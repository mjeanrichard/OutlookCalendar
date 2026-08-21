"""outlook_week: option handling, day grouping, and failure paths."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from flask import Flask

CALENDARS_URL = "/me/calendars?"
VIEW_URL = "calendarView"
ZURICH = ZoneInfo("Europe/Zurich")


def _graph_event(day: int, hour: int, subject: str, **over: Any) -> dict[str, Any]:
    """A Graph event on 2026-08-<day> at <hour>:00 local time."""
    raw = {
        "id": f"evt-{day}-{hour}",
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


@pytest.fixture
def now(monkeypatch: pytest.MonkeyPatch, week: Any) -> datetime:
    """Freeze "today" at 2026-08-20 14:30 Zurich, mid-afternoon on purpose:
    the week view must still start at midnight."""
    frozen = datetime(2026, 8, 20, 14, 30, tzinfo=ZURICH)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return frozen if tz is None else frozen.astimezone(tz)

    monkeypatch.setattr(week, "datetime", FrozenDatetime)
    monkeypatch.setattr(week, "app_timezone", lambda: ZURICH)
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


def _fetch(app: Flask, week: Any, **options: Any) -> dict[str, Any]:
    with app.test_request_context():
        return week.fetch(options, {}, ctx={})


# ----- option coercion ------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 7), ("", 7), ("5", 5), (3, 3), ("nonsense", 7), (0, 1), (999, 31)],
)
def test_days_ahead_is_coerced_and_clamped(
    app: Flask, week: Any, graph: Any, now: datetime, raw: Any, expected: int
) -> None:
    graph.add(VIEW_URL, {"value": []})

    data = _fetch(app, week, days_ahead=raw)

    span = datetime.fromisoformat(data["end"]) - datetime.fromisoformat(data["start"])
    assert span == timedelta(days=expected)


def test_the_window_starts_at_midnight_not_now(
    app: Flask, week: Any, graph: Any, now: datetime
) -> None:
    graph.add(VIEW_URL, {"value": []})

    data = _fetch(app, week)

    assert data["start"] == "2026-08-20"
    assert data["end"] == "2026-08-27"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ([], []),
        (["cal-work"], ["cal-work"]),
        ("cal-a, cal-b", ["cal-a", "cal-b"]),
        ([" cal-c ", ""], ["cal-c"]),
    ],
)
def test_calendar_ids_are_normalised(week: Any, raw: Any, expected: list[str]) -> None:
    assert week._calendar_ids({"calendars": raw}) == expected


# ----- grouping -------------------------------------------------------


def test_events_are_grouped_by_local_day(app: Flask, week: Any, graph: Any, now: datetime) -> None:
    graph.add(
        VIEW_URL,
        {
            "value": [
                _graph_event(20, 16, "Retro"),
                _graph_event(20, 9, "Standup"),
                _graph_event(22, 11, "One-to-one"),
            ]
        },
    )

    data = _fetch(app, week)

    assert data["count"] == 3
    assert [day["date"] for day in data["days"]] == ["2026-08-20", "2026-08-22"]
    assert [day["weekday"] for day in data["days"]] == ["Thursday", "Saturday"]
    assert [e["summary"] for e in data["days"][0]["events"]] == ["Standup", "Retro"]


def test_all_day_events_lead_their_day(app: Flask, week: Any, graph: Any, now: datetime) -> None:
    all_day = _graph_event(
        21,
        0,
        "Company holiday",
        isAllDay=True,
        start={"dateTime": "2026-08-21T00:00:00.0000000", "timeZone": "Europe/Zurich"},
        end={"dateTime": "2026-08-22T00:00:00.0000000", "timeZone": "Europe/Zurich"},
    )
    graph.add(VIEW_URL, {"value": [_graph_event(21, 9, "Standup"), all_day]})

    data = _fetch(app, week)

    assert [e["summary"] for e in data["days"][0]["events"]] == ["Company holiday", "Standup"]


def test_max_events_caps_the_list(app: Flask, week: Any, graph: Any, now: datetime) -> None:
    graph.add(
        VIEW_URL,
        {"value": [_graph_event(20, hour, f"Meeting {hour}") for hour in (9, 11, 13, 15)]},
    )

    data = _fetch(app, week, max_events=2)

    assert data["count"] == 2
    assert [e["summary"] for e in data["days"][0]["events"]] == ["Meeting 9", "Meeting 11"]


def test_an_empty_week_is_not_an_error(app: Flask, week: Any, graph: Any, now: datetime) -> None:
    graph.add(VIEW_URL, {"value": []})

    data = _fetch(app, week)

    assert data == {
        "now": "2026-08-20T14:30:00+02:00",
        "start": "2026-08-20",
        "end": "2026-08-27",
        "days": [],
        "count": 0,
        "stale": False,
        "fetched_at": data["fetched_at"],
        "calendars": ["Calendar"],
    }
    assert "error" not in data


def test_the_payload_carries_the_fields_the_layout_will_need(
    app: Flask, week: Any, graph: Any, now: datetime
) -> None:
    graph.add(
        VIEW_URL,
        {
            "value": [
                _graph_event(
                    20,
                    9,
                    "Sprint review",
                    location={"displayName": "Room 4.12"},
                    showAs="tentative",
                    responseStatus={"response": "tentativelyAccepted"},
                    categories=["Customer", "Travel"],
                )
            ]
        },
    )

    event = _fetch(app, week)["days"][0]["events"][0]

    assert event["location"] == "Room 4.12"
    assert event["show_as"] == "tentative"
    assert event["response"] == "tentativelyAccepted"
    assert event["categories"] == ["Customer", "Travel"]


# ----- failure paths --------------------------------------------------


def test_a_signed_out_account_reads_as_a_sentence(
    app: Flask, week: Any, fake_http: Any, now: datetime
) -> None:
    app.config["SETTINGS_STORE"].patch_section("plugins", {"outlook_core": {"client_id": "cid"}})

    data = _fetch(app, week)

    assert data["error"].startswith("Not signed in to Outlook")
    assert data["days"] == []
    assert fake_http.requests == []


def test_an_unconfigured_install_reads_as_a_sentence(app: Flask, week: Any, now: datetime) -> None:
    data = _fetch(app, week)

    assert "client) ID" in data["error"]
    assert data["count"] == 0


def test_a_missing_core_plugin_is_reported(
    app: Flask, week: Any, monkeypatch: pytest.MonkeyPatch, now: datetime
) -> None:
    monkeypatch.setattr(week, "_core", lambda: None)

    data = _fetch(app, week)

    assert data["error"] == week.CORE_MISSING


def test_an_unexpected_error_does_not_escape_fetch(
    app: Flask, week: Any, core: Any, monkeypatch: pytest.MonkeyPatch, now: datetime
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise ValueError("something odd")

    monkeypatch.setattr(core, "load_events_detailed", boom)

    data = _fetch(app, week)

    assert data["error"] == "Couldn't load your Outlook calendar (ValueError)."
    assert data["days"] == []


def test_stale_core_data_is_flagged_through(
    app: Flask, week: Any, core: Any, monkeypatch: pytest.MonkeyPatch, now: datetime
) -> None:
    monkeypatch.setattr(
        core,
        "load_events_detailed",
        lambda *a, **k: {
            "events": [],
            "stale": True,
            "fetched_at": "2026-08-20T09:00:00+02:00",
            "calendars": ["Calendar"],
        },
    )

    data = _fetch(app, week)

    assert data["stale"] is True
    assert data["fetched_at"] == "2026-08-20T09:00:00+02:00"


# ----- choices --------------------------------------------------------


def test_choices_delegate_to_the_core(app: Flask, week: Any, graph: Any, core_dir: Any) -> None:
    with app.test_request_context():
        options = week.choices("calendars")

    assert options == [{"value": "cal-primary", "label": "Calendar (default)"}]


def test_choices_are_empty_without_the_core(
    app: Flask, week: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(week, "_core", lambda: None)

    with app.test_request_context():
        assert week.choices("calendars") == []
