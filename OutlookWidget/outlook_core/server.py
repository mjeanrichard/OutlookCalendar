"""outlook_core, Microsoft Graph sign-in + calendar registry for the outlook_* family.

Plugins can't import each other through normal package paths, so the display
widgets reach this module via the live PluginRegistry
(``current_app.config["PLUGIN_REGISTRY"]``) and call :func:`load_events` /
:func:`load_events_detailed`. Token handling, Graph paging and caching all live
here, exactly the way ``calendar_core`` centralises iCal fetching for the
``calendar_*`` family.

Auth is the OAuth 2.0 device authorization grant: the admin page starts a flow,
shows a short user code, and polls the token endpoint until the user has signed
in on another device. ``offline_access`` gets us a refresh token, which is the
only credential persisted long-term.

Files in the plugin's ``data_dir``:
  token.json         refresh + access token, resolved account name (mode 600)
  device_code.json   in-flight sign-in, deleted once it completes (mode 600)
  calendars.json     calendar list, 1 hour TTL
  view_<key>.json    one calendarView window per calendar, 10 minute TTL

Every public function takes explicit ``cfg`` / ``data_dir`` arguments with
Flask-resolving defaults, so the logic is unit-testable without an app context.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from werkzeug.wrappers import Response

_log = logging.getLogger(__name__)

PLUGIN_ID = "outlook_core"
LOGIN_ROOT = "https://login.microsoftonline.com"
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

# Calendars.Read (not ReadBasic) is what carries location, categories, showAs
# and responseStatus; offline_access is what yields a refresh token; User.Read
# lets us name the signed-in account on the admin page.
SCOPES = "offline_access Calendars.Read User.Read"

# Reading a calendar someone else owns and shared with you needs this on top.
# It stays opt-in: the v2.0 endpoint has no partial consent, so requesting a
# scope the tenant won't grant fails the whole sign-in rather than returning a
# narrower token. Off by default means nobody is locked out of signing in.
SHARED_SCOPE = "Calendars.Read.Shared"

HTTP_TIMEOUT_S = 15
USER_AGENT = "tesserae/0.1 (+outlook_core)"
VIEW_TTL_S = 10 * 60
CALENDARS_TTL_S = 60 * 60
# Refresh a little early so a render never races an expiring token.
TOKEN_EXPIRY_SKEW_S = 60
GRAPH_PAGE_SIZE = 200
MAX_PAGES = 5
CACHE_SCHEMA = 1

EVENT_SELECT = "id,subject,start,end,isAllDay,location,showAs,responseStatus,categories,isCancelled"

# Events the user actively said no to are noise on a wall panel, as are
# cancelled ones. Filtered here so every outlook_* widget agrees.
DROPPED_RESPONSES = frozenset({"declined"})


class OutlookError(RuntimeError):
    """Base for the errors widgets are expected to surface as text."""


class OutlookAuthError(OutlookError):
    """Not signed in, not configured, or the refresh token was rejected."""


class OutlookApiError(OutlookError):
    """Graph refused or couldn't be reached.

    ``status`` is the HTTP status when there was one, so callers can react to
    a 401 without re-parsing the message.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AppConfig:
    """The Entra app registration this install signs in against."""

    client_id: str
    tenant: str = "common"
    read_shared: bool = False

    @property
    def scopes(self) -> str:
        """What to ask for at sign-in. Refreshes use the granted set instead."""
        return f"{SCOPES} {SHARED_SCOPE}" if self.read_shared else SCOPES

    @property
    def devicecode_url(self) -> str:
        return f"{LOGIN_ROOT}/{urllib.parse.quote(self.tenant)}/oauth2/v2.0/devicecode"

    @property
    def token_url(self) -> str:
        return f"{LOGIN_ROOT}/{urllib.parse.quote(self.tenant)}/oauth2/v2.0/token"


# ----- settings + storage ---------------------------------------------


def _settings() -> dict[str, Any]:
    store = current_app.config["SETTINGS_STORE"]
    section = store.get_section("plugins") or {}
    plugin_settings = section.get(PLUGIN_ID)
    return plugin_settings if isinstance(plugin_settings, dict) else {}


