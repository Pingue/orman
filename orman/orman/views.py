import json

from django.contrib import messages
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


def admin_required(view_func):
    """Require is_admin=True. Redirects anonymous users to login; 403 for logged-in non-admins."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_admin:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper

from django.conf import settings as _settings
from django.contrib.auth import login as _auth_login
from . import crud, forms, mcp_server, models

# ── iCal helpers ────────────────────────────────────────────────────────────

def _ical_escape(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _ical_fold(line: str) -> str:
    """Fold a content line at 75 characters (RFC 5545 §3.1)."""
    if len(line) <= 75:
        return line
    parts = []
    while len(line) > 75:
        parts.append(line[:75])
        line = " " + line[75:]
    parts.append(line)
    return "\r\n".join(parts)


def _ical_prop(name: str, value: str) -> str:
    return _ical_fold(f"{name}:{value}")


def _ical_date(d) -> str:
    return d.strftime("%Y%m%d")


def _ical_datetime(d, t) -> str:
    return d.strftime("%Y%m%d") + "T" + t.strftime("%H%M%S")


def _attendance_breakdown(rsvps):
    """Return dict keyed by instrument (or None) mapping to list of people.

    Uses each RSVP's playing_instruments if set, otherwise falls back to the
    person's general instruments, otherwise buckets under None.
    """
    from collections import defaultdict
    buckets = defaultdict(list)
    for rsvp in rsvps:
        playing = list(rsvp.playing_instruments.all())
        instruments = playing if playing else list(rsvp.person.instruments.all())
        if instruments:
            for inst in instruments:
                buckets[inst].append(rsvp.person)
        else:
            buckets[None].append(rsvp.person)
    return buckets


def _merged_attendance(yes_rsvps, maybe_rsvps):
    """Return sorted list of {instrument, going, maybe} for the attendance page."""
    going = _attendance_breakdown(yes_rsvps)
    maybe = _attendance_breakdown(maybe_rsvps)
    all_instruments = set(going) | set(maybe)
    rows = []
    for inst in sorted(all_instruments, key=lambda x: (x is None, str(x or ""))):
        rows.append({
            "instrument": inst,
            "going": going.get(inst, []),
            "maybe": maybe.get(inst, []),
        })
    return rows


def _passkey_rp_id(request):
    return request.get_host().split(":")[0]


def _passkey_origin(request):
    return f"{request.scheme}://{request.get_host()}"


def _parse_reg_credential(data):
    from webauthn.helpers.structs import (
        RegistrationCredential, AuthenticatorAttestationResponse,
        PublicKeyCredentialType,
    )
    from webauthn.helpers import base64url_to_bytes
    r = data["response"]
    return RegistrationCredential(
        id=data["id"],
        raw_id=base64url_to_bytes(data["rawId"]),
        response=AuthenticatorAttestationResponse(
            client_data_json=base64url_to_bytes(r["clientDataJSON"]),
            attestation_object=base64url_to_bytes(r["attestationObject"]),
        ),
        type=PublicKeyCredentialType.PUBLIC_KEY,
    )


def _parse_auth_credential(data):
    from webauthn.helpers.structs import (
        AuthenticationCredential, AuthenticatorAssertionResponse,
        PublicKeyCredentialType,
    )
    from webauthn.helpers import base64url_to_bytes
    r = data["response"]
    return AuthenticationCredential(
        id=data["id"],
        raw_id=base64url_to_bytes(data["rawId"]),
        response=AuthenticatorAssertionResponse(
            client_data_json=base64url_to_bytes(r["clientDataJSON"]),
            authenticator_data=base64url_to_bytes(r["authenticatorData"]),
            signature=base64url_to_bytes(r["signature"]),
            user_handle=base64url_to_bytes(r["userHandle"]) if r.get("userHandle") else None,
        ),
        type=PublicKeyCredentialType.PUBLIC_KEY,
    )


@login_required
def passkey_register_begin(request):
    """Return WebAuthn registration options as JSON."""
    import json
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria, ResidentKeyRequirement,
        UserVerificationRequirement, PublicKeyCredentialDescriptor,
    )
    from webauthn.helpers import bytes_to_base64url

    options = generate_registration_options(
        rp_id=_passkey_rp_id(request),
        rp_name=getattr(_settings, "SITE_NAME", "Orman"),
        user_id=str(request.user.pk).encode(),
        user_name=request.user.email,
        user_display_name=str(request.user),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=bytes(pk.credential_id))
            for pk in request.user.passkeys.all()
        ],
    )
    request.session["wn_reg_challenge"] = bytes_to_base64url(options.challenge)
    return JsonResponse(json.loads(options_to_json(options)))


@login_required
@require_POST
def passkey_register_complete(request):
    """Verify registration and store the new passkey."""
    import json
    from webauthn import verify_registration_response
    from webauthn.helpers import base64url_to_bytes

    challenge_b64 = request.session.pop("wn_reg_challenge", None)
    if not challenge_b64:
        return JsonResponse({"error": "Session expired — please try again"}, status=400)
    try:
        data = json.loads(request.body)
        verification = verify_registration_response(
            credential=_parse_reg_credential(data),
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=_passkey_rp_id(request),
            expected_origin=_passkey_origin(request),
            require_user_verification=False,
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    models.Passkey.objects.create(
        person=request.user,
        name=data.get("name") or "Passkey",
        credential_id=bytes(verification.credential_id),
        credential_public_key=bytes(verification.credential_public_key),
        sign_count=verification.sign_count,
    )
    return JsonResponse({"ok": True})


def passkey_auth_begin(request):
    """Return WebAuthn authentication options as JSON (discoverable-credential flow)."""
    import json
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement
    from webauthn.helpers import bytes_to_base64url

    options = generate_authentication_options(
        rp_id=_passkey_rp_id(request),
        user_verification=UserVerificationRequirement.PREFERRED,
        # No allowCredentials → browser prompts the user to pick a passkey.
    )
    request.session["wn_auth_challenge"] = bytes_to_base64url(options.challenge)
    return JsonResponse(json.loads(options_to_json(options)))


@require_POST
def passkey_auth_complete(request):
    """Verify authentication assertion, update sign-count, and log the user in."""
    import json
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes

    challenge_b64 = request.session.pop("wn_auth_challenge", None)
    if not challenge_b64:
        return JsonResponse({"error": "Session expired"}, status=400)

    try:
        data = json.loads(request.body)
        credential = _parse_auth_credential(data)
    except Exception as exc:
        return JsonResponse({"error": f"Invalid credential: {exc}"}, status=400)

    cred_id_bytes = base64url_to_bytes(data["rawId"])
    passkey = (models.Passkey.objects
               .filter(credential_id=cred_id_bytes)
               .select_related("person")
               .first())
    if not passkey:
        return JsonResponse({"error": "Passkey not found"}, status=400)

    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=_passkey_rp_id(request),
            expected_origin=_passkey_origin(request),
            credential_public_key=bytes(passkey.credential_public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=False,
        )
    except Exception as exc:
        return JsonResponse({"error": f"Verification failed: {exc}"}, status=400)

    passkey.sign_count = verification.new_sign_count
    passkey.last_used_at = timezone.now()
    passkey.save(update_fields=["sign_count", "last_used_at"])

    _auth_login(request, passkey.person, backend="django.contrib.auth.backends.ModelBackend")
    return JsonResponse({"ok": True, "next": "/"})


@login_required
@require_POST
def passkey_delete(request, passkey_id):
    """Remove a passkey owned by the current user."""
    passkey = get_object_or_404(models.Passkey, pk=passkey_id, person=request.user)
    passkey.delete()
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def mcp_endpoint(request):
    """MCP (Model Context Protocol) JSON-RPC endpoint — see mcp_server.py.

    Authenticated by `Authorization: Bearer <mcp_token>` rather than a
    session, so it's CSRF-exempt like any other bearer-token API (there's
    no cookie for a third-party site to ride along with).
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse(
            {"error": "Missing or invalid Authorization header. Expected 'Bearer <token>'."},
            status=401, headers={"WWW-Authenticate": 'Bearer realm="orman-mcp"'},
        )
    token = auth[len("Bearer "):].strip()
    try:
        person = models.Person.objects.get(mcp_token=token, is_active=True)
    except (models.Person.DoesNotExist, ValueError, ValidationError):
        return JsonResponse(
            {"error": "Invalid or revoked token."},
            status=401, headers={"WWW-Authenticate": 'Bearer realm="orman-mcp"'},
        )
    if not person.is_admin:
        return JsonResponse({"error": "This account no longer has admin access."}, status=403)

    accept = request.headers.get("Accept", "")
    if accept and "application/json" not in accept and "*/*" not in accept:
        return JsonResponse({"error": "Client must accept application/json."}, status=406)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": mcp_server.PARSE_ERROR, "message": "Invalid JSON."}},
            status=400,
        )

    try:
        response = mcp_server.handle_message(body, person)
    except mcp_server.JsonRpcError as e:
        msg_id = body.get("id") if isinstance(body, dict) else None
        return JsonResponse(
            {"jsonrpc": "2.0", "id": msg_id, "error": {"code": e.code, "message": e.message}},
            status=400,
        )
    except Exception as e:
        msg_id = body.get("id") if isinstance(body, dict) else None
        return JsonResponse(
            {"jsonrpc": "2.0", "id": msg_id,
             "error": {"code": mcp_server.INTERNAL_ERROR, "message": f"Internal error: {e}"}},
            status=500,
        )

    if response is None:
        return HttpResponse(status=202)  # notification — no reply body
    return JsonResponse(response)


