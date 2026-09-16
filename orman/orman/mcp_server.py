"""MCP (Model Context Protocol) server for orman.

Exposes the same admin operations available under /admin_*/ as MCP tools,
over a stateless JSON-RPC HTTP endpoint at /mcp/ (wired up in urls.py /
views.mcp_endpoint).

Auth: `Authorization: Bearer <token>` where token is a Person.mcp_token.
Every tool call re-checks person.is_admin at call time — nothing about
admin access is baked into the token itself, so revoking admin (or
regenerating the token, see views.admin_regenerate_mcp_token) cuts off
access immediately.

Transport: MCP "Streamable HTTP" in stateless JSON mode (protocol version
2025-06-18) — one JSON-RPC request per POST, one JSON response per request,
no SSE, no session id. This mirrors the reference Python SDK's
`is_json_response_enabled=True` mode (see mcp.server.streamable_http), but
is hand-rolled here rather than using that SDK's HTTP server because it's
ASGI/Starlette-based and this project runs on WSGI (gunicorn).

Known, deliberate limitations:
- No file uploads. MCP tool arguments are JSON, so music parts can only be
  linked via `external_link` through this API — use the web UI to upload
  an actual file.
- No OAuth. A single long-lived bearer token per admin, the same trust
  model as the existing `calendar_token` field already used in this app.
- MailingList and SocialApp are intentionally excluded from the resource
  registry below: both carry credentials (API keys / OAuth client
  secrets / service-account JSON) that shouldn't be readable through an
  LLM-facing tool call.
"""
from __future__ import annotations

import datetime
import decimal

from django.db.models import Model as DjangoModel
from django.db.models import ProtectedError
from django.utils.dateparse import parse_time

from . import forms, models

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "orman"
SERVER_VERSION = "1.0.0"

# Standard JSON-RPC 2.0 error codes (transport/protocol-level failures only —
# tool-level failures are reported as a successful response with isError).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class JsonRpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(Exception):
    """An expected, user-facing tool failure — reported to the client inside
    a successful CallToolResult (isError: true) so the calling LLM can see
    it and self-correct, per MCP convention."""


# ── Value serialization ─────────────────────────────────────────────────────

def _serialize_value(value):
    if value is None:
        return None
    if isinstance(value, DjangoModel):
        return {"id": value.pk, "label": str(value)}
    if hasattr(value, "all") and hasattr(value, "model"):  # M2M/FK related manager
        return [{"id": v.pk, "label": str(v)} for v in value.all()]
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return str(value)
    if hasattr(value, "url"):  # FieldFile
        return value.url if value else None
    return value


def _serialize(obj, field_names):
    data = {"id": obj.pk, "display": str(obj)}
    for name in field_names:
        if name == "id":
            continue
        try:
            data[name] = _serialize_value(getattr(obj, name))
        except Exception:
            data[name] = None
    return data


def _parse_time_arg(value):
    if not value:
        return None
    if isinstance(value, str):
        parsed = parse_time(value)
        if parsed is None:
            raise ToolError(f"Invalid time '{value}' — expected HH:MM or HH:MM:SS.")
        return parsed
    return value


# ── Resource registry (generic CRUD over the app's existing admin forms) ────

class Resource:
    def __init__(self, name, model, form_class, summary_fields):
        self.name = name
        self.model = model
        self.form_class = form_class
        self.summary_fields = summary_fields

    @property
    def all_fields(self):
        return list(self.form_class.base_fields.keys())


RESOURCES: dict[str, Resource] = {}


def _register(name, model, form_class, summary_fields):
    RESOURCES[name] = Resource(name, model, form_class, summary_fields)


_register("venue", models.Venue, forms.VenueForm,
           ["name", "address", "phone", "email"])
_register("rental_contract", models.RentalContract, forms.RentalContractForm,
           ["description", "supplier", "cost", "startDate", "endDate"])