def app_config(settings: dict[str, Any] | None = None) -> AppConfig:
    """Resolve the Entra app registration, raising if it isn't configured yet."""
    s = _settings() if settings is None else settings
    client_id = str(s.get("client_id") or "").strip()
    tenant = str(s.get("tenant") or "").strip() or "common"
    if not client_id:
        raise OutlookAuthError(
            "Outlook Core has no application (client) ID yet. Add one in Settings → Plugins."
        )
    return AppConfig(client_id=client_id, tenant=tenant, read_shared=bool(s.get("read_shared")))


def _data_dir() -> Path:
    registry = current_app.config["PLUGIN_REGISTRY"]
    plugin = registry.get(PLUGIN_ID)
    if plugin is None:
        raise RuntimeError(f"{PLUGIN_ID} plugin not registered")
    path: Path = plugin.data_dir
    return path


def _resolve(cfg: AppConfig | None, data_dir: Path | None) -> tuple[AppConfig, Path]:
    return (cfg if cfg is not None else app_config()), (
        data_dir if data_dir is not None else _data_dir()
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any], *, private: bool = False) -> None:
    """Write atomically so a crash mid-write can't strand a half file, and
    keep credential files owner-only where the platform honours it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - platform dependent
            _log.debug("could not chmod %s", tmp)
    os.replace(tmp, path)


def _token_path(data_dir: Path) -> Path:
    return data_dir / "token.json"


def _device_code_path(data_dir: Path) -> Path:
    return data_dir / "device_code.json"


def _calendars_path(data_dir: Path) -> Path:
    return data_dir / "calendars.json"


def _view_path(data_dir: Path, key: str) -> Path:
    return data_dir / f"view_{key}.json"


def load_token(data_dir: Path) -> dict[str, Any]:
    return _read_json(_token_path(data_dir))


def save_token(data_dir: Path, token: dict[str, Any]) -> None:
    _write_json(_token_path(data_dir), token, private=True)


def clear_token(data_dir: Path) -> None:
    _token_path(data_dir).unlink(missing_ok=True)
    _device_code_path(data_dir).unlink(missing_ok=True)


# ----- HTTP -----------------------------------------------------------


def _json_body(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _post_form(url: str, form: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """POST a form-encoded body. Identity endpoints answer 400 with a JSON
    ``error`` for expected states (``authorization_pending``), so the status
    code comes back to the caller instead of raising."""
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
            return int(getattr(resp, "status", 200) or 200), _json_body(resp.read())
    except urllib.error.HTTPError as err:
        return int(err.code), _json_body(err.read())
    except urllib.error.URLError as err:
        raise OutlookApiError("Couldn't reach Microsoft sign-in right now.") from err
    except TimeoutError as err:
        raise OutlookApiError("Microsoft sign-in timed out.") from err


def _auth_error_message(payload: dict[str, Any], fallback: str) -> str:
    """Turn an identity-platform error body into something a person can act on."""
    code = str(payload.get("error") or "")
    known = {
        "invalid_client": (
            "Microsoft rejected the client ID. Check it in Settings → Plugins, and that "
            "'Allow public client flows' is enabled on the app registration."
        ),
        "unauthorized_client": (
            "The app registration isn't allowed to use the device-code flow. Enable "
            "'Allow public client flows' in its Authentication settings."
        ),
        "invalid_grant": "Outlook sign-in expired. Sign in again on the Outlook Core page.",
        "invalid_scope": "Microsoft rejected the requested permissions.",
        "consent_required": (
            "Your Microsoft account can't consent to reading shared calendars. Turn off "
            "'Read calendars shared with me' in Settings → Plugins, or ask an administrator "
            f"to grant {SHARED_SCOPE}."
        ),
        "authorization_declined": "The sign-in was declined.",
        "expired_token": "The sign-in code expired. Start again.",
    }
    if code in known:
        return known[code]
    description = str(payload.get("error_description") or "").splitlines()
    if code and description:
        _log.warning("outlook_core auth error %s: %s", code, description[0])
    return fallback


def _graph_error_message(status: int, payload: dict[str, Any]) -> str:
    detail = payload.get("error")
    if isinstance(detail, dict):
        _log.warning(
            "outlook_core graph error %s: %s %s",
            status,
            detail.get("code"),
            detail.get("message"),
        )
    if status == 401:
        return "Outlook sign-in expired. Sign in again on the Outlook Core page."
    if status == 403:
        return "Outlook denied access. Check the app has the Calendars.Read permission."
    if status == 404:
        return "That Outlook calendar no longer exists."
    if status in (429, 503):
        return "Outlook is rate-limiting us. Showing the last known schedule."
    return "Couldn't load your Outlook calendar right now."


# ----- auth -----------------------------------------------------------


def granted_scopes(token: dict[str, Any]) -> str:
    """The space-separated scopes a stored token was issued with."""
    return str(token.get("scope") or "").strip()


def has_shared_scope(token: dict[str, Any]) -> bool:
    """Whether this token may read calendars other people own.

    Compared case-insensitively: the identity platform echoes scopes back in
    whatever case it likes, and ``openid``/``profile``/``email`` ride along.
    """
    return SHARED_SCOPE.lower() in granted_scopes(token).lower().split()


def _token_from_payload(payload: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    """Build the stored token from a token-endpoint response.

    A refresh response may omit ``refresh_token``; when it does, the previous
    one stays valid, so carry it (and the resolved account name) forward.
    """
    expires_in = payload.get("expires_in")
    try:
        lifetime = int(expires_in)
    except (TypeError, ValueError):
        lifetime = 3600
    return {
        "access_token": str(payload.get("access_token") or ""),
        "refresh_token": str(payload.get("refresh_token") or previous.get("refresh_token") or ""),
        "expires_at": time.time() + lifetime,
        "scope": str(payload.get("scope") or previous.get("scope") or SCOPES),
        "account": str(previous.get("account") or ""),
    }


def start_device_code(cfg: AppConfig | None = None, data_dir: Path | None = None) -> dict[str, Any]:
    """Begin a sign-in. Returns the code and URL to show the user; the
    ``device_code`` itself stays on disk and never reaches the browser."""
    cfg, data_dir = _resolve(cfg, data_dir)
    status, payload = _post_form(
        cfg.devicecode_url, {"client_id": cfg.client_id, "scope": cfg.scopes}
    )
    if status >= 400 or not payload.get("device_code"):
        raise OutlookAuthError(_auth_error_message(payload, "Couldn't start the Outlook sign-in."))
    try:
        expires_in = int(payload.get("expires_in") or 900)
    except (TypeError, ValueError):
        expires_in = 900
    try:
        interval = int(payload.get("interval") or 5)
    except (TypeError, ValueError):
        interval = 5
    pending = {
        "device_code": str(payload["device_code"]),
        "user_code": str(payload.get("user_code") or ""),
        "verification_uri": str(payload.get("verification_uri") or ""),
        "message": str(payload.get("message") or ""),
        "interval": interval,
        "expires_at": time.time() + expires_in,
    }
    _write_json(_device_code_path(data_dir), pending, private=True)
    return {k: v for k, v in pending.items() if k != "device_code"}


def pending_device_code(data_dir: Path) -> dict[str, Any]:
    """The in-flight sign-in without its secret, or ``{}``."""
    pending = _read_json(_device_code_path(data_dir))
    if not pending:
        return {}
    return {k: v for k, v in pending.items() if k != "device_code"}


def poll_device_code(cfg: AppConfig | None = None, data_dir: Path | None = None) -> dict[str, Any]:
    """Ask the token endpoint once whether the user has finished signing in.

    Returns ``{"status": none|pending|signed_in|expired|declined|error"}``; the
    caller decides how often to come back (the flow's ``interval``).
    """
    cfg, data_dir = _resolve(cfg, data_dir)
    pending = _read_json(_device_code_path(data_dir))
    if not pending.get("device_code"):
        return {"status": "none"}
    if time.time() > float(pending.get("expires_at") or 0):
        _device_code_path(data_dir).unlink(missing_ok=True)
        return {"status": "expired", "message": "The sign-in code expired. Start again."}

    status, payload = _post_form(
        cfg.token_url,
        {
            "grant_type": DEVICE_CODE_GRANT,
            "client_id": cfg.client_id,
            "device_code": str(pending["device_code"]),
        },
    )
    if status < 400 and payload.get("access_token"):
        token = _token_from_payload(payload, previous={})
        token["account"] = _fetch_account_name(token["access_token"])
        save_token(data_dir, token)
        _device_code_path(data_dir).unlink(missing_ok=True)
        return {"status": "signed_in", "account": token["account"]}

    error = str(payload.get("error") or "")
    if error == "authorization_pending":
        return {"status": "pending", "interval": int(pending.get("interval") or 5)}
    if error == "slow_down":
        pending["interval"] = int(pending.get("interval") or 5) + 5
        _write_json(_device_code_path(data_dir), pending, private=True)
        return {"status": "pending", "interval": int(pending["interval"])}
    if error in ("authorization_declined", "expired_token", "bad_verification_code"):
        _device_code_path(data_dir).unlink(missing_ok=True)
        state = "declined" if error == "authorization_declined" else "expired"
        return {"status": state, "message": _auth_error_message(payload, "Sign-in failed.")}
    return {
        "status": "error",
        "message": _auth_error_message(payload, "Couldn't complete the Outlook sign-in."),
    }


def _refresh_token(cfg: AppConfig, data_dir: Path, token: dict[str, Any]) -> dict[str, Any]:
    refresh = str(token.get("refresh_token") or "")
    if not refresh:
        raise OutlookAuthError(
            "Not signed in to Outlook yet. Open Plugins → Outlook Core to sign in."
        )
    # Refresh with the scopes this token was actually granted, not with
    # whatever the current setting asks for. Widening the request on a refresh
    # is an error, so keying off a constant would sign people out the moment a
    # release (or a settings toggle) added a scope they never consented to.
    status, payload = _post_form(
        cfg.token_url,
        {
            "grant_type": "refresh_token",
            "client_id": cfg.client_id,
            "refresh_token": refresh,
            "scope": granted_scopes(token) or SCOPES,
        },
    )
    if status >= 400 or not payload.get("access_token"):
        raise OutlookAuthError(
            _auth_error_message(payload, "Couldn't refresh the Outlook sign-in.")
        )
    # Refresh tokens rotate: always persist what came back.
    fresh = _token_from_payload(payload, previous=token)
    save_token(data_dir, fresh)
    return fresh


def access_token(cfg: AppConfig | None = None, data_dir: Path | None = None) -> str:
    """A valid access token, refreshing when the cached one is near expiry."""
    cfg, data_dir = _resolve(cfg, data_dir)
    token = load_token(data_dir)
    if not token.get("refresh_token") and not token.get("access_token"):
        raise OutlookAuthError(
            "Not signed in to Outlook yet. Open Plugins → Outlook Core to sign in."
        )
    expires_at = float(token.get("expires_at") or 0)
    if token.get("access_token") and time.time() < expires_at - TOKEN_EXPIRY_SKEW_S:
        return str(token["access_token"])
    return str(_refresh_token(cfg, data_dir, token)["access_token"])


def _fetch_account_name(token: str) -> str:
    """Best-effort display name for the admin page. Never fails the sign-in:
    reading the id_token is explicitly discouraged, so ask /me instead."""
    try:
        payload = _graph_request(f"{GRAPH_ROOT}/me?$select=displayName,userPrincipalName", token)
    except OutlookError:
        return ""
    name = str(payload.get("displayName") or "").strip()
    upn = str(payload.get("userPrincipalName") or "").strip()
    if name and upn:
        return f"{name} ({upn})"
    return name or upn


# ----- Graph ----------------------------------------------------------


def _graph_request(url: str, token: str, *, timezone_name: str | None = None) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if timezone_name:
        headers["Prefer"] = f'outlook.timezone="{timezone_name}"'
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
            return _json_body(resp.read())
    except urllib.error.HTTPError as err:
        status = int(err.code)
        raise OutlookApiError(
            _graph_error_message(status, _json_body(err.read())), status=status
        ) from err
    except urllib.error.URLError as err:
        raise OutlookApiError("Couldn't reach Outlook right now.") from err
    except TimeoutError as err:
        raise OutlookApiError("Outlook took too long to answer.") from err


def _graph_collection(
    cfg: AppConfig,
    data_dir: Path,
    path: str,
    params: dict[str, str] | None = None,
    *,
    timezone_name: str | None = None,
) -> list[dict[str, Any]]:
    """GET a Graph collection, following ``@odata.nextLink`` up to MAX_PAGES."""
    url = f"{GRAPH_ROOT}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    token = access_token(cfg, data_dir)
    out: list[dict[str, Any]] = []
    pages = 0
    reauthed = False
    while url and pages < MAX_PAGES:
        try:
            payload = _graph_request(url, token, timezone_name=timezone_name)
        except OutlookApiError as err:
            # A token can be rejected before its stated expiry: revoked
            # session, password change, clock skew. Force one refresh and try
            # the same page again before giving up on the user.
            if reauthed or getattr(err, "status", None) != 401:
                raise
            reauthed = True
            token = _refresh_token(cfg, data_dir, load_token(data_dir))["access_token"]
            continue
        value = payload.get("value")
        if isinstance(value, list):
            out.extend(item for item in value if isinstance(item, dict))
        next_link = payload.get("@odata.nextLink")
        url = str(next_link) if isinstance(next_link, str) else ""
        pages += 1
    return out


def list_calendars(
    cfg: AppConfig | None = None, data_dir: Path | None = None, *, max_age_s: int = CALENDARS_TTL_S
) -> list[dict[str, Any]]:
    """The account's calendars as ``{id, name, is_default}``, cached an hour."""
    cfg, data_dir = _resolve(cfg, data_dir)
    cached = _read_json(_calendars_path(data_dir))
    if cached.get("calendars") is not None and _age_s(cached) < max_age_s:
        entries = cached["calendars"]
        return list(entries) if isinstance(entries, list) else []
    items = _graph_collection(
        cfg,
        data_dir,
        "/me/calendars",
        {"$select": "id,name,isDefaultCalendar", "$top": "100"},
    )
    calendars = [
        {
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or "Calendar"),
            "is_default": bool(item.get("isDefaultCalendar")),
        }
        for item in items
        if item.get("id")
    ]
    _write_json(
        _calendars_path(data_dir),
        {"v": CACHE_SCHEMA, "fetched_at": time.time(), "calendars": calendars},
    )
    return calendars


def forget_calendars(data_dir: Path) -> None:
    _calendars_path(data_dir).unlink(missing_ok=True)


# ----- events ---------------------------------------------------------


def _zone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def _zone_name(zone: tzinfo) -> str:
    """The IANA name Graph's ``Prefer: outlook.timezone`` header wants.

    ``str()`` on a fixed-offset tzinfo yields "UTC+02:00", which Graph doesn't
    recognise, so fall back to UTC and let it return UTC times we convert
    ourselves.
    """
    key = getattr(zone, "key", None)
    return str(key) if key else "UTC"


_FRACTION_RE = re.compile(r"(\.\d{1,6})\d*")


def _parse_graph_dt(raw: Any, zone: tzinfo) -> datetime | None:
    """Graph returns ``{"dateTime": "2026-08-20T09:30:00.0000000", "timeZone": "..."}``
    with seven fractional digits, which ``fromisoformat`` won't take."""
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("dateTime") or "").strip()
    if not text:
        return None
    text = _FRACTION_RE.sub(r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_zone(str(raw.get("timeZone") or "UTC")))
    return parsed.astimezone(zone)