def calendar_ics(request, token):
    """Return an iCal feed for all rehearsals and published performances.

    The UUID token in the URL acts as authentication — no session required,
    so calendar apps can subscribe directly.
    """
    from datetime import datetime as _dt

    get_object_or_404(models.Person, calendar_token=token, is_active=True)

    now_stamp = _dt.utcnow().strftime("%Y%m%dT%H%M%SZ")
    site = getattr(_settings, "SITE_NAME", "Orman")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Orman//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _ical_prop("X-WR-CALNAME", _ical_escape(site) + " Calendar"),
        _ical_prop("X-WR-CALDESC", "Rehearsals and performances"),
    ]

    for r in models.Rehearsal.objects.select_related("venue").order_by("startDate"):
        lines += [
            "BEGIN:VEVENT",
            _ical_prop("UID", f"rehearsal-{r.id}@orman"),
            _ical_prop("DTSTAMP", now_stamp),
            _ical_prop("SUMMARY", _ical_escape(r.name)),
        ]
        if r.startTime:
            lines.append(_ical_prop("DTSTART", _ical_datetime(r.startDate, r.startTime)))
            if r.endTime:
                lines.append(_ical_prop("DTEND", _ical_datetime(r.startDate, r.endTime)))
            else:
                lines.append("DURATION:PT2H")
        else:
            lines.append(_ical_prop("DTSTART;VALUE=DATE", _ical_date(r.startDate)))
        if r.venue:
            lines.append(_ical_prop("LOCATION", _ical_escape(r.venue.name)))
        lines.append("END:VEVENT")

    for p in (models.Performance.objects.filter(published=True)
              .select_related("venue").order_by("date")):
        lines += [
            "BEGIN:VEVENT",
            _ical_prop("UID", f"performance-{p.id}@orman"),
            _ical_prop("DTSTAMP", now_stamp),
            _ical_prop("SUMMARY", _ical_escape(p.name)),
        ]
        if p.time:
            lines.append(_ical_prop("DTSTART", _ical_datetime(p.date, p.time)))
            lines.append("DURATION:PT2H")
        else:
            lines.append(_ical_prop("DTSTART;VALUE=DATE", _ical_date(p.date)))
        if p.venue:
            lines.append(_ical_prop("LOCATION", _ical_escape(p.venue.name)))
        if p.publicDescription:
            lines.append(_ical_prop("DESCRIPTION", _ical_escape(p.publicDescription)))
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    content = "\r\n".join(lines) + "\r\n"
    return HttpResponse(content, content_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="orman.ics"'})


def index(request):
    today = timezone.localdate()

    if not request.user.is_authenticated:
        # Public landing page: only ever shows performances the admin has
        # explicitly published, and never privateDescription.
        next_performance = (
            models.Performance.objects
            .filter(date__gte=today, published=True)
            .order_by("date", "time")
            .first()
        )
        return render(request, "index.html", {
            "next_performance": next_performance,
            "site_content": models.SiteContent.load(),
            "carousel_images": list(models.CarouselImage.objects.all()),
        })

    next_rehearsal = (
        models.Rehearsal.objects
        .filter(startDate__gte=today)
        .order_by("startDate", "startTime")
        .first()
    )
    next_performance = (
        models.Performance.objects
        .filter(date__gte=today)
        .order_by("date", "time")
        .first()
    )
    return render(request, "index.html", {
        "next_rehearsal": next_rehearsal,
        "next_performance": next_performance,
        "person": request.user,
    })


@login_required
def rehearsal_detail(request, rehearsal_id):
    rehearsal = get_object_or_404(models.Rehearsal, pk=rehearsal_id)
    items = rehearsal.rehearsalitem_set.select_related("musicItem").order_by("order")
    rsvp = rehearsal.rsvps.filter(person=request.user).first()
    return render(request, "rehearsal_detail.html", {
        "rehearsal": rehearsal,
        "items": items,
        "rsvp": rsvp,
    })


@login_required
def performance_detail(request, performance_id):
    performance = get_object_or_404(models.Performance, pk=performance_id)
    items = performance.performanceitem_set.select_related("musicItem").order_by("order")
    rsvp = performance.rsvps.filter(person=request.user).first()
    return render(request, "performance_detail.html", {
        "performance": performance,
        "items": items,
        "rsvp": rsvp,
    })


@login_required
def profile(request):
    if request.method == "POST":
        form = forms.ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated.")
            return redirect("profile")
    else:
        form = forms.ProfileForm(instance=request.user)
    return render(request, "profile.html", {"person": request.user, "form": form})


@admin_required
@require_POST
def admin_regenerate_mcp_token(request):
    """Rotate the current user's MCP API token, invalidating the old one."""
    import uuid as _uuid
    request.user.mcp_token = _uuid.uuid4()
    request.user.save(update_fields=["mcp_token"])
    messages.success(request, "MCP token regenerated. The old token no longer works.")
    return redirect("profile")


@login_required
def events(request):
    """All upcoming rehearsals and performances, in date order.

    Decorates each event with `my_rsvp` (the current user's RSVP, or None) so
    the template can render the right active button without an extra query.
    """
    from django.db.models import Prefetch
    today = timezone.localdate()
    rehearsals = list(
        models.Rehearsal.objects
        .filter(startDate__gte=today)
        .order_by("startDate", "startTime")
        .prefetch_related(
            Prefetch(
                "rehearsalitem_set",
                queryset=models.RehearsalItem.objects.select_related("musicItem").order_by("order"),
                to_attr="ordered_items",
            )
        )
    )
    performances = list(
        models.Performance.objects
        .filter(date__gte=today)
        .order_by("date", "time")
        .prefetch_related(
            Prefetch(
                "performanceitem_set",
                queryset=models.PerformanceItem.objects.select_related("musicItem").order_by("order"),
                to_attr="ordered_items",
            )
        )
    )
    person = request.user
    rh_rsvps = {
        r.rehearsal_id: r for r in
        models.RSVP.objects.filter(person=person, rehearsal__in=rehearsals)
        .prefetch_related("playing_instruments")
    }
    pf_rsvps = {
        r.performance_id: r for r in
        models.RSVP.objects.filter(person=person, performance__in=performances)
        .prefetch_related("playing_instruments")
    }
    for r in rehearsals:
        r.my_rsvp = rh_rsvps.get(r.id)
    for p in performances:
        p.my_rsvp = pf_rsvps.get(p.id)
    my_instruments = list(person.instruments.order_by("name"))
    return render(request, "events.html", {
        "rehearsals": rehearsals,
        "performances": performances,
        "person": person,
        "rsvp_choices": models.RSVP.STATUS_CHOICES,
        "my_instruments": my_instruments,
    })


@login_required
@require_POST
def rsvp(request, kind, id):
    """Set the current user's RSVP for a rehearsal or performance.

    POST body must include `status` (yes/no/maybe). Optionally `note`.
    """
    person = request.user
    status = request.POST.get("status", "")
    valid_statuses = {c[0] for c in models.RSVP.STATUS_CHOICES}
    if status not in valid_statuses:
        return HttpResponseBadRequest("Invalid RSVP status")

    note = request.POST.get("note", "")

    instrument_ids = request.POST.getlist("playing_instruments")

    if kind == "rehearsal":
        event = get_object_or_404(models.Rehearsal, pk=id)
        rsvp_obj, _ = models.RSVP.objects.update_or_create(
            person=person, rehearsal=event,
            defaults={"status": status, "note": note},
        )
    elif kind == "performance":
        event = get_object_or_404(models.Performance, pk=id)
        rsvp_obj, _ = models.RSVP.objects.update_or_create(
            person=person, performance=event,
            defaults={"status": status, "note": note},
        )
    else:
        return HttpResponseBadRequest("Unknown event kind")

    valid_ids = list(person.instruments.filter(pk__in=instrument_ids).values_list("pk", flat=True))
    rsvp_obj.playing_instruments.set(valid_ids)

    return redirect("events")


@login_required
def music(request):
    """All MusicItems in the library, ordered by name. Includes parts split by the user's instruments.

    A part can be assigned to the full score and/or several instruments (e.g.
    one file covering both Score and Percussion), so "my parts" is anything
    whose instrument set overlaps the member's own instruments — a
    score-only part (no instruments) never matches and stays under "other".
    """
    from django.db.models import Prefetch
    items = list(models.MusicItem.objects.prefetch_related(
        Prefetch(
            "musicitempart_set",
            queryset=models.MusicItemPart.objects.prefetch_related("instruments"),
        )
    ).order_by("name"))
    my_instrument_ids = set(request.user.instruments.values_list("pk", flat=True))
    # Annotate each item with my_parts / other_parts so the template stays simple
    for item in items:
        all_parts = list(item.musicitempart_set.all())
        item.my_parts = [
            p for p in all_parts
            if my_instrument_ids & {i.id for i in p.instruments.all()}
        ]
        item.other_parts = [p for p in all_parts if p not in item.my_parts]
    return render(request, "music.html", {"items": items})


@login_required
@require_POST
def dismiss_announcement(request, id):
    announcement = get_object_or_404(models.Announcement, pk=id)
    models.AnnouncementDismissal.objects.get_or_create(
        announcement=announcement, person=request.user,
    )
    return HttpResponse(status=204)


# ---------------------------------------------------------------------------
# InstrumentFamily — kept on the original inline-edit pattern for now (the JS
# in admin_instrument_family.html drives single-field rows directly).
# ---------------------------------------------------------------------------
@admin_required
def admin_instrument_family(request, id=None):
    if request.method == "POST":
        try:
            name = request.POST.get("name", "").strip()
            if not name:
                return HttpResponseBadRequest("Name is required")
            if id == 0 or id is None:
                family = models.InstrumentFamily(name=name)
            else:
                family = get_object_or_404(models.InstrumentFamily, pk=id)
                family.name = name
            family.save()
            return HttpResponse(str(family.id))
        except Exception as e:
            return HttpResponseBadRequest(str(e))
    elif request.method == "DELETE":
        family = get_object_or_404(models.InstrumentFamily, pk=id)
        try:
            family.delete()
            return HttpResponse("OK")
        except Exception as e:
            return HttpResponseBadRequest(str(e))
    families = models.InstrumentFamily.objects.all().order_by("name")
    return render(request, 'admin_instrument_family.html', {"families": families})


@admin_required
def admin_instrument_family_members(request, family_id):
    family = get_object_or_404(models.InstrumentFamily, pk=family_id)
    people = (
        models.Person.objects
        .filter(instruments__family=family)
        .prefetch_related("instruments")
        .distinct()
        .order_by("lastName", "firstNames")
    )
    rows = []
    for person in people:
        playing = [i for i in person.instruments.all() if i.family_id == family_id]
        rows.append({"person": person, "instruments": playing})
    return render(request, "admin_instrument_members.html", {
        "heading": family.name,
        "back_url": reverse("admin_instrument_family"),
        "rows": rows,
    })


@admin_required
def admin_instrument_members(request, instrument_id):
    instrument = get_object_or_404(models.Instrument, pk=instrument_id)
    people = (
        models.Person.objects
        .filter(instruments=instrument)
        .order_by("lastName", "firstNames")
    )
    rows = [{"person": p, "instruments": [instrument]} for p in people]
    return render(request, "admin_instrument_members.html", {
        "heading": instrument.name,
        "back_url": reverse("admin_instrument"),
        "rows": rows,
    })


# ---------------------------------------------------------------------------
# Modal-form CRUD pages.
# ---------------------------------------------------------------------------
@admin_required
def admin_instrument(request, id=None):
    return crud.crud_view(
        request,
        model=models.Instrument,
        form_class=forms.InstrumentForm,
        page_title="Instruments",
        columns=[
            {"label": "Name", "field": "name"},
            {"label": "Family", "field": "family.name"},
        ],
        list_context_name="instruments",
        list_url_name="admin_instrument",
        form_url_name="admin_instrument_form",
        save_url_name="admin_instrument",
        delete_url_name="admin_instrument_delete",
        row_extra_actions=[{"label": "Members", "url_name": "admin_instrument_members"}],
        id=id,
    )


@admin_required
def admin_person(request, id=None):
    return crud.crud_view(
        request,
        model=models.Person,
        form_class=forms.PersonForm,
        page_title="People",
        singular="Person",
        columns=[
            {"label": "First names", "field": "firstNames"},
            {"label": "Last name", "field": "lastName"},
            {"label": "Email", "field": "email"},
            {"label": "Phone", "field": "phone"},
            {"label": "Instruments", "field": "instruments"},
            {"label": "Member?", "field": "member"},
            {"label": "Admin?", "field": "is_admin"},
        ],
        list_context_name="people",
        list_url_name="admin_person",
        form_url_name="admin_person_form",
        save_url_name="admin_person",
        delete_url_name="admin_person_delete",
        row_extra_actions=[{"label": "Set password", "url_name": "admin_person_set_password"}],
        id=id,
    )


@admin_required
def admin_set_password(request, id):
    person = get_object_or_404(models.Person, pk=id)
    if request.method == "POST":
        form = forms.DaisySetPasswordForm(person, request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f"Password updated for {person}.")
            return redirect("admin_person")
    else:
        form = forms.DaisySetPasswordForm(person)
    return render(request, "admin_set_password.html", {"form": form, "person": person})


@admin_required
def admin_venue(request, id=None):
    return crud.crud_view(
        request,
        model=models.Venue,
        form_class=forms.VenueForm,
        page_title="Venues",
        columns=[
            {"label": "Name", "field": "name"},
            {"label": "Address", "field": "address"},
            {"label": "Postcode", "field": "postcode"},
            {"label": "Contact", "field": "contactName"},
            {"label": "Phone", "field": "phone"},
            {"label": "Email", "field": "email"},
        ],
        list_context_name="venues",
        list_url_name="admin_venue",
        form_url_name="admin_venue_form",
        save_url_name="admin_venue",
        delete_url_name="admin_venue_delete",
        id=id,
    )


@admin_required
def admin_rental_contract(request, id=None):
    return crud.crud_view(
        request,
        model=models.RentalContract,
        form_class=forms.RentalContractForm,
        page_title="Rental Contracts",
        columns=[
            {"label": "Description", "field": "description"},
            {"label": "Supplier", "field": "supplier"},
            {"label": "Cost", "field": "cost"},
            {"label": "Start", "field": "startDate"},
            {"label": "End", "field": "endDate"},
        ],
        list_context_name="contracts",
        list_url_name="admin_rental_contract",
        form_url_name="admin_rental_contract_form",
        save_url_name="admin_rental_contract",
        delete_url_name="admin_rental_contract_delete",
        id=id,
    )


@admin_required
def admin_music_item(request, id=None):
    return crud.crud_view(
        request,
        model=models.MusicItem,
        form_class=forms.MusicItemForm,
        page_title="Music Items",
        columns=[
            {"label": "Name", "field": "name"},
            {"label": "Composer", "field": "composer"},
            {"label": "Duration", "field": "duration"},
            {"label": "Contract", "field": "contract.description"},
        ],
        list_context_name="music_items",
        list_url_name="admin_music_item",
        form_url_name="admin_music_item_form",
        save_url_name="admin_music_item",
        delete_url_name="admin_music_item_delete",
        row_extra_actions=[{"label": "Parts", "url_name": "admin_music_item_parts"}],
        page_notice=(
            "Reminder: by uploading scores or parts to this site you confirm you have the right to do so "
            "and will comply with all relevant terms of hire, licensing, and copyright obligations."
        ),
        id=id,
    )


_ALLOWED_PART_EXTENSIONS = {
    ".pdf", ".xml", ".mxl", ".mus", ".musx", ".sib", ".mscz",
    ".mid", ".midi", ".mp3", ".wav", ".flac", ".ogg",
}


def _validate_part_upload(uploaded_file):
    """Return the lowercased extension, or raise ValueError if not allowed."""
    import os
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in _ALLOWED_PART_EXTENSIONS:
        allowed = ", ".join(sorted(_ALLOWED_PART_EXTENSIONS))
        raise ValueError(f"File type '{ext}' is not allowed. Allowed: {allowed}")
    return ext


def _parse_part_assignment(post):
    """Read the 'is Score / which instruments' checklist from a part form POST."""
    is_score = post.get("is_score") in ("1", "true", "on")
    instrument_ids = [i for i in post.getlist("instrument_ids") if i]
    return is_score, instrument_ids


def _part_json(obj):
    import json
    return json.dumps({
        "id": obj.pk,
        "file_url": obj.file.url if obj.file else "",
        "file_name": obj.file.name.split("/")[-1] if obj.file else "",
        "external_link": obj.external_link,
        "is_score": obj.is_score,
        "instrument_ids": list(obj.instruments.values_list("id", flat=True)),
        "label": obj.label,
    })


@admin_required
def admin_music_item_parts(request, music_item_id, part_id=None):
    music_item = get_object_or_404(models.MusicItem, pk=music_item_id)

    # Extract trailing id from path (0 = add, N = update/delete)
    path_parts = request.path.rstrip("/").split("/")
    try:
        path_id = int(path_parts[-1])
    except (ValueError, IndexError):
        path_id = None

    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        if uploaded_file:
            try:
                _validate_part_upload(uploaded_file)
            except ValueError as e:
                return HttpResponseBadRequest(str(e))

        # Clearing a file is a minimal, self-contained action — it doesn't
        # touch the score/instrument assignment, so it skips that validation.
        if path_id != 0 and request.POST.get("clear_file") == "1":
            obj = get_object_or_404(models.MusicItemPart, pk=path_id)
            if obj.file:
                obj.file.delete(save=False)
                obj.file = None
                obj.save(update_fields=["file"])
            return HttpResponse("ok")

        is_score, instrument_ids = _parse_part_assignment(request.POST)
        if not is_score and not instrument_ids:
            return HttpResponseBadRequest("Select Score and/or at least one instrument.")
        instruments = models.Instrument.objects.filter(pk__in=instrument_ids)

        if path_id == 0:  # add
            obj = models.MusicItemPart(
                musicItem=music_item,
                is_score=is_score,
                external_link=forms.normalise_url(request.POST.get("external_link", "")),
            )
            if uploaded_file:
                obj.file = uploaded_file
            obj.save()
            obj.instruments.set(instruments)
            return HttpResponse(_part_json(obj), content_type="application/json")
        else:  # update
            obj = get_object_or_404(models.MusicItemPart, pk=path_id)
            obj.external_link = forms.normalise_url(request.POST.get("external_link", obj.external_link))
            obj.is_score = is_score
            update_fields = ["external_link", "is_score"]
            if uploaded_file:
                # Delete old file from disk before replacing
                if obj.file:
                    obj.file.delete(save=False)
                obj.file = uploaded_file
                update_fields.append("file")
            obj.save(update_fields=update_fields)
            obj.instruments.set(instruments)
            return HttpResponse(_part_json(obj), content_type="application/json")

    if request.method == "DELETE":
        obj = get_object_or_404(models.MusicItemPart, pk=path_id)
        if obj.file:
            obj.file.delete(save=False)
        obj.delete()
        return HttpResponse("ok")

    parts = list(
        models.MusicItemPart.objects.filter(musicItem=music_item)
        .prefetch_related("instruments")
    )
    for p in parts:
        p.instrument_id_list = [i.id for i in p.instruments.all()]
    instruments = models.Instrument.objects.select_related("family").order_by("name")
    return render(request, "admin_music_item_parts.html", {
        "music_item": music_item,
        "parts": parts,
        "instruments": instruments,
        "allowed_extensions": ", ".join(sorted(_ALLOWED_PART_EXTENSIONS)),
    })


@admin_required
def admin_rehearsal_series(request, id=None):
    return crud.crud_view(
        request,
        model=models.RehearsalSeries,
        form_class=forms.RehearsalSeriesForm,
        page_title="Rehearsal Series",
        columns=[
            {"label": "Name", "field": "name"},
            {"label": "Frequency", "field": "frequency"},
            {"label": "Day", "field": "get_day_of_week_display"},
            {"label": "Start", "field": "series_start"},
            {"label": "End", "field": "series_end"},
        ],
        list_context_name="series_list",
        list_url_name="admin_rehearsal_series",
        form_url_name="admin_rehearsal_series_form",
        save_url_name="admin_rehearsal_series",
        delete_url_name="admin_rehearsal_series_delete",
        singular="Rehearsal Series",
        row_extra_actions=[{"label": "Generate", "url_name": "admin_rehearsal_series_generate"}],
        id=id,
    )


@admin_required
def admin_rehearsal_series_generate(request, series_id):
    series = get_object_or_404(models.RehearsalSeries, pk=series_id)
    form = forms.RehearsalSeriesGenerateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        created, updated = series.generate_rehearsals(form.cleaned_data["until"])
        parts = []
        if created:
            parts.append(f'created {created}')
        if updated:
            parts.append(f'updated {updated}')
        if not parts:
            parts.append('nothing to do')
        messages.success(request, f'"{series}": {", ".join(parts)}.')
        return redirect("admin_rehearsal_series")
    return render(request, "admin_rehearsal_series_generate.html", {"series": series, "form": form})


@admin_required
def admin_rehearsal(request, id=None):
    return crud.crud_view(
        request,
        model=models.Rehearsal,
        form_class=forms.RehearsalForm,
        page_title="Rehearsals",
        columns=[
            {"label": "Name", "field": "name"},
            {"label": "Date", "field": "startDate"},
            {"label": "Start", "field": "startTime"},
            {"label": "End", "field": "endTime"},
            {"label": "Venue", "field": "venue.name"},
            {"label": "Series", "field": "series.name"},
        ],
        list_context_name="rehearsals",
        list_url_name="admin_rehearsal",
        form_url_name="admin_rehearsal_form",
        save_url_name="admin_rehearsal",
        delete_url_name="admin_rehearsal_delete",
        row_extra_actions=[
            {"label": "Repertoire", "url_name": "admin_rehearsal_repertoire"},
            {"label": "Attendance", "url_name": "admin_rehearsal_attendance"},
        ],
        id=id,
    )


@admin_required
def admin_rehearsal_attendance(request, rehearsal_id):
    rehearsal = get_object_or_404(models.Rehearsal, pk=rehearsal_id)
    _qs = dict(rehearsal=rehearsal)
    yes_rsvps = (models.RSVP.objects.filter(**_qs, status=models.RSVP.STATUS_YES)
                 .select_related("person").prefetch_related("playing_instruments", "person__instruments"))
    maybe_rsvps = (models.RSVP.objects.filter(**_qs, status=models.RSVP.STATUS_MAYBE)
                   .select_related("person").prefetch_related("playing_instruments", "person__instruments"))
    rows = _merged_attendance(yes_rsvps, maybe_rsvps)
    return render(request, "admin_attendance.html", {
        "event": rehearsal,
        "event_date": rehearsal.startDate,
        "back_url": "admin_rehearsal",
        "rows": rows,
        "total_going": sum(len(r["going"]) for r in rows),
        "total_maybe": sum(len(r["maybe"]) for r in rows),
    })


def _parse_time(s):
    """Parse 'HH:MM' string → datetime.time, or return None."""
    if not s:
        return None
    try:
        from datetime import time as dt_time
        h, m = s.split(":")
        return dt_time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def _repertoire_view(request, event, items_qs, item_model, event_fk_name, back_url_name, template):
    """Shared inline-XHR handler for RehearsalItem / PerformanceItem."""
    import json as _json

    item_id = None
    # Extract trailing numeric segment from path (0 = add new, N = update/delete)
    path_segs = request.path.rstrip("/").split("/")
    try:
        item_id = int(path_segs[-1])
    except (ValueError, IndexError):
        item_id = None

    if request.method == "POST":
        # Bulk reorder: ?action=reorder  body: order[]=id1&order[]=id2...
        if request.GET.get("action") == "reorder":
            ids = request.POST.getlist("order[]")
            for idx, oid in enumerate(ids, start=1):
                item_model.objects.filter(pk=oid).update(order=idx)
            return HttpResponse("ok")

        music_item_id = request.POST.get("music_item_id")
        start_time = _parse_time(request.POST.get("start_time", ""))

        if item_id == 0:  # add
            # Auto-assign next order value
            last = items_qs.order_by("order").last()
            order = (last.order + 1) if last else 1
            music = get_object_or_404(models.MusicItem, pk=music_item_id)
            obj = item_model.objects.create(
                **{event_fk_name: event, "musicItem": music, "order": order, "start_time": start_time}
            )
            duration_display = ""
            if music.duration:
                total_seconds = int(music.duration.total_seconds())
                m, s = divmod(total_seconds, 60)
                h, m = divmod(m, 60)
                duration_display = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            return HttpResponse(
                _json.dumps({
                    "id": obj.pk,
                    "title": str(music),
                    "start_time": obj.start_time.strftime("%H:%M") if obj.start_time else "",
                    "duration": duration_display,
                }),
                content_type="application/json",
            )
        else:  # update start_time for an existing item
            obj = get_object_or_404(item_model, pk=item_id)
            obj.start_time = start_time
            obj.save(update_fields=["start_time"])
            return HttpResponse("ok")

    if request.method == "DELETE":
        obj = get_object_or_404(item_model, pk=item_id)
        obj.delete()
        return HttpResponse("ok")

    # GET — render the editor page
    all_music = models.MusicItem.objects.order_by("name")
    items = items_qs.select_related("musicItem").order_by("order")
    return render(request, template, {
        "event": event,
        "items": items,
        "all_music": all_music,
        "back_url": back_url_name,
    })


@admin_required
def admin_rehearsal_repertoire(request, rehearsal_id, item_id=None):
    rehearsal = get_object_or_404(models.Rehearsal, pk=rehearsal_id)
    return _repertoire_view(
        request, rehearsal,
        models.RehearsalItem.objects.filter(rehearsal=rehearsal),
        models.RehearsalItem, "rehearsal",
        "admin_rehearsal", "admin_repertoire.html",
    )


@admin_required
def admin_performance_repertoire(request, performance_id, item_id=None):
    performance = get_object_or_404(models.Performance, pk=performance_id)
    return _repertoire_view(
        request, performance,
        models.PerformanceItem.objects.filter(performance=performance),
        models.PerformanceItem, "performance",
        "admin_performance", "admin_repertoire.html",
    )


@login_required
def polls(request):
    person = request.user
    active_polls = list(
        models.Poll.objects.filter(active=True)
        .prefetch_related("questions__choices")
    )
    all_questions = [q for poll in active_polls for q in poll.questions.all()]
    answers = (
        models.PollAnswer.objects
        .filter(person=person, question__in=all_questions)
        .select_related("choice")
    )
    answers_by_question = {}
    for a in answers:
        answers_by_question.setdefault(a.question_id, []).append(a)
    for question in all_questions:
        question.my_answers = answers_by_question.get(question.id, [])
    return render(request, "polls.html", {"polls": active_polls})


@login_required
@require_POST
def poll_answer(request, question_id):
    question = get_object_or_404(
        models.PollQuestion, pk=question_id, poll__active=True,
    )
    person = request.user

    if question.kind == models.PollQuestion.TYPE_FREE_TEXT:
        text = request.POST.get("answer", "").strip()
        if not text:
            return HttpResponseBadRequest("Answer is required")
        models.PollAnswer.objects.update_or_create(
            question=question, person=person, choice=None,
            defaults={"text_value": text},
        )

    elif question.kind == models.PollQuestion.TYPE_SINGLE:
        choice_id = request.POST.get("choice", "")
        choice = get_object_or_404(models.PollChoice, pk=choice_id, question=question)
        models.PollAnswer.objects.filter(question=question, person=person).delete()
        models.PollAnswer.objects.create(question=question, person=person, choice=choice)

    elif question.kind == models.PollQuestion.TYPE_MULTI:
        raw_ids = request.POST.getlist("choices")
        selected = {
            int(x) for x in raw_ids
            if x.isdigit()
            and question.choices.filter(pk=int(x)).exists()
        }
        models.PollAnswer.objects.filter(question=question, person=person).delete()
        models.PollAnswer.objects.bulk_create([
            models.PollAnswer(question=question, person=person, choice_id=cid)
            for cid in selected
        ])

    messages.success(request, "Answer saved.")
    return redirect("polls")


@admin_required
def admin_poll(request, id=None):
    return crud.crud_view(
        request,
        model=models.Poll,
        form_class=forms.PollForm,
        page_title="Polls",
        columns=[
            {"label": "Title", "field": "title"},
            {"label": "Active?", "field": "active"},
            {"label": "Created", "field": "createdAt"},
            {"label": "Questions", "field": "questions.count"},
        ],
        list_context_name="polls",
        list_url_name="admin_poll",
        form_url_name="admin_poll_form",
        save_url_name="admin_poll",
        delete_url_name="admin_poll_delete",
        row_extra_actions=[
            {"label": "Questions", "url_name": "admin_poll_questions"},
            {"label": "Responses", "url_name": "admin_poll_responses"},
        ],
        id=id,
    )


@admin_required
def admin_poll_questions(request, poll_id, id=None):
    poll = get_object_or_404(models.Poll, pk=poll_id)

    if request.method == "GET" and request.path.endswith("/form/"):
        instance = get_object_or_404(models.PollQuestion, pk=id, poll=poll) if id else None
        form = forms.PollQuestionForm(instance=instance)
        return render(request, "_modal_form.html", {"form": form, "instance": instance})

    if request.method == "POST" and request.path.endswith("/delete/"):
        get_object_or_404(models.PollQuestion, pk=id, poll=poll).delete()
        return redirect("admin_poll_questions", poll_id=poll_id)

    if request.method == "POST":
        instance = get_object_or_404(models.PollQuestion, pk=id, poll=poll) if id else None
        form = forms.PollQuestionForm(request.POST, instance=instance)
        if form.is_valid():
            q = form.save(commit=False)
            q.poll = poll
            q.save()
            return redirect("admin_poll_questions", poll_id=poll_id)
        questions = poll.questions.prefetch_related("choices")
        return render(request, "admin_poll_questions.html", {
            "poll": poll, "questions": questions,
            "form": form, "open_modal": True, "edit_id": id or 0,
        })

    questions = poll.questions.prefetch_related("choices")
    return render(request, "admin_poll_questions.html", {
        "poll": poll, "questions": questions,
        "form": forms.PollQuestionForm(), "open_modal": False, "edit_id": 0,
    })


@admin_required
def admin_poll_choices(request, poll_id, question_id, id=None):
    poll = get_object_or_404(models.Poll, pk=poll_id)
    question = get_object_or_404(models.PollQuestion, pk=question_id, poll=poll)

    if request.method == "POST":
        text = request.POST.get("text", "").strip()
        if not text:
            return HttpResponseBadRequest("Text is required")
        if id == 0 or id is None:
            choice = models.PollChoice(question=question, text=text)
        else:
            choice = get_object_or_404(models.PollChoice, pk=id, question=question)
            choice.text = text
        choice.save()
        return HttpResponse(str(choice.id))

    if request.method == "DELETE":
        get_object_or_404(models.PollChoice, pk=id, question=question).delete()
        return HttpResponse("OK")

    return render(request, "admin_poll_choices.html", {
        "poll": poll,
        "question": question,
        "choices": question.choices.all(),
    })


@admin_required
def admin_poll_responses(request, id):
    poll = get_object_or_404(models.Poll, pk=id)
    questions = poll.questions.prefetch_related(
        "answers__person", "answers__choice", "choices",
    )
    return render(request, "admin_poll_responses.html", {
        "poll": poll,
        "questions": questions,
    })


@admin_required
def admin_poll_choice_respondents(request, poll_id, choice_id):
    poll = get_object_or_404(models.Poll, pk=poll_id)
    choice = get_object_or_404(models.PollChoice, pk=choice_id, question__poll=poll)
    respondents = (
        models.PollAnswer.objects
        .filter(choice=choice)
        .select_related("person")
        .order_by("person__lastName", "person__firstNames")
    )
    return render(request, "admin_poll_choice_respondents.html", {
        "poll": poll,
        "choice": choice,
        "respondents": respondents,
    })


@admin_required
def admin_poll_responses_table(request, poll_id):
    poll = get_object_or_404(models.Poll, pk=poll_id)
    questions = list(poll.questions.prefetch_related("choices").order_by("order"))
    # Build {person: {question_id: [answer_display, ...]}} mapping
    all_answers = (
        models.PollAnswer.objects
        .filter(question__poll=poll)
        .select_related("person", "choice")
        .order_by("person__lastName", "person__firstNames")
    )
    from collections import defaultdict
    people_map = {}  # person -> {q_id: [display]}
    for ans in all_answers:
        p = ans.person
        if p not in people_map:
            people_map[p] = defaultdict(list)
        if ans.choice:
            people_map[p][ans.question_id].append(ans.choice.text)
        else:
            people_map[p][ans.question_id].append(ans.text_value)
    rows = [
        {"person": person, "answers": [", ".join(people_map[person].get(q.id, ["—"])) for q in questions]}
        for person in sorted(people_map, key=lambda p: (p.lastName, p.firstNames))
    ]
    return render(request, "admin_poll_responses_table.html", {
        "poll": poll,
        "questions": questions,
        "rows": rows,
    })


@admin_required
def admin_site_content(request):
    """Edit the singleton public home page content (description + contact details)."""
    content = models.SiteContent.load()
    if request.method == "POST":
        form = forms.SiteContentForm(request.POST, instance=content)
        if form.is_valid():
            form.save()
            messages.success(request, "Home page content updated.")
            return redirect("admin_site_content")
    else:
        form = forms.SiteContentForm(instance=content)
    return render(request, "admin_site_content.html", {"form": form})


@admin_required
def admin_carousel_image(request, id=None):
    return crud.crud_view(
        request,
        model=models.CarouselImage,
        form_class=forms.CarouselImageForm,
        page_title="Carousel Images",
        columns=[
            {"label": "Preview", "field": "image"},
            {"label": "Caption", "field": "caption"},
            {"label": "Order", "field": "order"},
        ],
        list_context_name="carousel_images",
        list_url_name="admin_carousel_image",
        form_url_name="admin_carousel_image_form",
        save_url_name="admin_carousel_image",
        delete_url_name="admin_carousel_image_delete",
        id=id,
    )


@admin_required
def admin_announcement(request, id=None):
    return crud.crud_view(
        request,
        model=models.Announcement,
        form_class=forms.AnnouncementForm,
        page_title="Announcements",
        columns=[
            {"label": "Title", "field": "title"},
            {"label": "Severity", "field": "severity"},
            {"label": "Active?", "field": "active"},
            {"label": "Visible from", "field": "visibleFrom"},
            {"label": "Visible until", "field": "visibleUntil"},
        ],
        list_context_name="announcements",
        list_url_name="admin_announcement",
        form_url_name="admin_announcement_form",
        save_url_name="admin_announcement",
        delete_url_name="admin_announcement_delete",
        id=id,
    )


@admin_required
def admin_performance(request, id=None):
    return crud.crud_view(
        request,
        model=models.Performance,
        form_class=forms.PerformanceForm,
        page_title="Performances",
        columns=[
            {"label": "Name", "field": "name"},
            {"label": "Date", "field": "date"},
            {"label": "Time", "field": "time"},
            {"label": "Venue", "field": "venue.name"},
            {"label": "Published?", "field": "published"},
        ],
        list_context_name="performances",
        list_url_name="admin_performance",
        form_url_name="admin_performance_form",
        save_url_name="admin_performance",
        delete_url_name="admin_performance_delete",
        row_extra_actions=[
            {"label": "Repertoire", "url_name": "admin_performance_repertoire"},
            {"label": "Attendance", "url_name": "admin_performance_attendance"},
        ],
        id=id,
    )


@admin_required
def admin_social_apps(request, id=None):
    from django.conf import settings as _settings
    notice = None if _settings.SOCIAL_LOGIN_ENABLED else (
        "Social login is currently disabled (SOCIAL_LOGIN_ENABLED = False). "
        "Configure apps here, then set SOCIAL_LOGIN_ENABLED = True to activate them."
    )
    return crud.crud_view(
        request,
        model=__import__("allauth.socialaccount.models", fromlist=["SocialApp"]).SocialApp,
        form_class=forms.SocialAppForm,
        page_title="Social Auth Apps",
        singular="Social App",
        columns=[
            {"label": "Provider", "field": "provider"},
            {"label": "Name", "field": "name"},
            {"label": "Client ID", "field": "client_id"},
        ],
        list_context_name="social_apps",
        list_url_name="admin_social_apps",
        form_url_name="admin_social_apps_form",
        save_url_name="admin_social_apps",
        delete_url_name="admin_social_apps_delete",
        page_notice=notice,
        id=id,
    )


@admin_required
def admin_performance_attendance(request, performance_id):
    performance = get_object_or_404(models.Performance, pk=performance_id)
    _qs = dict(performance=performance)
    yes_rsvps = (models.RSVP.objects.filter(**_qs, status=models.RSVP.STATUS_YES)
                 .select_related("person").prefetch_related("playing_instruments", "person__instruments"))
    maybe_rsvps = (models.RSVP.objects.filter(**_qs, status=models.RSVP.STATUS_MAYBE)
                   .select_related("person").prefetch_related("playing_instruments", "person__instruments"))
    rows = _merged_attendance(yes_rsvps, maybe_rsvps)
    return render(request, "admin_attendance.html", {
        "event": performance,
        "event_date": performance.date,
        "back_url": "admin_performance",
        "rows": rows,
        "total_going": sum(len(r["going"]) for r in rows),
        "total_maybe": sum(len(r["maybe"]) for r in rows),
    })


# ── Mailing lists ─────────────────────────────────────────────────────────────

def _reconcile_dynamic(ml) -> tuple[int, int]:
    """Update MailingListMember rows to match a dynamic list's computed membership.

    - Adds new MailingListMember rows (SYNC_PENDING) for people now in scope.
    - Deletes rows (and calls remove_member on the provider) for people no longer in scope.
    Returns (added, removed).
    """
    from . import mailing as _mailing

    computed_qs = ml.get_computed_members()
    if computed_qs is None:
        return 0, 0

    target_ids = set(computed_qs.values_list("id", flat=True))
    existing = {m.person_id: m for m in ml.list_members.select_related("person")}
    existing_ids = set(existing)

    added = 0
    for pid in target_ids - existing_ids:
        models.MailingListMember.objects.create(
            mailing_list=ml,
            person_id=pid,
            sync_status=models.MailingListMember.SYNC_PENDING,
        )
        added += 1

    removed = 0
    for pid in existing_ids - target_ids:
        member = existing[pid]
        _mailing.remove_member(ml, member.person)
        member.delete()
        removed += 1

    return added, removed


_MAILING_LIST_MODAL_JS = """
function modalInitHook() {
  var ft   = document.getElementById('id_filter_type');
  var prov = document.getElementById('id_provider');
  if (!ft || !prov) return;

  function field(name) {
    return document.querySelector('[data-field="' + name + '"]');
  }
  function setVisible(name, visible) {
    var el = field(name);
    if (el) el.style.display = visible ? '' : 'none';
  }

  function update() {
    var ftv = ft.value, pv = prov.value;
    setVisible('filter_instrument',        ftv === 'instrument');
    setVisible('filter_instrument_family', ftv === 'instrument_family');
    setVisible('list_id',                  pv !== 'manual');
    setVisible('api_key',                  pv === 'mailchimp' || pv === 'brevo');
    setVisible('google_service_account_json', pv === 'google_groups');
    setVisible('google_delegated_admin',   pv === 'google_groups');
  }

  update();
  ft.addEventListener('change', update);
  prov.addEventListener('change', update);
}
"""


@admin_required
def admin_mailing_list(request, id=None):
    from django.utils.safestring import mark_safe
    return crud.crud_view(
        request,
        model=models.MailingList,
        form_class=forms.MailingListForm,
        page_title="Mailing Lists",
        singular="Mailing List",
        columns=[
            {"label": "Name", "field": "name"},
            {"label": "Provider", "field": "provider"},
            {"label": "Address", "field": "address"},
        ],
        list_context_name="lists",
        list_url_name="admin_mailing_list",
        form_url_name="admin_mailing_list_form",
        save_url_name="admin_mailing_list",
        delete_url_name="admin_mailing_list_delete",
        row_extra_actions=[
            {"label": "Members", "url_name": "admin_mailing_list_detail"},
        ],
        id=id,
        extra_context={"modal_init_js": mark_safe(_MAILING_LIST_MODAL_JS)},
    )


@admin_required
def admin_mailing_list_detail(request, list_id):
    import csv as _csv
    from . import mailing as _mailing
    from django.utils import timezone as _tz

    ml = get_object_or_404(models.MailingList, pk=list_id)
    provider_needs_sync = ml.provider != models.MailingList.PROVIDER_MANUAL

    # ── Exports (GET) ─────────────────────────────────────────────────────────
    export = request.GET.get("export")
    if export:
        # For dynamic lists, export from the computed queryset; for manual, from the DB table.
        if ml.is_dynamic:
            computed = ml.get_computed_members()
            people = list(computed.order_by("lastName", "firstNames")) if computed else []
            rows_iter = (
                (p.firstNames, p.lastName, p.email) for p in people
            )
        else:
            qs = ml.list_members.select_related("person").order_by(
                "person__lastName", "person__firstNames"
            )
            rows_iter = (
                (m.person.firstNames, m.person.lastName, m.person.email) for m in qs
            )
        if export == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="{ml.name}.csv"'
            writer = _csv.writer(response)
            writer.writerow(["First names", "Last name", "Email"])
            for row in rows_iter:
                writer.writerow(row)
            return response
        if export == "emails":
            lines = "\n".join(r[2] for r in rows_iter)
            response = HttpResponse(lines, content_type="text/plain")
            response["Content-Disposition"] = f'attachment; filename="{ml.name}-emails.txt"'
            return response

    # ── POST actions ──────────────────────────────────────────────────────────
    if request.method == "POST":
        action = request.POST.get("action")

        # ── Dynamic-only: reconcile membership table ───────────────────────
        if action == "reconcile" and ml.is_dynamic:
            added, removed = _reconcile_dynamic(ml)
            messages.success(request, f"Reconciled: {added} added, {removed} removed.")
            return redirect("admin_mailing_list_detail", list_id)

        # ── Sync all (reconcile first for dynamic lists, then push to provider) ─
        if action == "sync_all":
            if ml.is_dynamic:
                added, removed = _reconcile_dynamic(ml)
                if added or removed:
                    messages.info(request, f"Reconciled before sync: {added} added, {removed} removed.")
            synced = errors = 0
            for member in ml.list_members.select_related("person"):
                ok, err = _mailing.sync_member(member)
                if ok:
                    member.sync_status = models.MailingListMember.SYNC_OK
                    member.sync_error = ""
                    member.synced_at = _tz.now()
                    synced += 1
                else:
                    member.sync_status = models.MailingListMember.SYNC_ERROR
                    member.sync_error = err
                    errors += 1
                member.save(update_fields=["sync_status", "sync_error", "synced_at"])
            if errors:
                messages.error(request, f"Sync: {synced} OK, {errors} error(s). See the Sync column.")
            else:
                messages.success(request, f"Synced {synced} member(s) successfully.")
            return redirect("admin_mailing_list_detail", list_id)

        # ── Manual-only actions ────────────────────────────────────────────
        if action == "add_members" and not ml.is_dynamic:
            person_ids = request.POST.getlist("person_ids")
            if not person_ids:
                messages.warning(request, "No people selected.")
            else:
                added = sum(
                    1 for pid in person_ids
                    if models.MailingListMember.objects.get_or_create(
                        mailing_list=ml,
                        person_id=pid,
                        defaults={"sync_status": models.MailingListMember.SYNC_PENDING},
                    )[1]
                )
                messages.success(request, f"Added {added} member(s).")
            return redirect("admin_mailing_list_detail", list_id)

        if action == "add_all_members" and not ml.is_dynamic:
            existing_ids = set(ml.list_members.values_list("person_id", flat=True))
            added = 0
            for person in models.Person.objects.filter(member=True, is_active=True):
                if person.id not in existing_ids:
                    models.MailingListMember.objects.create(
                        mailing_list=ml, person=person,
                        sync_status=models.MailingListMember.SYNC_PENDING,
                    )
                    added += 1
            messages.success(request, f"Added {added} new active member(s).")
            return redirect("admin_mailing_list_detail", list_id)

        if action == "remove_member" and not ml.is_dynamic:
            member = get_object_or_404(
                models.MailingListMember, pk=request.POST.get("member_id"), mailing_list=ml
            )
            _mailing.remove_member(ml, member.person)
            member.delete()
            messages.success(request, "Member removed.")
            return redirect("admin_mailing_list_detail", list_id)

        if action == "sync_member" and not ml.is_dynamic:
            member = get_object_or_404(
                models.MailingListMember, pk=request.POST.get("member_id"), mailing_list=ml
            )
            ok, err = _mailing.sync_member(member)
            if ok:
                member.sync_status = models.MailingListMember.SYNC_OK
                member.sync_error = ""
                member.synced_at = _tz.now()
            else:
                member.sync_status = models.MailingListMember.SYNC_ERROR
                member.sync_error = err
            member.save(update_fields=["sync_status", "sync_error", "synced_at"])
            if ok:
                messages.success(request, "Synced successfully.")
            else:
                messages.error(request, f"Sync failed: {err}")
            return redirect("admin_mailing_list_detail", list_id)

    # ── GET: build unified members_display list ────────────────────────────────
    # Both dynamic and manual produce a list of dicts:
    # {person, mlm_id, sync_status, sync_error, synced_at}
    # This keeps the template simple — one table works for both modes.

    if ml.is_dynamic:
        computed = ml.get_computed_members()
        member_map = {m.person_id: m for m in ml.list_members.all()}
        members_display = []
        if computed is not None:
            for person in computed.order_by("lastName", "firstNames"):
                mlm = member_map.get(person.id)
                members_display.append({
                    "person": person,
                    "mlm_id": mlm.id if mlm else None,
                    "sync_status": mlm.sync_status if mlm else models.MailingListMember.SYNC_PENDING,
                    "sync_error": mlm.sync_error if mlm else "",
                    "synced_at": mlm.synced_at if mlm else None,
                })
        available_people = None
    else:
        mlm_qs = ml.list_members.select_related("person").order_by(
            "person__lastName", "person__firstNames"
        )
        members_display = [{
            "person": m.person,
            "mlm_id": m.id,
            "sync_status": m.sync_status,
            "sync_error": m.sync_error,
            "synced_at": m.synced_at,
        } for m in mlm_qs]
        existing_ids = {m["person"].id for m in members_display}
        available_people = (
            models.Person.objects
            .filter(is_active=True)
            .exclude(id__in=existing_ids)
            .order_by("lastName", "firstNames")
        )

    return render(request, "admin_mailing_list_detail.html", {
        "ml": ml,
        "members_display": members_display,
        "available_people": available_people,
        "provider_needs_sync": provider_needs_sync,
    })
