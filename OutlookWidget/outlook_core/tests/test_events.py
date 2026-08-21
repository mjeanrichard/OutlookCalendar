"""outlook_core: calendar listing, event normalisation, windowing, caching."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

CALENDARS_URL = "/me/calendars?"
VIEW_URL = "calendarView"
ZURICH = ZoneInfo("Europe/Zurich")


def _event(**over: Any) -> dict[str, Any]:
    """A Graph event as calendarView returns it, seven fractional digits and all."""
    raw = {
        "id": "evt-1",
        "subject": "Sprint review",
        "start": {"dateTime": "2026-08-20T09:30:00.0000000", "timeZone": "Europe/Zurich"},
        "end": {"dateTime": "2026-08-20T10:00:00.0000000", "timeZone": "Europe/Zurich"},
        "isAllDay": False,
        "location": {"displayName": "Room 4.12"},
        "showAs": "busy",
        "responseStatus": {"response": "accepted", "time": "2026-08-01T10:00:00Z"},
        "categories": ["Team"],
        "isCancelled": False,
    }
    raw.update(over)
    return raw


def _calendars_payload() -> dict[str, Any]:
    return {
        "value": [
            {"id": "cal-primary", "name": "Calendar", "isDefaultCalendar": True},
            {"id": "cal-work", "name": "Work", "isDefaultCalendar": False},
        ]
    }


def _window() -> tuple[datetime, datetime]:
    start = datetime(2026, 8, 20, 0, 0, tzinfo=ZURICH)
    return start, start + timedelta(days=7)


@pytest.fixture
def graph(fake_http: Any) -> Any:
    """A signed-in account whose calendar list is already answerable."""
    fake_http.add(CALENDARS_URL, _calendars_payload())
    return fake_http


# ----- calendars ------------------------------------------------------


def test_list_calendars_maps_and_caches(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, graph: Any
) -> None:
    calendars = core.list_calendars(cfg, core_dir)

    assert calendars == [
        {"id": "cal-primary", "name": "Calendar", "is_default": True},
        {"id": "cal-work", "name": "Work", "is_default": False},
    ]
    core.list_calendars(cfg, core_dir)
    assert graph.calls_matching("/me/calendars") == 1  # second call served from disk


def test_forget_calendars_forces_a_refetch(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, graph: Any
) -> None:
    core.list_calendars(cfg, core_dir)
    core.forget_calendars(core_dir)
    core.list_calendars(cfg, core_dir)

    assert graph.calls_matching("/me/calendars") == 2


def test_choices_are_empty_for_an_unknown_key(core: Any) -> None:
    assert core.choices("nope") == []


def test_choices_label_the_default_calendar(
    core: Any, app: Any, core_dir: Path, signed_in: Any, graph: Any, monkeypatch: Any
) -> None:
    monkeypatch.setitem(app.config["SETTINGS_STORE"].get_section("plugins"), "outlook_core", {})
    with app.test_request_context():
        app.config["SETTINGS_STORE"].patch_section(
            "plugins", {"outlook_core": {"client_id": "cid"}}
        )
        options = core.choices("calendars")

    assert options == [
        {"value": "cal-primary", "label": "Calendar (default)"},
        {"value": "cal-work", "label": "Work"},
    ]


def test_choices_stay_empty_when_nothing_is_configured(core: Any, app: Any) -> None:
    with app.test_request_context():
        assert core.choices("calendars") == []


# ----- normalisation --------------------------------------------------


def test_normalise_keeps_the_fields_widgets_render(core: Any) -> None:
    event = core._normalise_event(
        _event(), calendar={"id": "cal-primary", "name": "Calendar"}, zone=ZURICH
    )

    assert event == {
        "id": "evt-1",
        "summary": "Sprint review",
        "start": "2026-08-20T09:30:00+02:00",
        "end": "2026-08-20T10:00:00+02:00",
        "all_day": False,
        "location": "Room 4.12",
        "show_as": "busy",
        "response": "accepted",
        "categories": ["Team"],
        "calendar_id": "cal-primary",
        "calendar_name": "Calendar",
    }


@pytest.mark.parametrize(
    "over",
    [
        {"isCancelled": True},
        {"responseStatus": {"response": "declined"}},
    ],
    ids=["cancelled", "declined"],
)
def test_normalise_drops_events_that_should_never_reach_a_panel(
    core: Any, over: dict[str, Any]
) -> None:
    assert (
        core._normalise_event(_event(**over), calendar={"id": "c", "name": "Calendar"}, zone=ZURICH)
        is None
    )


@pytest.mark.parametrize("response", ["accepted", "tentativelyAccepted", "organizer", "none"])
def test_normalise_keeps_every_other_response(core: Any, response: str) -> None:
    event = core._normalise_event(
        _event(responseStatus={"response": response}),
        calendar={"id": "c", "name": "Calendar"},
        zone=ZURICH,
    )
    assert event is not None
    assert event["response"] == response


def test_normalise_converts_into_the_requested_zone(core: Any) -> None:
    event = core._normalise_event(
        _event(start={"dateTime": "2026-08-20T07:30:00.0000000", "timeZone": "UTC"}),
        calendar={"id": "c", "name": "Calendar"},
        zone=ZURICH,
    )
    assert event is not None
    assert event["start"] == "2026-08-20T09:30:00+02:00"


def test_normalise_handles_missing_pieces(core: Any) -> None:
    event = core._normalise_event(
        {
            "id": "evt-2",
            "start": {"dateTime": "2026-08-21T00:00:00.0000000", "timeZone": "Europe/Zurich"},
            "isAllDay": True,
        },
        calendar={"id": "c", "name": "Calendar"},
        zone=ZURICH,
    )

    assert event is not None
    assert event["summary"] == "(no subject)"
    assert event["location"] == ""
    assert event["categories"] == []
    assert event["show_as"] == "unknown"
    assert event["all_day"] is True
    assert event["end"] == event["start"]  # no end given, treated as instantaneous


def test_normalise_rejects_an_unparseable_start(core: Any) -> None:
    assert (
        core._normalise_event(
            _event(start={"dateTime": "not a date"}),
            calendar={"id": "c", "name": "Calendar"},
            zone=ZURICH,
        )
        is None
    )


def test_unknown_timezone_falls_back_to_utc(core: Any) -> None:
    event = core._normalise_event(
        _event(start={"dateTime": "2026-08-20T07:30:00", "timeZone": "Middle Earth/Shire"}),
        calendar={"id": "c", "name": "Calendar"},
        zone=ZURICH,
    )
    assert event is not None
    assert event["start"] == "2026-08-20T09:30:00+02:00"


# ----- windowing ------------------------------------------------------


def test_snap_window_leaves_whole_days_alone(core: Any) -> None:
    start, end = _window()
    assert core._snap_window(start, end) == (start, end)


def test_snap_window_extends_a_part_day(core: Any) -> None:
    start = datetime(2026, 8, 20, 14, 30, tzinfo=ZURICH)
    end = datetime(2026, 8, 22, 9, 15, tzinfo=ZURICH)

    snapped_start, snapped_end = core._snap_window(start, end)

    assert snapped_start == datetime(2026, 8, 20, 0, 0, tzinfo=ZURICH)
    assert snapped_end == datetime(2026, 8, 23, 0, 0, tzinfo=ZURICH)


def test_zone_name_prefers_the_iana_key(core: Any) -> None:
    assert core._zone_name(ZURICH) == "Europe/Zurich"


def test_zone_name_falls_back_for_a_fixed_offset(core: Any) -> None:
    from datetime import timezone

    assert core._zone_name(timezone(timedelta(hours=2))) == "UTC"


# ----- loading --------------------------------------------------------


def test_load_events_queries_graph_and_filters_to_the_window(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, graph: Any
) -> None:
    outside = _event(
        id="evt-late",
        subject="Next month",
        start={"dateTime": "2026-09-20T09:00:00.0000000", "timeZone": "Europe/Zurich"},
        end={"dateTime": "2026-09-20T10:00:00.0000000", "timeZone": "Europe/Zurich"},
    )
    graph.add(VIEW_URL, {"value": [_event(), outside]})
    start, end = _window()

    events = core.load_events(None, start, end, cfg=cfg, data_dir=core_dir)

    assert [e["summary"] for e in events] == ["Sprint review"]
    url = graph.last_request(VIEW_URL).full_url
    assert "/me/calendars/cal-primary/calendarView" in url
    assert "startDateTime=2026-08-20T00%3A00%3A00%2B02%3A00" in url
    assert "endDateTime=2026-08-27T00%3A00%3A00%2B02%3A00" in url
    assert "showAs" in url and "categories" in url
    assert graph.last_request(VIEW_URL).get_header("Prefer") == 'outlook.timezone="Europe/Zurich"'


def test_load_events_sorts_all_day_first(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, graph: Any
) -> None:
    timed = _event(id="a", subject="Standup")
    all_day = _event(
        id="b",
        subject="Public holiday",
        isAllDay=True,
        start={"dateTime": "2026-08-21T00:00:00.0000000", "timeZone": "Europe/Zurich"},
        end={"dateTime": "2026-08-22T00:00:00.0000000", "timeZone": "Europe/Zurich"},
    )
    graph.add(VIEW_URL, {"value": [timed, all_day]})
    start, end = _window()

    events = core.load_events(None, start, end, cfg=cfg, data_dir=core_dir)

    assert [e["summary"] for e in events] == ["Public holiday", "Standup"]


def test_load_events_follows_paging(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, graph: Any
) -> None:
    graph.add(
        VIEW_URL,
        {
            "value": [_event(id="p1", subject="Page one")],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/nextcalendarViewpage",
        },
    )
    graph.add(VIEW_URL, {"value": [_event(id="p2", subject="Page two")]})
    start, end = _window()

    events = core.load_events(None, start, end, cfg=cfg, data_dir=core_dir)

    assert {e["summary"] for e in events} == {"Page one", "Page two"}


def test_load_events_reads_the_named_calendars(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, graph: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_event()]})
    start, end = _window()

    events = core.load_events(["cal-work"], start, end, cfg=cfg, data_dir=core_dir)

    assert events[0]["calendar_name"] == "Work"
    assert "/me/calendars/cal-work/calendarView" in graph.last_request(VIEW_URL).full_url


def test_second_load_inside_the_ttl_is_served_from_cache(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, graph: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_event()]})
    start, end = _window()

    core.load_events(None, start, end, cfg=cfg, data_dir=core_dir)
    core.load_events(None, start, end, cfg=cfg, data_dir=core_dir)

    assert graph.calls_matching("calendarView") == 1
    assert list(core_dir.glob("view_*.json"))


def test_a_stale_cache_beats_an_error(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, graph: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_event()]})
    start, end = _window()
    core.load_events(None, start, end, cfg=cfg, data_dir=core_dir)

    # Age the cache past its TTL, then have Graph fall over.
    cache_file = next(iter(core_dir.glob("view_*.json")))
    cached = json.loads(cache_file.read_text(encoding="utf-8"))
    cached["fetched_at"] = time.time() - core.VIEW_TTL_S - 60
    cache_file.write_text(json.dumps(cached), encoding="utf-8")
    graph.routes.clear()  # calendars are cached on disk; only the view is asked for now
    graph.add(VIEW_URL, {"error": {"code": "serviceUnavailable"}}, status=503)

    result = core.load_events_detailed(None, start, end, cfg=cfg, data_dir=core_dir)

    assert [e["summary"] for e in result["events"]] == ["Sprint review"]
    assert result["stale"] is True
    assert result["fetched_at"] is not None


def test_an_error_with_no_cache_surfaces(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, graph: Any
) -> None:
    graph.add(VIEW_URL, {"error": {"code": "serviceUnavailable"}}, status=503)
    start, end = _window()

    with pytest.raises(core.OutlookApiError) as excinfo:
        core.load_events(None, start, end, cfg=cfg, data_dir=core_dir)

    assert "rate-limiting" in str(excinfo.value)


def test_a_missing_calendar_reads_as_such(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, graph: Any
) -> None:
    graph.add(VIEW_URL, {"error": {"code": "ErrorItemNotFound"}}, status=404)
    start, end = _window()

    with pytest.raises(core.OutlookApiError) as excinfo:
        core.load_events(["cal-work"], start, end, cfg=cfg, data_dir=core_dir)

    assert "no longer exists" in str(excinfo.value)


def test_load_falls_back_to_the_primary_calendar_when_the_list_fails(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, fake_http: Any
) -> None:
    fake_http.add(CALENDARS_URL, {"error": {"code": "serviceUnavailable"}}, status=503)
    fake_http.add(VIEW_URL, {"value": [_event()]})
    start, end = _window()

    events = core.load_events(None, start, end, cfg=cfg, data_dir=core_dir)

    assert events[0]["calendar_name"] == "Calendar"
    assert "/me/calendarView" in fake_http.last_request(VIEW_URL).full_url


def test_load_events_detailed_reports_the_calendars_it_read(
    core: Any, cfg: Any, core_dir: Path, signed_in: Any, graph: Any
) -> None:
    graph.add(VIEW_URL, {"value": [_event()]})
    start, end = _window()

    result = core.load_events_detailed(
        ["cal-primary", "cal-work"], start, end, cfg=cfg, data_dir=core_dir
    )

    assert result["calendars"] == ["Calendar", "Work"]
    assert result["stale"] is False


def test_status_describes_an_unconfigured_install(core: Any, app: Any) -> None:
    with app.test_request_context():
        state = core.status()

    assert state["configured"] is False
    assert state["signed_in"] is False
    assert "client) ID" in state["problem"]


def test_status_describes_a_signed_in_install(
    core: Any, app: Any, core_dir: Path, signed_in: Any
) -> None:
    with app.test_request_context():
        app.config["SETTINGS_STORE"].patch_section(
            "plugins", {"outlook_core": {"client_id": "cid", "tenant": "common"}}
        )
        state = core.status()

    assert state["configured"] is True
    assert state["signed_in"] is True
    assert state["account"] == "Test User (test@example.com)"
    assert state["expires_in_s"] > 0