def _normalise_event(
    raw: dict[str, Any], *, calendar: dict[str, str], zone: tzinfo
) -> dict[str, Any] | None:
    """One Graph event → the shape every outlook_* widget consumes, or None
    when it should never reach a panel (cancelled, or declined by the user)."""
    if raw.get("isCancelled"):
        return None
    response = str((raw.get("responseStatus") or {}).get("response") or "none")
    if response in DROPPED_RESPONSES:
        return None
    start = _parse_graph_dt(raw.get("start"), zone)
    end = _parse_graph_dt(raw.get("end"), zone)
    if start is None:
        return None
    if end is None:
        end = start
    location = str((raw.get("location") or {}).get("displayName") or "").strip()
    categories = [str(c) for c in (raw.get("categories") or []) if str(c).strip()]
    return {
        "id": str(raw.get("id") or ""),
        "summary": str(raw.get("subject") or "").strip() or "(no subject)",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "all_day": bool(raw.get("isAllDay")),
        "location": location,
        "show_as": str(raw.get("showAs") or "unknown"),
        "response": response,
        "categories": categories,
        "calendar_id": calendar["id"],
        "calendar_name": calendar["name"],
    }


def _age_s(cached: dict[str, Any]) -> float:
    return max(0.0, time.time() - float(cached.get("fetched_at") or 0))