_register("instrument_family", models.InstrumentFamily, forms.InstrumentFamilyForm,
           ["name"])
_register("instrument", models.Instrument, forms.InstrumentForm,
           ["name", "family"])
_register("music_item", models.MusicItem, forms.MusicItemForm,
           ["name", "composer", "duration"])
_register("rehearsal_series", models.RehearsalSeries, forms.RehearsalSeriesForm,
           ["name", "frequency", "day_of_week", "venue", "series_start", "series_end"])
_register("rehearsal", models.Rehearsal, forms.RehearsalForm,
           ["name", "startDate", "startTime", "venue"])
_register("performance", models.Performance, forms.PerformanceForm,
           ["name", "date", "time", "venue", "published"])
_register("announcement", models.Announcement, forms.AnnouncementForm,
           ["title", "severity", "active", "visibleFrom", "visibleUntil"])
_register("poll", models.Poll, forms.PollForm,
           ["title", "active", "show_as_banner"])
_register("person", models.Person, forms.PersonForm,
           ["firstNames", "lastName", "email", "member", "is_admin"])


def _get_resource(name) -> Resource:
    try:
        return RESOURCES[name]
    except KeyError:
        raise ToolError(f"Unknown resource '{name}'. Valid resources: {', '.join(sorted(RESOURCES))}")


def _get_instance(resource: Resource, pk):
    try:
        return resource.model.objects.get(pk=pk)
    except resource.model.DoesNotExist:
        raise ToolError(f"No {resource.name} with id {pk}.")


def _form_initial_data(obj, resource):
    """Current field values, shaped the way ModelForm.data expects them —
    used so a partial update can still pass full-form validation."""
    data = {}
    for name in resource.form_class.base_fields:
        value = getattr(obj, name, None)
        if hasattr(value, "all") and hasattr(value, "model"):
            value = list(value.values_list("pk", flat=True))
        elif isinstance(value, DjangoModel):
            value = value.pk
        data[name] = value
    return data


def tool_list_resources(args, person):
    return {
        "resources": [
            {"resource": r.name, "fields": r.all_fields, "summary_fields": r.summary_fields}
            for r in RESOURCES.values()
        ],
    }


def tool_list(args, person):
    resource = _get_resource(args.get("resource"))
    qs = resource.model.objects.all()
    filters = args.get("filters") or {}
    if filters:
        try:
            qs = qs.filter(**filters)
        except Exception as e:
            raise ToolError(f"Invalid filter {filters!r}: {e}")
    total = qs.count()
    limit = max(1, min(int(args.get("limit", 50)), 200))
    offset = max(0, int(args.get("offset", 0)))
    rows = list(qs[offset:offset + limit])
    return {
        "resource": resource.name,
        "total": total,
        "offset": offset,
        "records": [_serialize(obj, resource.summary_fields) for obj in rows],
    }


def tool_get(args, person):
    resource = _get_resource(args.get("resource"))
    obj = _get_instance(resource, args.get("id"))
    return _serialize(obj, resource.all_fields)


def _create_person(fields):
    fields = dict(fields)
    password = fields.pop("password", None)
    if not password:
        raise ToolError(
            "Creating a person requires a 'password' field — they can sign in with it "
            "(or you can add a passkey / reset it later)."
        )
    form = forms.PersonForm(data=fields)
    if not form.is_valid():
        raise ToolError(f"Validation failed: {form.errors.as_text()}")
    obj = form.save(commit=False)
    obj.set_password(password)
    obj.save()
    form.save_m2m()
    return obj


def tool_create(args, person):
    resource = _get_resource(args.get("resource"))
    fields = args.get("fields") or {}
    if resource.name == "person":
        obj = _create_person(fields)
    else:
        form = resource.form_class(data=fields)
        if not form.is_valid():
            raise ToolError(f"Validation failed: {form.errors.as_text()}")
        obj = form.save()
    return {"created": True, **_serialize(obj, resource.all_fields)}


