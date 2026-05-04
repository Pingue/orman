"""External mailing-list provider sync helpers.

Each sync function returns ``(ok: bool, error_message: str)``.

Supported providers
-------------------
manual        — no sync needed; members are tracked locally only.
mailchimp     — Mailchimp Marketing API v3 (subscribe/unsubscribe).
brevo         — Brevo (Sendinblue) API v3 (add contact to list / remove).
google_groups — Google Admin SDK Directory API (requires google-api-python-client).
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request


# ── Internal helpers ──────────────────────────────────────────────────────────

def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _http(method: str, url: str, data=None, headers: dict | None = None) -> tuple[int, str]:
    """Minimal urllib wrapper. Returns (status_code, response_body)."""
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if body:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except Exception as exc:
        return 0, str(exc)


# ── Public API ────────────────────────────────────────────────────────────────

def sync_member(ml_member) -> tuple[bool, str]:
    """Sync a MailingListMember to its provider. Returns (ok, error_msg)."""
    provider = ml_member.mailing_list.provider
    if provider == "mailchimp":
        return _mailchimp_add(ml_member.mailing_list, ml_member.person)
    if provider == "brevo":
        return _brevo_add(ml_member.mailing_list, ml_member.person)
    if provider == "google_groups":
        return _google_add(ml_member.mailing_list, ml_member.person)
    # manual — nothing to do
    return True, ""


def remove_member(ml, person) -> tuple[bool, str]:
    """Remove a person from an external provider list. Returns (ok, error_msg)."""
    if ml.provider == "mailchimp":
        return _mailchimp_remove(ml, person)
    if ml.provider == "brevo":
        return _brevo_remove(ml, person)
    if ml.provider == "google_groups":
        return _google_remove(ml, person)
    return True, ""


# ── Mailchimp ─────────────────────────────────────────────────────────────────

def _mc_base(ml) -> str:
    dc = (ml.api_key or "").split("-")[-1] or "us1"
    return f"https://{dc}.api.mailchimp.com/3.0"


def _mc_email_hash(email: str) -> str:
    import hashlib
    return hashlib.md5(email.lower().encode()).hexdigest()


def _mc_headers(ml) -> dict:
    return {"Authorization": f"Basic {_b64(f'anystring:{ml.api_key}')}"}


def _mailchimp_add(ml, person) -> tuple[bool, str]:
    if not ml.api_key or not ml.list_id:
        return False, "Mailchimp requires an API key and audience ID."
    url = f"{_mc_base(ml)}/lists/{ml.list_id}/members/{_mc_email_hash(person.email)}"
    status, body = _http("PUT", url, {
        "email_address": person.email,
        "status_if_new": "subscribed",
        "status": "subscribed",
        "merge_fields": {"FNAME": person.firstNames, "LNAME": person.lastName},
    }, _mc_headers(ml))
    if status in (200, 201):
        return True, ""
    return False, f"HTTP {status}: {body[:300]}"


def _mailchimp_remove(ml, person) -> tuple[bool, str]:
    if not ml.api_key or not ml.list_id:
        return True, ""
    url = f"{_mc_base(ml)}/lists/{ml.list_id}/members/{_mc_email_hash(person.email)}"
    status, body = _http("PATCH", url, {"status": "unsubscribed"}, _mc_headers(ml))
    if status in (200, 204, 404):
        return True, ""
    return False, f"HTTP {status}: {body[:300]}"


# ── Brevo ─────────────────────────────────────────────────────────────────────

_BREVO_BASE = "https://api.brevo.com/v3"


def _brevo_headers(ml) -> dict:
    return {"api-key": ml.api_key}


def _brevo_add(ml, person) -> tuple[bool, str]:
    if not ml.api_key or not ml.list_id:
        return False, "Brevo requires an API key and list ID."
    try:
        list_id_int = int(ml.list_id)
    except ValueError:
        return False, "Brevo list ID must be a number."

    # Create or update the contact (updateEnabled upserts).
    status, body = _http("POST", f"{_BREVO_BASE}/contacts", {
        "email": person.email,
        "attributes": {"FIRSTNAME": person.firstNames, "LASTNAME": person.lastName},
        "listIds": [list_id_int],
        "updateEnabled": True,
    }, _brevo_headers(ml))

    if status in (201, 204):
        return True, ""

    # 400 "Contact already exist" — add to list explicitly.
    if status == 400 and "already exist" in body.lower():
        status2, body2 = _http(
            "POST",
            f"{_BREVO_BASE}/contacts/lists/{list_id_int}/contacts/add",
            {"emails": [person.email]},
            _brevo_headers(ml),
        )
        if status2 in (201, 204):
            return True, ""
        return False, f"HTTP {status2}: {body2[:300]}"

    return False, f"HTTP {status}: {body[:300]}"


def _brevo_remove(ml, person) -> tuple[bool, str]:
    if not ml.api_key or not ml.list_id:
        return True, ""
    try:
        list_id_int = int(ml.list_id)
    except ValueError:
        return True, ""
    status, body = _http(
        "POST",
        f"{_BREVO_BASE}/contacts/lists/{list_id_int}/contacts/remove",
        {"emails": [person.email]},
        _brevo_headers(ml),
    )
    if status in (201, 204, 404):
        return True, ""
    return False, f"HTTP {status}: {body[:300]}"


# ── Google Groups (Admin SDK Directory API) ───────────────────────────────────

def _google_add(ml, person) -> tuple[bool, str]:
    try:
        from googleapiclient.discovery import build  # type: ignore
        from google.oauth2 import service_account   # type: ignore
    except ImportError:
        return False, (
            "google-api-python-client is not installed. "
            "Add 'google-api-python-client google-auth' to requirements.txt."
        )
    if not ml.google_service_account_json or not ml.list_id:
        return False, "Google Groups requires a service-account JSON and group email (list ID)."
    try:
        sa_info = json.loads(ml.google_service_account_json)
    except (json.JSONDecodeError, ValueError) as exc:
        return False, f"Invalid service-account JSON: {exc}"

    scopes = ["https://www.googleapis.com/auth/admin.directory.group.member"]
    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)
    if ml.google_delegated_admin:
        creds = creds.with_subject(ml.google_delegated_admin)

    try:
        svc = build("admin", "directory_v1", credentials=creds, cache_discovery=False)
        svc.members().insert(
            groupKey=ml.list_id,
            body={"email": person.email, "role": "MEMBER"},
        ).execute()
        return True, ""
    except Exception as exc:
        msg = str(exc)
        # "Member already exists" is not an error for our purposes.
        if "already" in msg.lower() or "duplicate" in msg.lower():
            return True, ""
        return False, msg[:300]


def _google_remove(ml, person) -> tuple[bool, str]:
    try:
        from googleapiclient.discovery import build  # type: ignore
        from google.oauth2 import service_account   # type: ignore
    except ImportError:
        return False, "google-api-python-client is not installed."
    if not ml.google_service_account_json or not ml.list_id:
        return True, ""
    try:
        sa_info = json.loads(ml.google_service_account_json)
    except Exception as exc:
        return False, str(exc)

    scopes = ["https://www.googleapis.com/auth/admin.directory.group.member"]
    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)
    if ml.google_delegated_admin:
        creds = creds.with_subject(ml.google_delegated_admin)

    try:
        svc = build("admin", "directory_v1", credentials=creds, cache_discovery=False)
        svc.members().delete(groupKey=ml.list_id, memberKey=person.email).execute()
        return True, ""
    except Exception as exc:
        msg = str(exc)
        if "notFound" in msg or "404" in msg:
            return True, ""  # already not a member
        return False, msg[:300]