def _snap_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Widen the fetched window to whole local days so the cache key is stable
    across a day's worth of renders instead of moving with the clock."""
    day_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = end.replace(hour=0, minute=0, second=0, microsecond=0)
    if day_end < end:  # a window ending mid-day still needs that whole day
        day_end = day_end + timedelta(days=1)
    return day_start, day_end


def _view_key(calendar_id: str, start: datetime, end: datetime) -> str:
    raw = f"{calendar_id}|{start.isoformat()}|{end.isoformat()}"
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _resolve_calendars(
    cfg: AppConfig, data_dir: Path, calendar_ids: list[str] | None
) -> list[dict[str, str]]:
    """Map requested ids to ``{id, name}``. Empty / None means the primary
    calendar; if the calendar list can't be reached we still fall back to it so
    a transient failure doesn't take the widget down."""
    wanted = [c for c in (calendar_ids or []) if str(c).strip()]
    try:
        known = list_calendars(cfg, data_dir)
    except OutlookApiError:
        known = []
    by_id = {c["id"]: c for c in known}
    if not wanted:
        default = next((c for c in known if c.get("is_default")), None)
        if default is not None:
            return [{"id": str(default["id"]), "name": str(default["name"])}]
        return [{"id": "", "name": "Calendar"}]
    return [
        {"id": str(cid), "name": str(by_id.get(cid, {}).get("name") or "Calendar")}
        for cid in wanted
    ]