def tool_update(args, person):
    resource = _get_resource(args.get("resource"))
    obj = _get_instance(resource, args.get("id"))
    fields = args.get("fields") or {}
    if not fields:
        raise ToolError("Provide at least one field in 'fields' to update.")
    if resource.name == "person" and "password" in fields:
        raise ToolError("Use orman_set_person_password to change a password, not orman_update.")
    data = _form_initial_data(obj, resource)
    data.update(fields)
    form = resource.form_class(data=data, instance=obj)
    if not form.is_valid():
        raise ToolError(f"Validation failed: {form.errors.as_text()}")
    obj = form.save()
    return {"updated": True, **_serialize(obj, resource.all_fields)}


def tool_delete(args, person):
    resource = _get_resource(args.get("resource"))
    obj = _get_instance(resource, args.get("id"))
    label = str(obj)
    try:
        obj.delete()
    except ProtectedError as e:
        blockers = sorted({type(o).__name__ for o in e.protected_objects})
        raise ToolError(
            f"Cannot delete {resource.name} '{label}': still referenced by "
            f"{', '.join(blockers)}. Remove or reassign those first."
        )
    return {"deleted": True, "id": args.get("id"), "label": label}


# ── Specialized workflow tools ──────────────────────────────────────────────

def tool_upcoming_events(args, person):
    from django.utils import timezone
    today = timezone.localdate()
    days = max(1, min(int(args.get("days", 90)), 365))
    cutoff = today + datetime.timedelta(days=days)
    rehearsals = (
        models.Rehearsal.objects.filter(startDate__gte=today, startDate__lte=cutoff)
        .select_related("venue").order_by("startDate", "startTime")
    )
    performances = (
        models.Performance.objects.filter(date__gte=today, date__lte=cutoff)
        .select_related("venue").order_by("date", "time")
    )
    return {
        "rehearsals": [
            _serialize(r, ["name", "startDate", "startTime", "endTime", "venue"]) for r in rehearsals
        ],
        "performances": [
            _serialize(p, ["name", "date", "time", "venue", "published", "publicDescription"])
            for p in performances
        ],
    }


def tool_music_parts_list(args, person):
    item = _get_instance(_get_resource("music_item"), args.get("music_item_id"))
    parts = models.MusicItemPart.objects.filter(musicItem=item).prefetch_related("instruments")
    return {
        "music_item": {"id": item.pk, "name": item.name},
        "parts": [
            {
                "id": p.pk,
                "label": p.label,
                "is_score": p.is_score,
                "instrument_ids": [i.id for i in p.instruments.all()],
                "instruments": [i.name for i in p.instruments.all()],
                "external_link": p.external_link,
                "has_file": bool(p.file),
                "file_url": p.file.url if p.file else None,
            }
            for p in parts
        ],
    }


def tool_music_parts_set(args, person):
    item = _get_instance(_get_resource("music_item"), args.get("music_item_id"))
    is_score = bool(args.get("is_score", False))
    instrument_ids = args.get("instrument_ids") or []
    if not is_score and not instrument_ids:
        raise ToolError("Set is_score=true and/or provide at least one instrument_id.")
    instruments = models.Instrument.objects.filter(pk__in=instrument_ids)
    external_link = forms.normalise_url(args["external_link"]) if args.get("external_link") else ""
    part_id = args.get("part_id")
    if part_id:
        try:
            part = models.MusicItemPart.objects.get(pk=part_id, musicItem=item)
        except models.MusicItemPart.DoesNotExist:
            raise ToolError(f"No part with id {part_id} on music_item {item.pk}.")
        part.is_score = is_score
        if "external_link" in args:
            part.external_link = external_link
        part.save(update_fields=["is_score", "external_link"])
    else:
        part = models.MusicItemPart.objects.create(
            musicItem=item, is_score=is_score, external_link=external_link,
        )
    part.instruments.set(instruments)
    return {"saved": True, "id": part.pk, "label": part.label}


