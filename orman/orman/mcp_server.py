"""MCP (Model Context Protocol) server for orman.

Exposes the same admin operations available under /admin_*/ as MCP tools,
over a stateless JSON-RPC HTTP endpoint at /mcp/ (wired up in urls.py /
views.mcp_endpoint).

Auth: `Authorization: Bearer <token>` where token is a Person.mcp_token.
Every tool call re-checks person.is_admin at call time — nothing about
admin access is baked into the token itself, so revoking admin (or
regenerating the token, see views.regenerate_mcp_token) cuts off
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
    my_rehearsal_rsvps = dict(
        models.RSVP.objects.filter(person=person, rehearsal__in=rehearsals)
        .values_list("rehearsal_id", "status")
    )
    my_performance_rsvps = dict(
        models.RSVP.objects.filter(person=person, performance__in=performances)
        .values_list("performance_id", "status")
    )

    rehearsal_rows = []
    for r in rehearsals:
        row = _serialize(r, ["name", "startDate", "startTime", "endTime", "venue"])
        row["my_rsvp"] = my_rehearsal_rsvps.get(r.pk)
        rehearsal_rows.append(row)

    performance_rows = []
    for p in performances:
        row = _serialize(p, ["name", "date", "time", "venue", "published", "publicDescription"])
        row["my_rsvp"] = my_performance_rsvps.get(p.pk)
        performance_rows.append(row)

    return {"rehearsals": rehearsal_rows, "performances": performance_rows}


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
    order_arg = args.get("order")
    start_time = _parse_time_arg(args.get("start_time"))
    item_id = args.get("item_id")
    if item_id:
        try:
            item = item_model.objects.get(pk=item_id, **{fk_name: event})
        except item_model.DoesNotExist:
            raise ToolError(f"No repertoire item with id {item_id} on this {args.get('event_type')}.")
        item.musicItem = music_item
        item.order = int(order_arg) if order_arg is not None else item.order
        item.start_time = start_time
        item.save()
    else:
        if order_arg is not None:
            order = int(order_arg)
        else:
            items_qs = item_model.objects.filter(**{fk_name: event})
            last = items_qs.order_by("order").last()
            order = (last.order + 1) if last else 1
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


# ── Member-scoped tools ──────────────────────────────────────────────────────
# These always act on the calling `person` — none of them accept a person_id —
# so they're safe to expose to every authenticated account, mirroring exactly
# what that member could already do on the member-facing pages.

def tool_whoami(args, person):
    return _serialize(person, forms.ProfileForm.base_fields.keys()) | {"member": person.member, "is_admin": person.is_admin}


def tool_update_my_profile(args, person):
    fields = {k: v for k, v in args.items() if k in forms.ProfileForm.base_fields}
    if "instrument_ids" in args:
        fields["instruments"] = args["instrument_ids"]
    if not fields:
        raise ToolError("Provide at least one of: firstNames, lastName, email, phone, instrument_ids.")
    data = _form_initial_data(person, Resource("person", models.Person, forms.ProfileForm, []))
    data.update(fields)
    form = forms.ProfileForm(data=data, instance=person)
    if not form.is_valid():
        raise ToolError(f"Validation failed: {form.errors.as_text()}")
    obj = form.save()
    return {"saved": True, **_serialize(obj, forms.ProfileForm.base_fields.keys())}


def tool_my_rsvps(args, person):
    qs = (
        models.RSVP.objects.filter(person=person)
        .select_related("rehearsal", "performance").prefetch_related("playing_instruments")
    )
    return {
        "rsvps": [
            {
                "id": r.pk,
                "event_type": "rehearsal" if r.rehearsal_id else "performance",
                "event_id": r.rehearsal_id or r.performance_id,
                "event": str(r.rehearsal or r.performance),
                "status": r.status, "note": r.note,
                "playing_instruments": [i.name for i in r.playing_instruments.all()],
            }
            for r in qs
        ],
    }


def tool_my_rsvp_set(args, person):
    event_type = args.get("event_type")
    if event_type not in _RSVP_EVENT_FIELDS:
        raise ToolError("event_type must be 'rehearsal' or 'performance'.")
    status = args.get("status")
    if status not in dict(models.RSVP.STATUS_CHOICES):
        valid = ", ".join(dict(models.RSVP.STATUS_CHOICES))
        raise ToolError(f"status must be one of: {valid}.")
    event_model = models.Rehearsal if event_type == "rehearsal" else models.Performance
    try:
        event = event_model.objects.get(pk=args.get("event_id"))
    except event_model.DoesNotExist:
        raise ToolError(f"No {event_type} with id {args.get('event_id')}.")

    rsvp_obj, _ = models.RSVP.objects.update_or_create(
        person=person, **{event_type: event},
        defaults={"status": status, "note": args.get("note", "")},
    )
    instrument_ids = args.get("playing_instrument_ids") or []
    valid_ids = list(person.instruments.filter(pk__in=instrument_ids).values_list("pk", flat=True))
    rsvp_obj.playing_instruments.set(valid_ids)
    return {"saved": True, "id": rsvp_obj.pk, "status": rsvp_obj.status}


def tool_music_library(args, person):
    my_instrument_ids = set(person.instruments.values_list("pk", flat=True))
    items = models.MusicItem.objects.prefetch_related("musicitempart_set__instruments").order_by("name")
    results = []
    for item in items:
        all_parts = list(item.musicitempart_set.all())
        my_parts, other_parts = [], []
        for p in all_parts:
            bucket = my_parts if (my_instrument_ids & {i.id for i in p.instruments.all()}) else other_parts
            bucket.append({
                "id": p.pk, "label": p.label, "is_score": p.is_score,
                "external_link": p.external_link, "has_file": bool(p.file),
                "file_url": p.file.url if p.file else None,
            })
        results.append({
            **_serialize(item, ["name", "composer", "duration", "notes"]),
            "my_parts": my_parts, "other_parts": other_parts,
        })
    return {"music_items": results}


def tool_polls(args, person):
    active_polls = list(
        models.Poll.objects.filter(active=True).prefetch_related("questions__choices")
    )
    all_questions = [q for poll in active_polls for q in poll.questions.all()]
    answers = (
        models.PollAnswer.objects.filter(person=person, question__in=all_questions)
        .select_related("choice")
    )
    answers_by_question = {}
    for a in answers:
        answers_by_question.setdefault(a.question_id, []).append(
            a.choice.text if a.choice else a.text_value
        )
    return {
        "polls": [
            {
                "id": poll.pk, "title": poll.title, "description": poll.description,
                "questions": [
                    {
                        "id": q.pk, "text": q.text, "kind": q.kind,
                        "choices": [{"id": c.pk, "text": c.text} for c in q.choices.all()],
                        "my_answers": answers_by_question.get(q.id, []),
                    }
                    for q in poll.questions.all()
                ],
            }
            for poll in active_polls
        ],
    }


def tool_answer_poll(args, person):
    try:
        question = models.PollQuestion.objects.get(pk=args.get("question_id"), poll__active=True)
    except models.PollQuestion.DoesNotExist:
        raise ToolError(f"No active poll question with id {args.get('question_id')}.")

    if question.kind == models.PollQuestion.TYPE_FREE_TEXT:
        text = (args.get("text") or "").strip()
        if not text:
            raise ToolError("This question needs a 'text' answer.")
        models.PollAnswer.objects.update_or_create(
            question=question, person=person, choice=None, defaults={"text_value": text},
        )
    elif question.kind == models.PollQuestion.TYPE_SINGLE:
        try:
            choice = models.PollChoice.objects.get(pk=args.get("choice_id"), question=question)
        except models.PollChoice.DoesNotExist:
            raise ToolError("Provide a valid 'choice_id' for this question.")
        models.PollAnswer.objects.filter(question=question, person=person).delete()
        models.PollAnswer.objects.create(question=question, person=person, choice=choice)
    elif question.kind == models.PollQuestion.TYPE_MULTI:
        choice_ids = args.get("choice_ids") or []
        valid_ids = set(question.choices.filter(pk__in=choice_ids).values_list("pk", flat=True))
        if not valid_ids:
            raise ToolError("Provide at least one valid id in 'choice_ids' for this question.")
        models.PollAnswer.objects.filter(question=question, person=person).delete()
        models.PollAnswer.objects.bulk_create([
            models.PollAnswer(question=question, person=person, choice_id=cid) for cid in valid_ids
        ])
    else:  # pragma: no cover — defensive, kind is a closed choice set
        raise ToolError(f"Unknown question kind '{question.kind}'.")
    return {"saved": True, "question_id": question.pk}


def tool_announcements(args, person):
    """Mirrors context_processors.announcements() — active, in-window, and
    not already dismissed by this person (same as the banners shown site-wide)."""
    from django.utils import timezone
    now = timezone.now()
    dismissed_ids = set(
        models.AnnouncementDismissal.objects.filter(person=person).values_list("announcement_id", flat=True)
    )
    visible = [
        a for a in models.Announcement.objects.filter(active=True)
        if a.is_visible_now(now) and a.pk not in dismissed_ids
    ]
    return {
        "announcements": [
            {"id": a.pk, "title": a.title, "body": a.body, "severity": a.severity, "dismissible": a.dismissible}
            for a in visible
        ],
    }


def tool_dismiss_announcement(args, person):
    try:
        announcement = models.Announcement.objects.get(pk=args.get("announcement_id"))
    except models.Announcement.DoesNotExist:
        raise ToolError(f"No announcement with id {args.get('announcement_id')}.")
    models.AnnouncementDismissal.objects.get_or_create(announcement=announcement, person=person)
    return {"saved": True, "announcement_id": announcement.pk}


# ── Tool registry ────────────────────────────────────────────────────────────

class ToolSpec:
    def __init__(self, name, description, input_schema, handler, *,
                 admin_only=True, read_only=False, destructive=False, idempotent=False):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler
        self.admin_only = admin_only
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
        tool_list_resources, admin_only=True, read_only=True, idempotent=True,
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
        tool_list, admin_only=True, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_get",
        "Get the full detail of a single record by id.",
        {
            "type": "object",
            "properties": {"resource": {"type": "string", "enum": _RESOURCE_ENUM}, "id": _INT},
            "required": ["resource", "id"],
        },
        tool_get, admin_only=True, read_only=True, idempotent=True,
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
        tool_create, admin_only=True, idempotent=False,
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
        tool_update, admin_only=True, idempotent=True,
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
        tool_delete, admin_only=True, destructive=True, idempotent=True,
    ),
    ToolSpec(
        "orman_upcoming_events",
        "List upcoming rehearsals and performances within the next N days (default 90), "
        "with the caller's own RSVP status attached to each.",
        {"type": "object", "properties": {"days": {**_INT, "default": 90, "maximum": 365}}},
        tool_upcoming_events, admin_only=False, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_music_parts_list",
        "List the score and instrument parts uploaded for a music item.",
        {
            "type": "object",
            "properties": {"music_item_id": _INT},
            "required": ["music_item_id"],
        },
        tool_music_parts_list, admin_only=True, read_only=True, idempotent=True,
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
        tool_music_parts_set, admin_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_music_parts_delete",
        "Delete a music part (and its uploaded file, if any).",
        {
            "type": "object",
            "properties": {"music_item_id": _INT, "part_id": _INT},
            "required": ["music_item_id", "part_id"],
        },
        tool_music_parts_delete, admin_only=True, destructive=True, idempotent=True,
    ),
    ToolSpec(
        "orman_repertoire_list",
        "List the programme (ordered music items) for a rehearsal or performance.",
        {
            "type": "object",
            "properties": {"event_type": _EVENT_TYPE, "event_id": _INT},
            "required": ["event_type", "event_id"],
        },
        tool_repertoire_list, admin_only=True, read_only=True, idempotent=True,
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
                "order": {
                    **_INT,
                    "description": "Position in the programme. Omit to append after the last "
                    "existing line (or to leave an existing line's position unchanged).",
                },
                "start_time": {**_STRING, "description": "HH:MM, optional."},
            },
            "required": ["event_type", "event_id", "music_item_id"],
        },
        tool_repertoire_set, admin_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_repertoire_delete",
        "Remove one programme line from a rehearsal or performance.",
        {
            "type": "object",
            "properties": {"event_type": _EVENT_TYPE, "event_id": _INT, "item_id": _INT},
            "required": ["event_type", "event_id", "item_id"],
        },
        tool_repertoire_delete, admin_only=True, destructive=True, idempotent=True,
    ),
    ToolSpec(
        "orman_rsvp_list",
        "List everyone's RSVP status for a rehearsal or performance.",
        {
            "type": "object",
            "properties": {"event_type": _EVENT_TYPE, "event_id": _INT},
            "required": ["event_type", "event_id"],
        },
        tool_rsvp_list, admin_only=True, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_rsvp_set",
        "Set (or change) a specific person's RSVP for a rehearsal or performance — for "
        "yourself, use orman_my_rsvp_set instead. playing_instrument_ids is filtered to "
        "instruments that person actually plays.",
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
        tool_rsvp_set, admin_only=True, idempotent=True,
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
        tool_set_person_password, admin_only=True, destructive=True, idempotent=True,
    ),

    # ── Member-scoped tools — always act as the calling person, never take a
    # person_id, and are available to every authenticated account. ─────────
    ToolSpec(
        "orman_whoami",
        "Get your own profile: name, email, phone, instruments, and whether you're an admin.",
        {"type": "object", "properties": {}},
        tool_whoami, admin_only=False, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_update_my_profile",
        "Update your own profile (name, email, phone, instruments you play).",
        {
            "type": "object",
            "properties": {
                "firstNames": _STRING, "lastName": _STRING, "email": _STRING,
                "phone": _STRING, "instrument_ids": _INT_ARRAY,
            },
        },
        tool_update_my_profile, admin_only=False, idempotent=True,
    ),
    ToolSpec(
        "orman_my_rsvps",
        "List your own RSVPs (past and upcoming) for rehearsals and performances.",
        {"type": "object", "properties": {}},
        tool_my_rsvps, admin_only=False, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_my_rsvp_set",
        "Set (or change) your own RSVP for a rehearsal or performance. "
        "playing_instrument_ids is filtered to instruments you actually play.",
        {
            "type": "object",
            "properties": {
                "event_type": _EVENT_TYPE, "event_id": _INT,
                "status": {"type": "string", "enum": [c[0] for c in models.RSVP.STATUS_CHOICES]},
                "note": _STRING,
                "playing_instrument_ids": _INT_ARRAY,
            },
            "required": ["event_type", "event_id", "status"],
        },
        tool_my_rsvp_set, admin_only=False, idempotent=True,
    ),
    ToolSpec(
        "orman_music_library",
        "List the music library. Each item's parts are split into ones matching your own "
        "instruments (my_parts) and everything else, including the full score (other_parts).",
        {"type": "object", "properties": {}},
        tool_music_library, admin_only=False, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_polls",
        "List active polls, their questions/choices, and any answers you've already given.",
        {"type": "object", "properties": {}},
        tool_polls, admin_only=False, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_answer_poll",
        "Answer one poll question as yourself. For a free_text question pass 'text'; for "
        "single_choice pass 'choice_id'; for multi_choice pass 'choice_ids' (replaces your "
        "previous answer to that question).",
        {
            "type": "object",
            "properties": {
                "question_id": _INT, "text": _STRING, "choice_id": _INT, "choice_ids": _INT_ARRAY,
            },
            "required": ["question_id"],
        },
        tool_answer_poll, admin_only=False, idempotent=True,
    ),
    ToolSpec(
        "orman_announcements",
        "List active site announcements currently visible to you (already-dismissed ones "
        "are left out, same as the site's banner).",
        {"type": "object", "properties": {}},
        tool_announcements, admin_only=False, read_only=True, idempotent=True,
    ),
    ToolSpec(
        "orman_dismiss_announcement",
        "Dismiss an announcement banner for yourself.",
        {"type": "object", "properties": {"announcement_id": _INT}, "required": ["announcement_id"]},
        tool_dismiss_announcement, admin_only=False, idempotent=True,
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
                (
                    "Full admin API for the Orman orchestra management system. Start with "
                    "orman_list_resources to see what's available, then orman_list/orman_get "
                    "to explore data before creating or changing anything."
                ) if person.is_admin else (
                    "Member API for the Orman orchestra management system — lets you view "
                    "upcoming events, the music library, and polls, and manage your own "
                    "profile, RSVPs, poll answers, and announcements. Call tools/list to "
                    "see what's available."
                )
            ),
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [t.to_mcp() for t in _tools_for(person)]}
    if method == "tools/call":
        return _call_tool(params, person)
    raise JsonRpcError(METHOD_NOT_FOUND, f"Unknown method '{method}'.")


def _tools_for(person):
    """Admins see every tool; everyone else only sees the member-scoped ones
    (which always act as the caller, never accept a person_id)."""
    if person.is_admin:
        return TOOLS
    return [t for t in TOOLS if not t.admin_only]


def _call_tool(params: dict, person):
    name = params.get("name")
    tool = TOOLS_BY_NAME.get(name)
    if tool is None or (tool.admin_only and not person.is_admin):
        # Same error either way — a non-admin shouldn't be able to tell an
        # admin-only tool apart from one that plain doesn't exist.
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