def _calendar_view_path(calendar_id: str) -> str:
    if not calendar_id:
        return "/me/calendarView"
    return f"/me/calendars/{urllib.parse.quote(calendar_id, safe='')}/calendarView"


def _fetch_view(
    cfg: AppConfig,
    data_dir: Path,
    calendar: dict[str, str],
    start: datetime,
    end: datetime,
    zone: tzinfo,
    *,
    max_age_s: int = VIEW_TTL_S,
) -> tuple[list[dict[str, Any]], float, bool]:
    """One calendar's window: ``(events, fetched_at, stale)``.

    Serves a stale cache rather than an error when Graph is unreachable, which
    is the right trade for a panel that only repaints every few minutes. Raises
    only when there is nothing cached to fall back on.
    """
    path = _view_path(data_dir, _view_key(calendar["id"], start, end))
    cached = _read_json(path)
    fresh_enough = (
        cached.get("v") == CACHE_SCHEMA
        and isinstance(cached.get("events"), list)
        and _age_s(cached) < max_age_s
    )
    if fresh_enough:
        return list(cached["events"]), float(cached.get("fetched_at") or 0), False

    try:
        items = _graph_collection(
            cfg,
            data_dir,
            _calendar_view_path(calendar["id"]),
            {
                "startDateTime": start.isoformat(),
                "endDateTime": end.isoformat(),
                "$select": EVENT_SELECT,
                "$orderby": "start/dateTime",
                "$top": str(GRAPH_PAGE_SIZE),
            },
            timezone_name=_zone_name(zone),
        )
    except OutlookApiError:
        if isinstance(cached.get("events"), list):
            _log.warning("outlook_core: serving stale cache for calendar %r", calendar["name"])
            return list(cached["events"]), float(cached.get("fetched_at") or 0), True
        raise

    events = [
        event
        for event in (_normalise_event(item, calendar=calendar, zone=zone) for item in items)
        if event is not None
    ]
    now = time.time()
    _write_json(path, {"v": CACHE_SCHEMA, "fetched_at": now, "events": events})
    return events, now, False


