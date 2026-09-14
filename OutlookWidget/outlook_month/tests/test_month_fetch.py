"""outlook_month: window, grouping, routine, member filtering, and failure paths."""

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


def _graph_event(day: int, hour: int, subject: str, month: int = 8, **over: Any) -> dict[str, Any]:
    """A Graph event on 2026-<month>-<day> at <hour>:00 Zurich time."""
    raw = {
        "id": f"evt-{month}-{day}-{hour}-{subject[:4]}",
        "subject": subject,
        "start": {
            "dateTime": f"2026-{month:02d}-{day:02d}T{hour:02d}:00:00.0000000",
            "timeZone": "Europe/Zurich",
        },
        "end": {
            "dateTime": f"2026-{month:02d}-{day:02d}T{hour + 1:02d}:00:00.0000000",
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
def now(monkeypatch: pytest.MonkeyPatch, month: Any) -> datetime:
    """Freeze "today" at Friday 2026-08-21 14:30 Zurich, so the current week
    started on Monday the 17th and four of its days are already over."""
    frozen = datetime(2026, 8, 21, 14, 30, tzinfo=ZURICH)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return frozen if tz is None else frozen.astimezone(tz)

    monkeypatch.setattr(month, "datetime", FrozenDatetime)
    monkeypatch.setattr(month, "app_timezone", lambda: ZURICH)
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


def _fetch(month: Any, **options: Any) -> dict[str, Any]:
    return month.fetch(options, {}, ctx={})


def _flat(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for day in data["days"] for e in day["events"]]


# ----- window ---------------------------------------------------------


def test_the_window_starts_on_this_weeks_monday(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        data = _fetch(month)

    assert data["start"] == "2026-08-17"
    assert data["end"] == "2026-09-14"
    assert len(data["days"]) == 28
    assert data["days"][0]["weekday"] == "Monday"
    # The Graph window is asked for from Monday, not from today: the four
    # days already over are on the panel too.
    assert "startDateTime=2026-08-17T00%3A00" in graph.last_request(VIEW_URL).full_url


def test_a_sunday_start_shifts_the_window_back_a_day(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        data = _fetch(month, week_start="sunday")

    assert data["start"] == "2026-08-16"
    assert data["days"][0]["weekday"] == "Sunday"
    assert data["week_start"] == "sunday"


def test_an_unknown_week_start_falls_back_to_monday(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        data = _fetch(month, week_start="thursday")

    assert data["start"] == "2026-08-17"
    assert data["week_start"] == "monday"


@pytest.mark.parametrize(("asked", "shown"), [(2, 2), (4, 4), (6, 6), (0, 2), (99, 6), ("", 4)])
def test_the_week_count_is_clamped_to_what_the_grid_can_hold(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any, asked: Any, shown: int
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        data = _fetch(month, weeks=asked)

    assert data["weeks"] == shown
    assert len(data["days"]) == shown * 7


def test_days_know_whether_they_are_over_today_or_ahead(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        data = _fetch(month)

    week = data["days"][:7]
    assert [d["is_past"] for d in week] == [True, True, True, True, False, False, False]
    assert [d["is_today"] for d in week] == [False, False, False, False, True, False, False]
    assert [d["is_weekend"] for d in week] == [False] * 5 + [True, True]
    # Only the first week can hold days that are over.
    assert not any(d["is_past"] for d in data["days"][7:])


def test_the_past_days_choice_reaches_the_client(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        assert _fetch(month)["past_days"] == "hollow"
        assert _fetch(month, past_days="painted")["past_days"] == "painted"
        assert _fetch(month, past_days="invisible")["past_days"] == "hollow"


def test_every_day_gets_a_cell_even_when_nothing_is_on(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_graph_event(21, 16, "L: Klavier")]})

    with app.test_request_context():
        data = _fetch(month)

    counts = [len(day["events"]) for day in data["days"]]
    assert counts[4] == 1
    assert sum(counts) == 1
    assert data["count"] == 1


# ----- ownership and routine ----------------------------------------------


def test_events_arrive_resolved_with_the_prefix_stripped(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_graph_event(21, 16, "L: Klavier")]})

    with app.test_request_context():
        data = _fetch(month)

    event = data["days"][4]["events"][0]
    assert event["members"] == ["lea"]
    assert event["title"] == "Klavier"
    assert event["summary"] == "L: Klavier"


def test_routine_is_dropped_unless_asked_for(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    """A daily commitment on every cell of a month says nothing and crowds
    out what does."""
    graph.add(
        VIEW_URL,
        {"value": [_graph_event(21, 8, "Büro"), _graph_event(21, 16, "L: Klavier")]},
    )

    with app.test_request_context():
        default = _fetch(month)
        shown = _fetch(month, show_routine=True)

    assert [e["title"] for e in _flat(default)] == ["Klavier"]
    assert default["count"] == 1
    assert [e["title"] for e in _flat(shown)] == ["Büro", "Klavier"]
    assert _flat(shown)[0]["routine"] is True


def test_the_roster_rides_along_so_the_client_can_paint(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": []})

    with app.test_request_context():
        data = _fetch(month)

    assert [m["id"] for m in data["members"]] == ["papa", "lea", "nils"]
    assert data["members"][1]["pattern"] == "dots"


def test_picking_members_narrows_both_the_events_and_the_roster(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(
        VIEW_URL,
        {"value": [_graph_event(21, 16, "L: Klavier"), _graph_event(21, 17, "N: Fussball")]},
    )

    with app.test_request_context():
        data = _fetch(month, members=["lea"])

    assert [e["title"] for e in _flat(data)] == ["Klavier"]
    assert [m["id"] for m in data["members"]] == ["lea"]


def test_an_unclaimed_event_is_still_shown(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_graph_event(21, 16, "Zahnarzt")]})

    with app.test_request_context():
        data = _fetch(month, members=["lea"])

    event = _flat(data)[0]
    assert event["members"] == []
    assert event["title"] == "Zahnarzt"


# ----- bands ----------------------------------------------------------


def test_an_all_day_event_becomes_a_band_spanning_its_days(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_all_day(22, 23, "Grosseltern")]})

    with app.test_request_context():
        data = _fetch(month)

    assert len(data["bands"]) == 1
    # Saturday and Sunday of the first week.
    assert (data["bands"][0]["first"], data["bands"][0]["last"]) == (5, 6)
    assert not _flat(data)


def test_a_band_crossing_a_week_boundary_keeps_its_full_span(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    """The client cuts it per week row; the server hands over one span."""
    graph.add(VIEW_URL, {"value": [_all_day(22, 26, "Herbstferien")]})

    with app.test_request_context():
        data = _fetch(month)

    assert (data["bands"][0]["first"], data["bands"][0]["last"]) == (5, 9)


def test_a_band_running_past_the_window_is_clipped_to_it(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_all_day(10, 30, "Sommerferien")]})

    with app.test_request_context():
        data = _fetch(month, weeks=2)

    # The window is the 17th to the 30th; the band fills all fourteen cells.
    assert (data["bands"][0]["first"], data["bands"][0]["last"]) == (0, 13)


def test_a_multi_day_trip_becomes_a_band(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    trip = _graph_event(23, 16, "Geschäftsreise")
    trip["end"] = {"dateTime": "2026-08-26T10:00:00.0000000", "timeZone": "Europe/Zurich"}
    graph.add(VIEW_URL, {"value": [trip]})

    with app.test_request_context():
        data = _fetch(month)

    assert (data["bands"][0]["first"], data["bands"][0]["last"]) == (6, 9)
    assert not _flat(data)


def test_a_timed_event_running_past_midnight_reaches_both_days(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    overnight = _graph_event(21, 22, "Nachtzug")
    overnight["end"] = {"dateTime": "2026-08-22T07:00:00.0000000", "timeZone": "Europe/Zurich"}
    graph.add(VIEW_URL, {"value": [overnight]})

    with app.test_request_context():
        data = _fetch(month)

    assert [len(day["events"]) for day in data["days"]][3:7] == [0, 1, 1, 0]


def test_routine_bands_are_dropped_too(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_all_day(24, 28, "Büro Homeoffice")]})

    with app.test_request_context():
        data = _fetch(month)

    assert data["bands"] == []


# ----- failure paths --------------------------------------------------


def test_a_signed_out_account_returns_a_sentence_not_a_traceback(
    app: Flask, month: Any, now: datetime, fake_http: Any
) -> None:
    app.config["SETTINGS_STORE"].patch_section("plugins", {"outlook_core": {"client_id": "cid"}})

    with app.test_request_context():
        data = _fetch(month)

    assert data["error"].startswith("Not signed in to Outlook")
    assert data["days"] == []
    assert data["bands"] == []


def test_a_missing_core_says_so(app: Flask, month: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(month, "_core", lambda: None)

    with app.test_request_context():
        data = _fetch(month)

    assert data["error"] == month.CORE_MISSING
    assert data["days"] == []


def test_choices_delegate_to_the_core(app: Flask, month: Any, core: Any, core_dir: Path) -> None:
    core.save_family({"members": MEMBERS, "rules": []}, core_dir)

    with app.test_request_context():
        members = month.choices("family_members")

    assert [m["value"] for m in members] == ["papa", "lea", "nils"]


# ----- merging --------------------------------------------------------


def test_same_title_on_the_same_day_becomes_one_chip_with_every_owner(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    """Without times, ``L: Klavier`` and ``N: Klavier`` say nothing a single
    ``Klavier`` with two marks does not."""
    graph.add(
        VIEW_URL,
        {"value": [_graph_event(21, 16, "N: Klavier"), _graph_event(21, 17, "L: Klavier")]},
    )

    with app.test_request_context():
        data = _fetch(month)

    events = _flat(data)
    assert [e["title"] for e in events] == ["Klavier"]
    # Roster order, not arrival order.
    assert events[0]["members"] == ["lea", "nils"]
    assert events[0]["merged"] == 2


def test_merging_ignores_case_but_not_the_day(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(
        VIEW_URL,
        {
            "value": [
                _graph_event(21, 16, "L: Zahnarzt"),
                _graph_event(21, 17, "N: zahnarzt"),
                _graph_event(22, 16, "P: Zahnarzt"),
            ]
        },
    )

    with app.test_request_context():
        data = _fetch(month)

    assert [len(day["events"]) for day in data["days"]][4:6] == [1, 1]
    assert data["days"][4]["events"][0]["members"] == ["lea", "nils"]
    assert data["days"][5]["events"][0]["members"] == ["papa"]


def test_the_same_event_on_two_calendars_is_shown_once(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    """An unowned copy folds into the owned one rather than sitting next to it
    as a second, white chip."""
    graph.add(
        VIEW_URL,
        {"value": [_graph_event(21, 16, "Elternabend"), _graph_event(21, 16, "P: Elternabend")]},
    )

    with app.test_request_context():
        data = _fetch(month)

    events = _flat(data)
    assert len(events) == 1
    assert events[0]["members"] == ["papa"]


def test_bands_merge_only_when_they_cover_the_same_days(
    app: Flask, month: Any, now: datetime, graph: Any, family: Any
) -> None:
    graph.add(
        VIEW_URL,
        {
            "value": [
                _all_day(22, 23, "L: Grosseltern"),
                _all_day(22, 23, "N: Grosseltern"),
                _all_day(22, 24, "P: Grosseltern"),
            ]
        },
    )

    with app.test_request_context():
        data = _fetch(month)

    spans = sorted((b["first"], b["last"], tuple(b["members"])) for b in data["bands"])
    assert spans == [(5, 6, ("lea", "nils")), (5, 7, ("papa",))]
