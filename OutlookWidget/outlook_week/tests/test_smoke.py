"""outlook_week: it renders at every supported size, with no network in sight.

The test-render route stamps the fetch() payload onto the cell as ``data-data``
and client.js paints from there in the browser, so asserting on that attribute
is how the server side proves the whole data path ran.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

CALENDARS_URL = "/me/calendars?"
GRAPH_FORMAT = "%Y-%m-%dT%H:%M:%S.0000000"


def _graph_event(start: datetime, subject: str) -> dict[str, Any]:
    return {
        "id": "evt-smoke",
        "subject": subject,
        "start": {"dateTime": start.strftime(GRAPH_FORMAT), "timeZone": str(start.tzinfo)},
        "end": {
            "dateTime": (start + timedelta(hours=1)).strftime(GRAPH_FORMAT),
            "timeZone": str(start.tzinfo),
        },
        "isAllDay": False,
        "location": {"displayName": "Room 4.12"},
        "showAs": "busy",
        "responseStatus": {"response": "accepted"},
        "categories": ["Team"],
        "isCancelled": False,
    }


@pytest.fixture
def outlook(app: Flask, core: Any, core_dir: Any, fake_http: Any) -> Any:
    """A signed-in account with one meeting later today and one cancelled."""
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
    from app.tz_resolve import app_timezone

    soon = datetime.now(app_timezone()).replace(minute=0, second=0, microsecond=0) + timedelta(
        hours=2
    )
    cancelled = _graph_event(soon + timedelta(hours=1), "Cancelled thing")
    cancelled["isCancelled"] = True
    fake_http.add(
        CALENDARS_URL,
        {"value": [{"id": "cal-primary", "name": "Calendar", "isDefaultCalendar": True}]},
    )
    fake_http.add("calendarView", {"value": [_graph_event(soon, "Sprint review"), cancelled]})
    return fake_http


def _cell_data(body: str) -> dict[str, Any]:
    marker = "data-data='"
    start = body.index(marker) + len(marker)
    return json.loads(body[start : body.index("'", start)])


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_widget_renders(client: FlaskClient, outlook: Any, size: str) -> None:
    response = client.get(f"/_test/render?plugin=outlook_week&size={size}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-plugin="outlook_week"' in body

    data = _cell_data(body)
    assert data["count"] == 1
    assert data["days"][0]["events"][0]["summary"] == "Sprint review"
    assert data["days"][0]["events"][0]["location"] == "Room 4.12"
    # Cancelled events never reach the panel.
    assert "Cancelled thing" not in body


def test_render_reaches_no_real_network(client: FlaskClient, outlook: Any) -> None:
    client.get("/_test/render?plugin=outlook_week&size=md")

    hosts = {url.split("/")[2] for url in outlook.urls}
    assert hosts <= {"graph.microsoft.com", "login.microsoftonline.com"}


def test_a_signed_out_account_renders_an_error_payload(
    app: Flask, client: FlaskClient, fake_http: Any
) -> None:
    app.config["SETTINGS_STORE"].patch_section("plugins", {"outlook_core": {"client_id": "cid"}})

    response = client.get("/_test/render?plugin=outlook_week&size=md")

    assert response.status_code == 200
    data = _cell_data(response.get_data(as_text=True))
    assert data["error"].startswith("Not signed in to Outlook")