def _overlaps(event: dict[str, Any], start: datetime, end: datetime) -> bool:
    try:
        ev_start = datetime.fromisoformat(str(event["start"]))
        ev_end = datetime.fromisoformat(str(event["end"]))
    except (KeyError, ValueError):
        return False
    return ev_end > start and ev_start < end


def load_events_detailed(
    calendar_ids: list[str] | None,
    start: datetime,
    end: datetime,
    *,
    cfg: AppConfig | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Events in ``[start, end)`` plus freshness metadata.

    ``{"events": [...], "stale": bool, "fetched_at": iso|None, "calendars": [...]}``.
    Widgets that only want the list can call :func:`load_events`.
    """
    cfg, data_dir = _resolve(cfg, data_dir)
    zone = start.tzinfo or _zone("UTC")
    fetch_start, fetch_end = _snap_window(start, end)
    calendars = _resolve_calendars(cfg, data_dir, calendar_ids)

    events: list[dict[str, Any]] = []
    oldest: float | None = None
    stale = False
    for calendar in calendars:
        found, fetched_at, was_stale = _fetch_view(
            cfg, data_dir, calendar, fetch_start, fetch_end, zone
        )
        events.extend(event for event in found if _overlaps(event, start, end))
        stale = stale or was_stale
        oldest = fetched_at if oldest is None else min(oldest, fetched_at)

    events.sort(key=lambda e: (not e["all_day"], str(e["start"])))
    return {
        "events": events,
        "stale": stale,
        "fetched_at": (
            datetime.fromtimestamp(oldest, tz=zone).isoformat() if oldest is not None else None
        ),
        "calendars": [c["name"] for c in calendars],
    }


def load_events(
    calendar_ids: list[str] | None,
    start: datetime,
    end: datetime,
    *,
    cfg: AppConfig | None = None,
    data_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Public API for the outlook_* widgets: events inside ``[start, end)``,
    all-day first then by start time. Cancelled and declined events are already
    filtered out."""
    detailed = load_events_detailed(calendar_ids, start, end, cfg=cfg, data_dir=data_dir)
    events: list[dict[str, Any]] = detailed["events"]
    return events


def status(cfg: AppConfig | None = None, data_dir: Path | None = None) -> dict[str, Any]:
    """What the admin page needs to render, without raising when unconfigured."""
    try:
        resolved_cfg = cfg if cfg is not None else app_config()
        configured = True
        problem = ""
    except OutlookAuthError as err:
        resolved_cfg = None
        configured = False
        problem = str(err)
    dd = data_dir if data_dir is not None else _data_dir()
    token = load_token(dd)
    signed_in = bool(token.get("refresh_token"))
    return {
        "configured": configured,
        "problem": problem,
        "tenant": resolved_cfg.tenant if resolved_cfg else "",
        "client_id": resolved_cfg.client_id if resolved_cfg else "",
        "signed_in": signed_in,
        "account": str(token.get("account") or ""),
        "expires_in_s": max(0, int(float(token.get("expires_at") or 0) - time.time())),
        "pending": pending_device_code(dd),
    }


# ----- host hooks -----------------------------------------------------


def choices(name: str) -> list[dict[str, str]]:
    """Dropdown contents for the widgets' ``choices_from: "calendars"``.

    The host calls ``choices()`` on the widget, which delegates here through
    the plugin registry. Errors render as an empty dropdown, so never raise.
    """
    if name != "calendars":
        return []
    try:
        return [
            {"value": c["id"], "label": c["name"] + (" (default)" if c["is_default"] else "")}
            for c in list_calendars()
        ]
    except (OutlookError, RuntimeError) as err:
        _log.info("outlook_core: no calendar choices available (%s)", err)
        return []


def blueprint() -> Blueprint:
    bp = Blueprint("outlook_core_admin", __name__, template_folder="templates")

    @bp.get("/")
    def index() -> str:
        state = status()
        calendars: list[dict[str, Any]] = []
        if state["signed_in"]:
            try:
                calendars = list_calendars()
            except OutlookError as err:
                flash(str(err), "error")
        return render_template(
            "outlook_core/index.html",
            status=state,
            calendars=calendars,
            scopes=SCOPES.split(),
        )

    @bp.post("/signin")
    def signin() -> Response:
        try:
            pending = start_device_code()
        except OutlookError as err:
            flash(str(err), "error")
            return redirect(url_for(".index"))
        flash(
            f"Go to {pending['verification_uri']} and enter the code {pending['user_code']}.",
            "success",
        )
        return redirect(url_for(".index"))

    @bp.post("/poll")
    def poll() -> Response:
        try:
            result = poll_device_code()
        except OutlookError as err:
            flash(str(err), "error")
            return redirect(url_for(".index"))
        state = result.get("status")
        if state == "signed_in":
            flash(f"Signed in as {result.get('account') or 'your Outlook account'}.", "success")
        elif state == "pending":
            flash("Still waiting for the sign-in to complete.", "info")
        elif state == "none":
            flash("No sign-in is in progress.", "info")
        else:
            flash(str(result.get("message") or "Sign-in failed."), "error")
        return redirect(url_for(".index"))

    @bp.post("/signout")
    def signout() -> Response:
        data_dir = _data_dir()
        clear_token(data_dir)
        forget_calendars(data_dir)
        flash("Signed out of Outlook.", "success")
        return redirect(url_for(".index"))

    @bp.post("/refresh-calendars")
    def refresh_calendars() -> Response:
        forget_calendars(_data_dir())
        flash("Calendar list refreshed.", "success")
        return redirect(url_for(".index"))

    return bp
