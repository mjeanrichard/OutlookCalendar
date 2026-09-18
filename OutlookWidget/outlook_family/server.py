"""outlook_family, the coming days as a family timetable.

Everything Graph-shaped (tokens, paging, caching, filtering) lives in
``outlook_core``, and so does the family itself: who exists, and which rule
decides that ``L: Klavier`` is Lea's. This module only picks the window, asks
the core to resolve ownership, groups by local day, and turns failures into a
sentence the cell can show.

Plugins can't import each other, so the core is reached through the live
plugin registry — the same route ``outlook_week`` takes.

The window **always starts today**. A family panel answers "what is coming",
so a fixed calendar week that slowly empties out as the week wears on is the
wrong shape; the first column is today and the rest follow.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.tz_resolve import app_timezone
from flask import current_app

CORE_ID = "outlook_core"
DEFAULT_DAYS = 7
MIN_DAYS = 3
MAX_DAYS = 7
DEFAULT_HOUR_START = 7
DEFAULT_HOUR_END = 21
CORE_MISSING = "Outlook Core plugin is not installed."


def _core() -> Any:
    """The outlook_core server module, or None when it isn't loaded."""
    registry = current_app.config.get("PLUGIN_REGISTRY")
    plugin = registry.get(CORE_ID) if registry is not None else None
    if plugin is None:
        return None
    return plugin.server_module


def _int_option(options: dict[str, Any], name: str, default: int) -> int:
    """Cell options arrive as whatever the editor stored, including "" and
    None once a field has been cleared."""
    raw = options.get(name, default)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _list_option(options: dict[str, Any], name: str) -> list[str]:
    raw = options.get(name)
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _hours(options: dict[str, Any]) -> dict[str, int]:
    """The visible window, always at least one hour wide.

    Deliberately not auto-fitted to the week's events: a grid that changes
    height whenever someone books a 06:00 flight reads as instability on a
    panel you walk past. Out-of-window events become edge markers in the
    client instead.
    """
    start = max(0, min(23, _int_option(options, "hour_start", DEFAULT_HOUR_START)))
    end = max(1, min(24, _int_option(options, "hour_end", DEFAULT_HOUR_END)))
    if end <= start:
        end = min(24, start + 1)
    return {"start": start, "end": end}


def _day_span(event: dict[str, Any], start: datetime, days: int) -> tuple[int, int] | None:
    """The window day indices an event covers, clipped, or None if it misses.

    An event that began before the window still belongs on the panel — a
    holiday that started last Sunday is exactly the thing you want to see on
    Monday — so the span is clipped to the window rather than the event being
    matched on its start date alone.
    """
    try:
        ev_start = datetime.fromisoformat(str(event["start"]))
        ev_end = datetime.fromisoformat(str(event["end"]))
    except (KeyError, ValueError):
        return None
    first = (ev_start.date() - start.date()).days
    # An event ending exactly at midnight belongs to the day before, not to a
    # sliver of the next one.
    last_dt = ev_end - timedelta(microseconds=1) if ev_end > ev_start else ev_end
    last = (last_dt.date() - start.date()).days
    first, last = max(0, first), min(days - 1, last)
    if last < first:
        return None
    return first, last


def _lasts_all_day(event: dict[str, Any]) -> bool:
    """True for a timed event covering 24 hours or more.

    The threshold is a full day rather than "touches two dates", so an
    overnight train at 22:00 still draws as a block on both of its days —
    that is real, readable timing — while a multi-day trip becomes a bar.
    """
    try:
        start = datetime.fromisoformat(str(event["start"]))
        end = datetime.fromisoformat(str(event["end"]))
    except (KeyError, ValueError):
        return False
    return (end - start) >= timedelta(days=1)


def _group_by_day(
    events: list[dict[str, Any]], start: datetime, days: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(days, bands)``: one bucket per day, plus the all-day bars.

    Every day in the window gets a bucket whether or not anything is in it — a
    week that silently loses Wednesday because nobody had anything on is worse
    than an empty column.

    All-day events are pulled out into ``bands`` carrying the day indices they
    span, so the client draws one bar across three columns instead of the same
    label three times. Timed events land in every day they touch; the client
    clamps each copy to that day's own hours.

    A timed event lasting a day or more is banded too. A trip that runs Sunday
    afternoon to Friday morning is not "busy 07:00-21:00" on the Wednesday in
    between — drawn as a block it fills the column and pushes out the
    appointments that day actually has.
    """
    today = start.date()
    buckets: list[dict[str, Any]] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        buckets.append(
            {
                "date": day.date().isoformat(),
                "weekday": day.strftime("%A"),
                "day": day.day,
                "is_today": day.date() == today,
                "is_weekend": day.weekday() >= 5,
                "events": [],
            }
        )

    bands: list[dict[str, Any]] = []
    for event in events:
        span = _day_span(event, start, days)
        if span is None:
            continue
        first, last = span
        if event.get("all_day") or _lasts_all_day(event):
            bands.append({**event, "first": first, "last": last})
            continue
        for index in range(first, last + 1):
            buckets[index]["events"].append(event)
    return buckets, bands


def _visible(events: list[dict[str, Any]], wanted: list[str]) -> list[dict[str, Any]]:
    """Filter to the selected members.

    Unassigned events (``members == []``) always survive: they are drawn grey
    rather than hidden, because an event disappearing from the panel because a
    rule stopped firing is exactly the failure nobody would notice.
    """
    if not wanted:
        return events
    keep = set(wanted)
    return [e for e in events if not e.get("members") or keep & set(e["members"])]


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    if core is None:
        return {"error": CORE_MISSING, "days": [], "count": 0}

    days = max(MIN_DAYS, min(MAX_DAYS, _int_option(options, "days", DEFAULT_DAYS)))
    hours = _hours(options)

    zone = app_timezone()
    now = datetime.now(zone)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)

    registry = current_app.config["PLUGIN_REGISTRY"]
    plugin = registry.get(CORE_ID)
    try:
        result = core.load_events_detailed(
            _list_option(options, "calendars"),
            start,
            end,
            data_dir=Path(plugin.data_dir),
        )
        events = core.resolve_events(list(result.get("events") or []))
        roster = core.family_roster()
    except core.OutlookError as err:
        return {"error": str(err), "days": [], "count": 0}
    except Exception as err:  # a widget must never take the whole page down
        return {
            "error": f"Couldn't load your Outlook calendar ({type(err).__name__}).",
            "days": [],
            "count": 0,
        }

    wanted = _list_option(options, "members")
    events = _visible(events, wanted)
    if wanted:
        keep = set(wanted)
        roster = [m for m in roster if m["id"] in keep]

    buckets, bands = _group_by_day(events, start, days)
    # No clock in the payload (D39): the render must be a pure function of
    # the calendar and the date, so an unchanged day answers the panel's
    # poll with 304 instead of a full repaint.
    return {
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "days": buckets,
        "bands": bands,
        "count": len(events),
        "members": roster,
        "hours": hours,
        "layout": str(options.get("layout") or "grid"),
        "routine_gutter": options.get("routine_gutter", True) is not False,
        "show_location": bool(options.get("show_location")),
        "stale": bool(result.get("stale")),
        "fetched_at": result.get("fetched_at"),
        "calendars": result.get("calendars") or [],
    }


def choices(name: str) -> list[dict[str, str]]:
    """The host calls choices() on this widget, never on the core, so both the
    calendar and the family-member dropdowns delegate through the registry."""
    core = _core()
    core_choices = getattr(core, "choices", None) if core is not None else None
    if not callable(core_choices):
        return []
    return list(core_choices(name))
