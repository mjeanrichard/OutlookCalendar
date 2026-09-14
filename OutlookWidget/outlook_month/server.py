"""outlook_month, the coming weeks as a family wall calendar.

Everything Graph-shaped (tokens, paging, caching, filtering) lives in
``outlook_core``, and so does the family itself. This module only picks the
window, asks the core to resolve ownership, drops routine, groups by local
day, and turns failures into a sentence the cell can show.

Plugins can't import each other, so the core is reached through the live
plugin registry — the same route ``outlook_week`` and ``outlook_family`` take.

The window **starts with the current week**, not the first of the month. A
calendar month is mostly history by the 20th; a grid that begins on this
week's Monday has at most six days behind it, and those are drawn as such.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.tz_resolve import app_timezone
from flask import current_app

CORE_ID = "outlook_core"
DEFAULT_WEEKS = 4
MIN_WEEKS = 2
MAX_WEEKS = 6
WEEK_STARTS = ("monday", "sunday")
PAST_DAYS = ("hollow", "painted")
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


def _choice_option(options: dict[str, Any], name: str, allowed: tuple[str, ...]) -> str:
    raw = str(options.get(name) or "").strip().lower()
    return raw if raw in allowed else allowed[0]


def _list_option(options: dict[str, Any], name: str) -> list[str]:
    raw = options.get(name)
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _week_start(today: datetime, week_start: str) -> datetime:
    """Midnight on the first day of the week ``today`` falls in."""
    # Python's Monday is 0; a Sunday-start week just shifts that by one.
    offset = (today.weekday() + (1 if week_start == "sunday" else 0)) % 7
    return today.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=offset)


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
    """True for a timed event covering 24 hours or more — a band, not a chip."""
    try:
        start = datetime.fromisoformat(str(event["start"]))
        end = datetime.fromisoformat(str(event["end"]))
    except (KeyError, ValueError):
        return False
    return (end - start) >= timedelta(days=1)


def _group_by_day(
    events: list[dict[str, Any]], start: datetime, days: int, today: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(days, bands)``: one bucket per day, plus the multi-day bars.

    Every day in the window gets a bucket whether or not anything is in it.
    All-day events, and timed events lasting a day or more, are pulled out
    into ``bands`` carrying the day indices they span, so the client draws
    one bar across the days instead of the same chip three times. A shorter
    timed event that crosses midnight lands in both days it touches — an
    overnight train is genuinely on both.
    """
    buckets: list[dict[str, Any]] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        buckets.append(
            {
                "date": day.date().isoformat(),
                "weekday": day.strftime("%A"),
                "day": day.day,
                "is_today": day.date() == today.date(),
                "is_past": day.date() < today.date(),
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


def _merge_same_title(
    events: list[dict[str, Any]], order: dict[str, int], key: Any = None
) -> list[dict[str, Any]]:
    """Fold events with the same title into one carrying every owner.

    Without times on the panel, ``L: Klavier`` and ``N: Klavier`` on the same
    day say nothing a single ``Klavier`` chip with two marks does not, and the
    same event on two people's calendars is the same thing twice. The title is
    compared after the rules have stripped it, case-insensitively; ``key``
    adds to what must match (a band's span). The first event's fields are
    kept, its members become the union in roster order (so stripe segments
    put the same person in the same place as everywhere else), and routine
    survives only if every copy was routine.
    """
    merged: list[dict[str, Any]] = []
    by_key: dict[Any, dict[str, Any]] = {}
    for event in events:
        title = str(event.get("title") or event.get("summary") or "").strip().casefold()
        k = (title, key(event) if key else None)
        seen = by_key.get(k)
        if seen is None:
            by_key[k] = {**event, "members": list(event.get("members") or [])}
            merged.append(by_key[k])
            continue
        members = set(seen["members"]) | set(event.get("members") or [])
        seen["members"] = sorted(members, key=lambda m: (order.get(m, len(order)), m))
        seen["routine"] = bool(seen.get("routine")) and bool(event.get("routine"))
        seen["merged"] = seen.get("merged", 1) + 1
    return merged


def _visible(events: list[dict[str, Any]], wanted: list[str]) -> list[dict[str, Any]]:
    """Filter to the selected members.

    Unassigned events (``members == []``) always survive: they are drawn
    plain rather than hidden, because an event disappearing from the panel
    because a rule stopped firing is exactly the failure nobody would notice.
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
        return {"error": CORE_MISSING, "days": [], "bands": [], "count": 0}

    weeks = max(MIN_WEEKS, min(MAX_WEEKS, _int_option(options, "weeks", DEFAULT_WEEKS)))
    week_start = _choice_option(options, "week_start", WEEK_STARTS)
    past_days = _choice_option(options, "past_days", PAST_DAYS)
    show_routine = options.get("show_routine") is True

    zone = app_timezone()
    now = datetime.now(zone)
    start = _week_start(now, week_start)
    days = weeks * 7
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
        return {"error": str(err), "days": [], "bands": [], "count": 0}
    except Exception as err:  # a widget must never take the whole page down
        return {
            "error": f"Couldn't load your Outlook calendar ({type(err).__name__}).",
            "days": [],
            "bands": [],
            "count": 0,
        }

    wanted = _list_option(options, "members")
    events = _visible(events, wanted)
    full_roster = roster
    if wanted:
        keep = set(wanted)
        roster = [m for m in roster if m["id"] in keep]
    # Routine is a daily commitment: on a month it says nothing and crowds
    # out what does, so it is dropped here rather than drawn small.
    if not show_routine:
        events = [e for e in events if not e.get("routine")]

    buckets, bands = _group_by_day(events, start, days, now)
    order = {m["id"]: i for i, m in enumerate(full_roster)}
    for bucket in buckets:
        bucket["events"] = _merge_same_title(bucket["events"], order)
    bands = _merge_same_title(bands, order, key=lambda b: (b["first"], b["last"]))
    return {
        "now": now.isoformat(),
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "weeks": weeks,
        "week_start": week_start,
        "past_days": past_days,
        "days": buckets,
        "bands": bands,
        "count": len(events),
        "members": roster,
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