def tool_music_parts_delete(args, person):
    part_id, music_item_id = args.get("part_id"), args.get("music_item_id")
    try:
        part = models.MusicItemPart.objects.get(pk=part_id, musicItem_id=music_item_id)
    except models.MusicItemPart.DoesNotExist:
        raise ToolError(f"No part with id {part_id} on music_item {music_item_id}.")
    if part.file:
        part.file.delete(save=False)
    part.delete()
    return {"deleted": True, "id": part_id}


_REPERTOIRE_MODELS = {
    "rehearsal": (models.Rehearsal, models.RehearsalItem, "rehearsal"),
    "performance": (models.Performance, models.PerformanceItem, "performance"),
}


def _repertoire_models(event_type):
    try:
        return _REPERTOIRE_MODELS[event_type]
    except KeyError:
        raise ToolError("event_type must be 'rehearsal' or 'performance'.")


def tool_repertoire_list(args, person):
    event_model, item_model, fk_name = _repertoire_models(args.get("event_type"))
    try:
        event = event_model.objects.get(pk=args.get("event_id"))
    except event_model.DoesNotExist:
        raise ToolError(f"No {args.get('event_type')} with id {args.get('event_id')}.")
    items = item_model.objects.filter(**{fk_name: event}).select_related("musicItem").order_by("order")
    return {
        "event": {"id": event.pk, "name": event.name},
        "items": [
            {
                "id": i.pk, "order": i.order,
                "music_item_id": i.musicItem_id, "music_item": i.musicItem.name,
                "start_time": i.start_time.isoformat() if i.start_time else None,
            }
            for i in items
        ],
    }


def tool_repertoire_set(args, person):
    event_model, item_model, fk_name = _repertoire_models(args.get("event_type"))
    try:
        event = event_model.objects.get(pk=args.get("event_id"))
    except event_model.DoesNotExist:
        raise ToolError(f"No {args.get('event_type')} with id {args.get('event_id')}.")
    try:
        music_item = models.MusicItem.objects.get(pk=args.get("music_item_id"))
    except models.MusicItem.DoesNotExist:
        raise ToolError(f"No music_item with id {args.get('music_item_id')}.")
    order = int(args.get("order", 1))
    start_time = _parse_time_arg(args.get("start_time"))
    item_id = args.get("item_id")
    if item_id:
        try:
            item = item_model.objects.get(pk=item_id, **{fk_name: event})
        except item_model.DoesNotExist:
            raise ToolError(f"No repertoire item with id {item_id} on this {args.get('event_type')}.")
        item.musicItem, item.order, item.start_time = music_item, order, start_time
        item.save()
    else:
        item = item_model.objects.create(
            **{fk_name: event, "musicItem": music_item, "order": order, "start_time": start_time},
        )
    return {"saved": True, "id": item.pk}


def tool_repertoire_delete(args, person):
    event_model, item_model, fk_name = _repertoire_models(args.get("event_type"))
    try:
        item = item_model.objects.get(pk=args.get("item_id"), **{f"{fk_name}_id": args.get("event_id")})
    except item_model.DoesNotExist:
        raise ToolError("No matching repertoire item found.")
    item.delete()
    return {"deleted": True}


_RSVP_EVENT_FIELDS = {"rehearsal": "rehearsal", "performance": "performance"}


def tool_rsvp_list(args, person):
    event_type = args.get("event_type")
    if event_type not in _RSVP_EVENT_FIELDS:
        raise ToolError("event_type must be 'rehearsal' or 'performance'.")
    qs = (
        models.RSVP.objects.filter(**{f"{event_type}_id": args.get("event_id")})
        .select_related("person").prefetch_related("playing_instruments")
    )
    return {
        "rsvps": [
            {
                "id": r.pk, "person_id": r.person_id, "person": str(r.person),
                "status": r.status, "note": r.note,
                "playing_instruments": [i.name for i in r.playing_instruments.all()],
            }
            for r in qs
        ],
    }


