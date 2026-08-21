"""outlook_core: what happens when Graph rejects a token mid-flight.

An access token can stop working before its stated expiry (revoked session,
password change, clock skew), so a 401 buys exactly one forced refresh.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

CALENDARS_URL = "/me/calendars?"
TOKEN_URL = "oauth2/v2.0/token"
ZURICH = ZoneInfo("Europe/Zurich")


def _window() -> tuple[datetime, datetime]:
    start = datetime(2026, 8, 20, 0, 0, tzinfo=ZURICH)
    return start, start + timedelta(days=7)


@pytest.fixture
def fresh_token(core: Any, core_dir: Path) -> None:
    """Valid on paper, so nothing refreshes until Graph says otherwise."""
    core.save_token(
        core_dir,
        {
            "access_token": "at-stale",
            "refresh_token": "rt-1",
            "expires_at": time.time() + 3600,
            "account": "Ada",
        },
    )


def test_a_401_triggers_exactly_one_refresh_then_succeeds(
    core: Any, cfg: Any, core_dir: Path, fresh_token: None, fake_http: Any
) -> None:
    fake_http.add(CALENDARS_URL, {"error": {"code": "InvalidAuthenticationToken"}}, status=401)
    fake_http.add(
        CALENDARS_URL, {"value": [{"id": "c1", "name": "Calendar", "isDefaultCalendar": True}]}
    )
    fake_http.add(
        TOKEN_URL, {"access_token": "at-fresh", "refresh_token": "rt-2", "expires_in": 3600}
    )

    calendars = core.list_calendars(cfg, core_dir)

    assert [c["name"] for c in calendars] == ["Calendar"]
    assert fake_http.calls_matching(TOKEN_URL) == 1
    assert core.load_token(core_dir)["access_token"] == "at-fresh"
    # The retry used the new token, not the rejected one.
    assert fake_http.last_request("/me/calendars").get_header("Authorization") == "Bearer at-fresh"


def test_a_second_401_gives_up_with_a_readable_message(
    core: Any, cfg: Any, core_dir: Path, fresh_token: None, fake_http: Any
) -> None:
    fake_http.add(CALENDARS_URL, {"error": {"code": "InvalidAuthenticationToken"}}, status=401)
    fake_http.add(
        TOKEN_URL, {"access_token": "at-fresh", "refresh_token": "rt-2", "expires_in": 3600}
    )

    with pytest.raises(core.OutlookApiError) as excinfo:
        core.list_calendars(cfg, core_dir)

    assert "Sign in again" in str(excinfo.value)
    assert excinfo.value.status == 401
    assert fake_http.calls_matching(TOKEN_URL) == 1  # one retry, not a loop


def test_other_statuses_are_not_retried(
    core: Any, cfg: Any, core_dir: Path, fresh_token: None, fake_http: Any
) -> None:
    fake_http.add(CALENDARS_URL, {"error": {"code": "serviceUnavailable"}}, status=503)

    with pytest.raises(core.OutlookApiError) as excinfo:
        core.list_calendars(cfg, core_dir)

    assert excinfo.value.status == 503
    assert fake_http.calls_matching(TOKEN_URL) == 0


def test_a_401_during_paging_still_recovers(
    core: Any, cfg: Any, core_dir: Path, fresh_token: None, fake_http: Any
) -> None:
    fake_http.add(
        CALENDARS_URL,
        {"value": [{"id": "cal-primary", "name": "Calendar", "isDefaultCalendar": True}]},
    )
    fake_http.add(
        "calendarView",
        {
            "value": [],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/page2calendarView",
        },
    )
    fake_http.add("calendarView", {"error": {"code": "InvalidAuthenticationToken"}}, status=401)
    fake_http.add("calendarView", {"value": []})
    fake_http.add(
        "oauth2/v2.0/token",
        {"access_token": "at-fresh", "refresh_token": "rt-2", "expires_in": 3600},
    )
    start, end = _window()

    events = core.load_events(None, start, end, cfg=cfg, data_dir=core_dir)

    assert events == []
    assert fake_http.calls_matching(TOKEN_URL) == 1
