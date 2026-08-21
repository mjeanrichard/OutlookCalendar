"""outlook_family: it renders at every supported size, with no network in sight.

The test-render route stamps the fetch() payload onto the cell as ``data-data``
and client.js paints from there in the browser, so asserting on that attribute
is how the server side proves the whole data path ran.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

CALENDARS_URL = "/me/calendars?"
GRAPH_FORMAT = "%Y-%m-%dT%H:%M:%S.0000000"

MEMBERS = [
    {"name": "Papa", "letter": "P", "accent": 5, "pattern": "solid"},
    {"name": "Mama", "letter": "M", "accent": 6, "pattern": "diag"},
    {"name": "Lea", "letter": "L", "accent": 3, "pattern": "dots"},
]
RULES = [{"id": "prefix", "kind": "prefix", "strip": True}]


def _graph_event(start: datetime, subject: str) -> dict[str, Any]:
    return {
        "id": f"evt-{subject[:6]}",
        "subject": subject,
        "start": {"dateTime": start.strftime(GRAPH_FORMAT), "timeZone": str(start.tzinfo)},
        "end": {
            "dateTime": (start + timedelta(hours=1)).strftime(GRAPH_FORMAT),
            "timeZone": str(start.tzinfo),
        },
        "isAllDay": False,
        "location": {"displayName": "Musikschule"},
        "showAs": "busy",
        "responseStatus": {"response": "accepted"},
        "categories": [],
        "isCancelled": False,
    }


@pytest.fixture
def outlook(app: Flask, core: Any, core_dir: Path, fake_http: Any) -> Any:
    """A signed-in account, a configured family, and one event each for a
    single person and for two people sharing."""
    app.config["SETTINGS_STORE"].patch_section(
        "plugins", {"outlook_core": {"client_id": "cid", "tenant": "common"}}
    )
    core.save_token(
        core_dir,
        {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_at": time.time() + 3600,
            "account": "Ada",
        },
    )
    core.save_family({"members": MEMBERS, "rules": RULES}, core_dir)

    from app.tz_resolve import app_timezone

    soon = datetime.now(app_timezone()).replace(minute=0, second=0, microsecond=0) + timedelta(
        hours=2
    )
    cancelled = _graph_event(soon + timedelta(hours=3), "L: Abgesagt")
    cancelled["isCancelled"] = True
    fake_http.add(
        CALENDARS_URL,
        {"value": [{"id": "cal-primary", "name": "Calendar", "isDefaultCalendar": True}]},
    )
    fake_http.add(
        "calendarView",
        {
            "value": [
                _graph_event(soon, "L: Klavier"),
                _graph_event(soon + timedelta(hours=1), "PM: Elternabend 5b"),
                cancelled,
            ]
        },
    )
    return fake_http


def _cell_data(body: str) -> dict[str, Any]:
    marker = "data-data='"
    start = body.index(marker) + len(marker)
    return json.loads(body[start : body.index("'", start)])


@pytest.mark.parametrize("size", ["md", "lg"])
def test_widget_renders(client: FlaskClient, outlook: Any, size: str) -> None:
    response = client.get(f"/_test/render?plugin=outlook_family&size={size}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-plugin="outlook_family"' in body

    data = _cell_data(body)
    assert data["count"] == 2
    titles = [e["title"] for day in data["days"] for e in day["events"]]
    assert titles == ["Klavier", "Elternabend 5b"]
    # Cancelled events never reach the panel.
    assert "Abgesagt" not in body


def test_the_panel_gets_everything_it_needs_to_paint_a_person(
    client: FlaskClient, outlook: Any
) -> None:
    data = _cell_data(client.get("/_test/render?plugin=outlook_family&size=lg").get_data(True))

    events = [e for day in data["days"] for e in day["events"]]
    assert events[0]["members"] == ["lea"]
    # A shared event carries both owners, in roster order, so the client can
    # split the stripe between them.
    assert events[1]["members"] == ["papa", "mama"]
    assert {m["id"] for m in data["members"]} == {"papa", "mama", "lea"}
    assert data["hours"] == {"start": 7, "end": 21}
    assert data["layout"] == "grid"


def test_render_reaches_no_real_network(client: FlaskClient, outlook: Any) -> None:
    client.get("/_test/render?plugin=outlook_family&size=md")

    hosts = {url.split("/")[2] for url in outlook.urls}
    assert hosts <= {"graph.microsoft.com", "login.microsoftonline.com"}


def test_a_signed_out_account_renders_an_error_payload(
    app: Flask, client: FlaskClient, fake_http: Any
) -> None:
    app.config["SETTINGS_STORE"].patch_section("plugins", {"outlook_core": {"client_id": "cid"}})

    response = client.get("/_test/render?plugin=outlook_family&size=md")

    assert response.status_code == 200
    data = _cell_data(response.get_data(as_text=True))
    assert data["error"].startswith("Not signed in to Outlook")


def test_it_renders_before_anyone_has_configured_a_family(
    app: Flask, client: FlaskClient, core: Any, core_dir: Path, fake_http: Any
) -> None:
    """A fresh install has no members, so every event is unassigned. It must
    still paint — grey — rather than showing an empty panel."""
    app.config["SETTINGS_STORE"].patch_section(
        "plugins", {"outlook_core": {"client_id": "cid", "tenant": "common"}}
    )
    core.save_token(
        core_dir,
        {"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 3600},
    )
    from app.tz_resolve import app_timezone

    soon = datetime.now(app_timezone()).replace(minute=0, second=0, microsecond=0) + timedelta(
        hours=2
    )
    fake_http.add(
        CALENDARS_URL,
        {"value": [{"id": "cal-primary", "name": "Calendar", "isDefaultCalendar": True}]},
    )
    fake_http.add("calendarView", {"value": [_graph_event(soon, "Zahnarzt")]})

    data = _cell_data(client.get("/_test/render?plugin=outlook_family&size=lg").get_data(True))

    assert data["members"] == []
    assert data["days"][0]["events"][0]["members"] == []
    assert data["days"][0]["events"][0]["title"] == "Zahnarzt"
