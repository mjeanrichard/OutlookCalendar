"""outlook_family: window, grouping, member filtering, and failure paths."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from flask import Flask

CALENDARS_URL = "/me/calendars?"
VIEW_URL = "calendarView"
ZURICH = ZoneInfo("Europe/Zurich")

MEMBERS = [
    {"name": "Papa", "letter": "P", "accent": 5, "pattern": "solid"},
    {"name": "Lea", "letter": "L", "accent": 3, "pattern": "dots"},
    {"name": "Nils", "letter": "N", "accent": 2, "pattern": "horiz"},
]
RULES = [
    {"id": "prefix", "kind": "prefix", "strip": True},
    {"id": "buero", "kind": "contains", "value": "Büro", "members": ["papa"], "routine": True},
]


def _graph_event(day: int, hour: int, subject: str, **over: Any) -> dict[str, Any]:
    """A Graph event on 2026-08-<day> at <hour>:00 Zurich time."""
    raw = {
        "id": f"evt-{day}-{hour}-{subject[:4]}",
        "subject": subject,
        "start": {
            "dateTime": f"2026-08-{day:02d}T{hour:02d}:00:00.0000000",
            "timeZone": "Europe/Zurich",
        },
        "end": {
            "dateTime": f"2026-08-{day:02d}T{hour + 1:02d}:00:00.0000000",
            "timeZone": "Europe/Zurich",
        },
        "isAllDay": False,
        "location": {"displayName": ""},
        "showAs": "busy",
        "responseStatus": {"response": "accepted"},
        "categories": [],
        "isCancelled": False,
    }
    raw.update(over)
    return raw


def _all_day(first: int, last: int, subject: str) -> dict[str, Any]:
    """An all-day event covering 2026-08-<first> through <last> inclusive.
    Graph reports the end as midnight on the day *after* the last one."""
    return {
        "id": f"band-{first}-{last}",
        "subject": subject,
        "start": {"dateTime": f"2026-08-{first:02d}T00:00:00.0000000", "timeZone": "Europe/Zurich"},
        "end": {
            "dateTime": f"2026-08-{last + 1:02d}T00:00:00.0000000",
            "timeZone": "Europe/Zurich",
        },
        "isAllDay": True,
        "location": {"displayName": ""},
        "showAs": "free",
        "responseStatus": {"response": "accepted"},
        "categories": [],
        "isCancelled": False,
    }


@pytest.fixture
def now(monkeypatch: pytest.MonkeyPatch, fam: Any) -> datetime:
    """Freeze "today" at 2026-08-21 14:30 Zurich. Mid-afternoon on purpose:
    the first column must still start at midnight."""
    frozen = datetime(2026, 8, 21, 14, 30, tzinfo=ZURICH)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return frozen if tz is None else frozen.astimezone(tz)

    monkeypatch.setattr(fam, "datetime", FrozenDatetime)
    monkeypatch.setattr(fam, "app_timezone", lambda: ZURICH)
    return frozen


@pytest.fixture
def graph(fake_http: Any, signed_in: Any, app: Flask) -> Any:
    app.config["SETTINGS_STORE"].patch_section(
        "plugins", {"outlook_core": {"client_id": "cid", "tenant": "common"}}
    )
    fake_http.add(
        CALENDARS_URL,
        {"value": [{"id": "cal-primary", "name": "Calendar", "isDefaultCalendar": True}]},
    )
    return fake_http


@pytest.fixture
def family(core: Any, core_dir: Path) -> dict[str, Any]:
    return core.save_family({"members": MEMBERS, "rules": RULES}, core_dir)


def _fetch(fam: Any, **options: Any) -> dict[str, Any]:
    return fam.fetch(options, {}, ctx={})


# ----- window ---------------------------------------------------------


def test_the_window_starts_today_at_midnight(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        data = _fetch(fam)

    assert data["start"] == "2026-08-21"
    assert data["end"] == "2026-08-28"
    assert [day["date"] for day in data["days"]][:2] == ["2026-08-21", "2026-08-22"]
    assert data["days"][0]["is_today"] is True


@pytest.mark.parametrize(("asked", "shown"), [(3, 3), (5, 5), (7, 7), (0, 3), (99, 7), ("", 7)])
def test_the_day_count_is_clamped_to_what_the_layout_can_hold(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any, asked: Any, shown: int
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        data = _fetch(fam, days=asked)

    assert len(data["days"]) == shown


def test_every_day_gets_a_column_even_when_nothing_is_on(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    """A week that silently loses Wednesday is worse than an empty column."""
    graph.add(VIEW_URL, {"value": [_graph_event(21, 16, "L: Klavier")]})

    with app.test_request_context():
        data = _fetch(fam)

    assert len(data["days"]) == 7
    assert [len(day["events"]) for day in data["days"]] == [1, 0, 0, 0, 0, 0, 0]


def test_the_hour_window_is_fixed_and_never_inverted(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        assert _fetch(fam)["hours"] == {"start": 7, "end": 21}
        assert _fetch(fam, hour_start=9, hour_end=18)["hours"] == {"start": 9, "end": 18}
        # An end at or before the start would divide by zero in the client.
        assert _fetch(fam, hour_start=20, hour_end=8)["hours"] == {"start": 20, "end": 21}


def test_the_weekend_is_marked_so_the_client_can_tint_it(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        data = _fetch(fam)

    # 2026-08-21 is a Friday, so days 1 and 2 are the weekend.
    assert [day["is_weekend"] for day in data["days"]] == [
        False,
        True,
        True,
        False,
        False,
        False,
        False,
    ]


# ----- ownership ------------------------------------------------------


def test_events_arrive_resolved_with_the_prefix_stripped(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_graph_event(21, 16, "L: Klavier")]})

    with app.test_request_context():
        data = _fetch(fam)

    event = data["days"][0]["events"][0]
    assert event["members"] == ["lea"]
    assert event["title"] == "Klavier"
    assert event["summary"] == "L: Klavier"


def test_a_routine_rule_reaches_the_client(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_graph_event(21, 8, "Büro")]})

    with app.test_request_context():
        data = _fetch(fam)

    assert data["days"][0]["events"][0]["routine"] is True


def test_the_roster_rides_along_so_the_client_can_paint(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        data = _fetch(fam)

    assert [m["id"] for m in data["members"]] == ["papa", "lea", "nils"]
    assert data["members"][1]["pattern"] == "dots"


def test_picking_members_narrows_both_the_events_and_the_roster(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(
        VIEW_URL,
        {"value": [_graph_event(21, 16, "L: Klavier"), _graph_event(21, 17, "N: Fussball")]},
    )

    with app.test_request_context():
        data = _fetch(fam, members=["lea"])

    assert [e["title"] for e in data["days"][0]["events"]] == ["Klavier"]
    assert [m["id"] for m in data["members"]] == ["lea"]


def test_an_unclaimed_event_is_still_shown(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    """An event vanishing because a rule stopped firing is the failure nobody
    would notice, so it survives even a member filter."""
    graph.add(VIEW_URL, {"value": [_graph_event(21, 16, "Zahnarzt")]})

    with app.test_request_context():
        data = _fetch(fam, members=["lea"])

    event = data["days"][0]["events"][0]
    assert event["members"] == []
    assert event["title"] == "Zahnarzt"


# ----- bands ----------------------------------------------------------


def test_an_all_day_event_becomes_a_band_spanning_its_days(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_all_day(22, 23, "Grosseltern")]})

    with app.test_request_context():
        data = _fetch(fam)

    assert len(data["bands"]) == 1
    assert (data["bands"][0]["first"], data["bands"][0]["last"]) == (1, 2)
    # It belongs to the band, not to any day's timed column.
    assert all(not day["events"] for day in data["days"])


def test_a_holiday_that_started_before_today_still_shows(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    """Matching on the start date alone would drop it — exactly the event you
    most want to see on the panel."""
    graph.add(VIEW_URL, {"value": [_all_day(17, 24, "Sommerferien")]})

    with app.test_request_context():
        data = _fetch(fam)

    assert (data["bands"][0]["first"], data["bands"][0]["last"]) == (0, 3)


def test_a_multi_day_trip_becomes_a_band_instead_of_filling_every_column(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    """Drawn as a block, a Sunday-to-Wednesday trip claims 07:00-21:00 on the
    Monday and Tuesday in between and pushes out what those days really hold."""
    trip = _graph_event(23, 16, "Geschäftsreise")
    trip["end"] = {"dateTime": "2026-08-26T10:00:00.0000000", "timeZone": "Europe/Zurich"}
    graph.add(VIEW_URL, {"value": [trip]})

    with app.test_request_context():
        data = _fetch(fam)

    assert (data["bands"][0]["first"], data["bands"][0]["last"]) == (2, 5)
    assert all(not day["events"] for day in data["days"])


def test_a_timed_event_running_past_midnight_reaches_both_days(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any
) -> None:
    """Under a day, so it stays a block: 22:00 to 07:00 is real, readable
    timing, unlike a trip that is simply "on" for four days."""
    overnight = _graph_event(21, 22, "Nachtzug")
    overnight["end"] = {"dateTime": "2026-08-22T07:00:00.0000000", "timeZone": "Europe/Zurich"}
    graph.add(VIEW_URL, {"value": [overnight]})

    with app.test_request_context():
        data = _fetch(fam)

    assert [len(day["events"]) for day in data["days"]][:3] == [1, 1, 0]


# ----- failure paths --------------------------------------------------


def test_a_signed_out_account_returns_a_sentence_not_a_traceback(
    app: Flask, fam: Any, now: datetime, fake_http: Any
) -> None:
    app.config["SETTINGS_STORE"].patch_section("plugins", {"outlook_core": {"client_id": "cid"}})

    with app.test_request_context():
        data = _fetch(fam)

    assert data["error"].startswith("Not signed in to Outlook")
    assert data["days"] == []


def test_a_missing_core_says_so(app: Flask, fam: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fam, "_core", lambda: None)

    with app.test_request_context():
        data = _fetch(fam)

    assert data["error"] == fam.CORE_MISSING


def test_an_unexpected_failure_never_takes_the_page_down(
    app: Flask, fam: Any, now: datetime, graph: Any, family: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app.test_request_context():
        registry = app.config["PLUGIN_REGISTRY"]
        module = registry.get("outlook_core").server_module

        def boom(*args: Any, **kwargs: Any) -> None:
            raise ValueError("something went sideways")

        monkeypatch.setattr(module, "load_events_detailed", boom)
        data = _fetch(fam)

    assert data["error"] == "Couldn't load your Outlook calendar (ValueError)."
    assert data["count"] == 0


# ----- host hooks -----------------------------------------------------


def test_choices_delegates_both_dropdowns_to_the_core(
    app: Flask, fam: Any, graph: Any, family: Any
) -> None:
    with app.test_request_context():
        calendars = fam.choices("calendars")
        members = fam.choices("family_members")
        unknown = fam.choices("nonsense")

    assert calendars[0]["value"] == "cal-primary"
    assert [m["value"] for m in members] == ["papa", "lea", "nils"]
    assert unknown == []