def tool_rsvp_set(args, person):
    event_type = args.get("event_type")
    if event_type not in _RSVP_EVENT_FIELDS:
        raise ToolError("event_type must be 'rehearsal' or 'performance'.")
    status = args.get("status")
    if status not in dict(models.RSVP.STATUS_CHOICES):
        valid = ", ".join(dict(models.RSVP.STATUS_CHOICES))
        raise ToolError(f"status must be one of: {valid}.")
    try:
        target = models.Person.objects.get(pk=args.get("person_id"))
    except models.Person.DoesNotExist:
        raise ToolError(f"No person with id {args.get('person_id')}.")
    event_model = models.Rehearsal if event_type == "rehearsal" else models.Performance
    try:
        event = event_model.objects.get(pk=args.get("event_id"))
    except event_model.DoesNotExist:
        raise ToolError(f"No {event_type} with id {args.get('event_id')}.")

    # update_or_create's create path needs the actual instance, not a raw pk
    # (unlike .filter(), Model construction requires a real FK instance).
    rsvp_obj, _ = models.RSVP.objects.update_or_create(
        person=target, **{event_type: event},
        defaults={"status": status, "note": args.get("note", "")},
    )
    instrument_ids = args.get("playing_instrument_ids") or []
    valid_ids = list(target.instruments.filter(pk__in=instrument_ids).values_list("pk", flat=True))
    rsvp_obj.playing_instruments.set(valid_ids)
    return {"saved": True, "id": rsvp_obj.pk, "status": rsvp_obj.status}


def tool_set_person_password(args, person):
    try:
        target = models.Person.objects.get(pk=args.get("person_id"))
    except models.Person.DoesNotExist:
        raise ToolError(f"No person with id {args.get('person_id')}.")
    password = args.get("password")
    if not password:
        raise ToolError("Provide a 'password'.")
    form = forms.DaisySetPasswordForm(target, data={"new_password1": password, "new_password2": password})
    if not form.is_valid():
        raise ToolError(f"Validation failed: {form.errors.as_text()}")
    form.save()
    return {"saved": True, "person_id": target.pk}


# ── Tool registry ────────────────────────────────────────────────────────────

class ToolSpec:
    def __init__(self, name, description, input_schema, handler, read_only=False, destructive=False, idempotent=False):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler
        self.read_only = read_only
        self.destructive = destructive
        self.idempotent = idempotent

    def to_mcp(self):
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "idempotentHint": self.idempotent,
                "openWorldHint": False,
            },
        }


_RESOURCE_ENUM = sorted(RESOURCES)

_STRING = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_INT_ARRAY = {"type": "array", "items": _INT}
_FIELDS_OBJ = {
    "type": "object",
    "description": "Field values to set, keyed by field name. Call orman_list_resources first "
                   "to see the valid field names for each resource.",
}
_EVENT_TYPE = {"type": "string", "enum": ["rehearsal", "performance"]}

