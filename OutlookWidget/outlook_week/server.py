"""outlook_week, the next few days of the signed-in Outlook calendar.

All Graph work (tokens, paging, caching, filtering) lives in ``outlook_core``;
this module only decides the window, groups what comes back by local day, and
turns failures into a sentence the cell can show. Plugins can't import each
other, so the core is reached through the live plugin registry, the same way
``calendar_day`` reaches ``calendar_core``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.tz_resolve import app_timezone
from flask import current_app

CORE_ID = "outlook_core"
DEFAULT_DAYS_AHEAD = 7
MAX_DAYS_AHEAD = 31
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


def _calendar_ids(options: dict[str, Any]) -> list[str]:
    raw = options.get("calendars")
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _group_by_day(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket events into local days, preserving the core's ordering
    (all-day first, then by start) inside each bucket."""
    days: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for event in events:
        try:
            start = datetime.fromisoformat(str(event["start"]))
        except (KeyError, ValueError):
            continue
        key = start.date().isoformat()
        day = index.get(key)
        if day is None:
            day = {
                "date": key,
                "weekday": start.strftime("%A"),
                "events": [],
            }
            index[key] = day
            days.append(day)
        day["events"].append(event)
    days.sort(key=lambda d: str(d["date"]))
    return days


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    if core is None:
        return {"error": CORE_MISSING, "days": [], "count": 0}

    days_ahead = max(1, min(MAX_DAYS_AHEAD, _int_option(options, "days_ahead", DEFAULT_DAYS_AHEAD)))
    max_events = max(0, _int_option(options, "max_events", 0))

    zone = app_timezone()
    now = datetime.now(zone)
    # Whole days: a week view that dropped this morning's finished meetings
    # would renumber itself mid-morning, which reads as a glitch on a panel.
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days_ahead)

    registry = current_app.config["PLUGIN_REGISTRY"]
    plugin = registry.get(CORE_ID)
    try:
        result = core.load_events_detailed(
            _calendar_ids(options),
            start,
            end,
            data_dir=Path(plugin.data_dir),
        )
    except core.OutlookError as err:
        return {"error": str(err), "days": [], "count": 0}
    except Exception as err:  # a widget must never take the whole page down
        return {
            "error": f"Couldn't load your Outlook calendar ({type(err).__name__}).",
            "days": [],
            "count": 0,
        }

    events: list[dict[str, Any]] = list(result.get("events") or [])
    if max_events > 0:
        events = events[:max_events]
    return {
        "now": now.isoformat(),
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "days": _group_by_day(events),
        "count": len(events),
        "stale": bool(result.get("stale")),
        "fetched_at": result.get("fetched_at"),
        "calendars": result.get("calendars") or [],
    }


def choices(name: str) -> list[dict[str, str]]:
    """The host calls choices() on this widget, never on the core, so the
    calendar dropdown delegates through the registry."""
    core = _core()
    core_choices = getattr(core, "choices", None) if core is not None else None
    if not callable(core_choices):
        return []
    return list(core_choices(name))