TOOLS = [
    ToolSpec(
        "orman_list_resources",
        "List every resource type this server manages (venues, performances, people, etc.), "
        "and which fields each one has. Call this first to discover the schema.",
        {"type": "object", "properties": {}},
        tool_list_resources, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_list",
        "List records of a resource, most recent/relevant fields only. Supports basic "
        "field-equality filters, e.g. filters={\"published\": true}.",
        {
            "type": "object",
            "properties": {
                "resource": {"type": "string", "enum": _RESOURCE_ENUM},
                "filters": {"type": "object", "description": "Exact-match field filters."},
                "limit": {**_INT, "default": 50, "maximum": 200},
                "offset": {**_INT, "default": 0},
            },
            "required": ["resource"],
        },
        tool_list, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_get",
        "Get the full detail of a single record by id.",
        {
            "type": "object",
            "properties": {"resource": {"type": "string", "enum": _RESOURCE_ENUM}, "id": _INT},
            "required": ["resource", "id"],
        },
        tool_get, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_create",
        "Create a new record. Creating a 'person' additionally requires a 'password' field "
        "in fields (not returned by orman_list_resources, since it's write-only).",
        {
            "type": "object",
            "properties": {"resource": {"type": "string", "enum": _RESOURCE_ENUM}, "fields": _FIELDS_OBJ},
            "required": ["resource", "fields"],
        },
        tool_create, idempotent=False,
    ),
    ToolSpec(
        "orman_update",
        "Update one or more fields on an existing record. To change a person's password, "
        "use orman_set_person_password instead.",
        {
            "type": "object",
            "properties": {
                "resource": {"type": "string", "enum": _RESOURCE_ENUM},
                "id": _INT, "fields": _FIELDS_OBJ,
            },
            "required": ["resource", "id", "fields"],
        },
        tool_update, idempotent=True,
    ),
    ToolSpec(
        "orman_delete",
        "Delete a record. Fails with an actionable error if other records still reference it "
        "(e.g. a venue used by a rehearsal) — reassign or delete those first.",
        {
            "type": "object",
            "properties": {"resource": {"type": "string", "enum": _RESOURCE_ENUM}, "id": _INT},
            "required": ["resource", "id"],
        },
        tool_delete, destructive=True, idempotent=True,
    ),
    ToolSpec(
        "orman_upcoming_events",
        "List upcoming rehearsals and performances within the next N days (default 90).",
        {"type": "object", "properties": {"days": {**_INT, "default": 90, "maximum": 365}}},
        tool_upcoming_events, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_music_parts_list",
        "List the score and instrument parts uploaded for a music item.",
        {
            "type": "object",
            "properties": {"music_item_id": _INT},
            "required": ["music_item_id"],
        },
        tool_music_parts_list, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_music_parts_set",
        "Create or update a music part's assignment and link. A single part can cover the full "
        "score and/or several instruments at once (e.g. is_score=true with instrument_ids for "
        "both Score and Percussion), so the same file/link never needs adding twice. Omit "
        "part_id to create a new part; pass it to edit an existing one. Note: this API can only "
        "set an external_link, not upload a file — use the web UI's parts editor for uploads.",
        {
            "type": "object",
            "properties": {
                "music_item_id": _INT,
                "part_id": {**_INT, "description": "Omit to create a new part."},
                "is_score": _BOOL,
                "instrument_ids": _INT_ARRAY,
                "external_link": _STRING,
            },
            "required": ["music_item_id"],
        },
        tool_music_parts_set, idempotent=True,
    ),
    ToolSpec(
        "orman_music_parts_delete",
        "Delete a music part (and its uploaded file, if any).",
        {
            "type": "object",
            "properties": {"music_item_id": _INT, "part_id": _INT},
            "required": ["music_item_id", "part_id"],
        },
        tool_music_parts_delete, destructive=True, idempotent=True,
    ),
    ToolSpec(
        "orman_repertoire_list",
        "List the programme (ordered music items) for a rehearsal or performance.",
        {
            "type": "object",
            "properties": {"event_type": _EVENT_TYPE, "event_id": _INT},
            "required": ["event_type", "event_id"],
        },
        tool_repertoire_list, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_repertoire_set",
        "Add or update one programme line for a rehearsal or performance. Omit item_id to add "
        "a new line; pass it to edit an existing one.",
        {
            "type": "object",
            "properties": {
                "event_type": _EVENT_TYPE, "event_id": _INT,
                "item_id": {**_INT, "description": "Omit to add a new line."},
                "music_item_id": _INT,
                "order": {**_INT, "default": 1},
                "start_time": {**_STRING, "description": "HH:MM, optional."},
            },
            "required": ["event_type", "event_id", "music_item_id"],
        },
        tool_repertoire_set, idempotent=True,
    ),
    ToolSpec(
        "orman_repertoire_delete",
        "Remove one programme line from a rehearsal or performance.",
        {
            "type": "object",
            "properties": {"event_type": _EVENT_TYPE, "event_id": _INT, "item_id": _INT},
            "required": ["event_type", "event_id", "item_id"],
        },
        tool_repertoire_delete, destructive=True, idempotent=True,
    ),
    ToolSpec(
        "orman_rsvp_list",
        "List everyone's RSVP status for a rehearsal or performance.",
        {
            "type": "object",
            "properties": {"event_type": _EVENT_TYPE, "event_id": _INT},
            "required": ["event_type", "event_id"],
        },
        tool_rsvp_list, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_rsvp_set",
        "Set (or change) a specific person's RSVP for a rehearsal or performance. "
        "playing_instrument_ids is filtered to instruments that person actually plays.",
        {
            "type": "object",
            "properties": {
                "event_type": _EVENT_TYPE, "event_id": _INT, "person_id": _INT,
                "status": {"type": "string", "enum": [c[0] for c in models.RSVP.STATUS_CHOICES]},
                "note": _STRING,
                "playing_instrument_ids": _INT_ARRAY,
            },
            "required": ["event_type", "event_id", "person_id", "status"],
        },
        tool_rsvp_set, idempotent=True,
    ),
    ToolSpec(
        "orman_set_person_password",
        "Set a person's password directly (e.g. for a new account). They can change it "
        "themselves afterwards from their profile.",
        {
            "type": "object",
            "properties": {"person_id": _INT, "password": _STRING},
            "required": ["person_id", "password"],
        },
        tool_set_person_password, destructive=True, idempotent=True,
    ),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


# ── JSON-RPC dispatch ────────────────────────────────────────────────────────

def handle_message(body: dict, person) -> dict | None:
    """Handle one JSON-RPC request/notification. Returns a JSON-RPC response
    dict, or None for a notification (no response expected)."""
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        raise JsonRpcError(INVALID_REQUEST, "Not a JSON-RPC 2.0 message.")

    method = body.get("method")
    msg_id = body.get("id")
    is_notification = "id" not in body

    if not isinstance(method, str):
        raise JsonRpcError(INVALID_REQUEST, "Missing or invalid 'method'.")

    if method == "notifications/initialized" or method.startswith("notifications/"):
        return None  # nothing to do; client just informing us

    result = _dispatch_method(method, body.get("params") or {}, person)

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _dispatch_method(method: str, params: dict, person):
    if method == "initialize":
        # Echo back whatever protocol version the client asked for, rather
        # than asserting our own fixed date: the tools/list + tools/call +
        # initialize wire shape we implement has stayed stable across MCP
        # spec revisions, so this maximises compatibility across client SDK
        # versions without us having to track every new version string.
        requested_version = params.get("protocolVersion")
        return {
            "protocolVersion": requested_version if isinstance(requested_version, str) and requested_version else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Admin API for the Orman orchestra management system. Start with "
                "orman_list_resources to see what's available, then orman_list/orman_get "
                "to explore data before creating or changing anything."
            ),
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [t.to_mcp() for t in TOOLS]}
    if method == "tools/call":
        return _call_tool(params, person)
    raise JsonRpcError(METHOD_NOT_FOUND, f"Unknown method '{method}'.")


def _call_tool(params: dict, person):
    name = params.get("name")
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise JsonRpcError(METHOD_NOT_FOUND, f"Unknown tool '{name}'.")
    arguments = params.get("arguments") or {}
    try:
        output = tool.handler(arguments, person)
    except ToolError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except (KeyError, TypeError, ValueError) as e:
        return {"content": [{"type": "text", "text": f"Invalid arguments: {e}"}], "isError": True}
    import json
    return {
        "content": [{"type": "text", "text": json.dumps(output, default=str)}],
        "structuredContent": output if isinstance(output, dict) else {"result": output},
        "isError": False,
    }
