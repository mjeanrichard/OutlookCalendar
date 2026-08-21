"""outlook_core: the admin page at /plugins/outlook_core/."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

BASE = "/plugins/outlook_core"
DEVICE_CODE_URL = "oauth2/v2.0/devicecode"
TOKEN_URL = "oauth2/v2.0/token"
CALENDARS_URL = "/me/calendars?"


@pytest.fixture
def configured(app: Flask) -> Flask:
    app.config["SETTINGS_STORE"].patch_section(
        "plugins", {"outlook_core": {"client_id": "test-client-id", "tenant": "common"}}
    )
    return app


def _page(client: FlaskClient, path: str = "/") -> str:
    response = client.get(f"{BASE}{path}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_unconfigured_page_explains_the_app_registration(app: Flask, client: FlaskClient) -> None:
    body = _page(client)

    assert "Allow public client flows" in body
    assert "Calendars.Read" in body
    # No sign-in button until there is something to sign in with.
    assert "Sign in with Microsoft" not in body


def test_configured_page_offers_sign_in(configured: Flask, client: FlaskClient) -> None:
    assert "Sign in with Microsoft" in _page(client)


def test_signin_starts_the_flow_and_shows_the_code(
    configured: Flask, client: FlaskClient, fake_http: Any, core_dir: Path
) -> None:
    fake_http.add(
        DEVICE_CODE_URL,
        {
            "device_code": "dc",
            "user_code": "WXYZ-1234",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
        },
    )

    response = client.post(f"{BASE}/signin", follow_redirects=True)

    body = response.get_data(as_text=True)
    assert "WXYZ-1234" in body
    assert "Check sign-in" in body
    assert (core_dir / "device_code.json").exists()


def test_signin_surfaces_a_rejected_client_id(
    configured: Flask, client: FlaskClient, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, {"error": "invalid_client"}, status=400)

    response = client.post(f"{BASE}/signin", follow_redirects=True)

    assert "Microsoft rejected the client ID" in response.get_data(as_text=True)


def test_poll_reports_that_it_is_still_waiting(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(
        DEVICE_CODE_URL,
        {"device_code": "dc", "user_code": "AAAA", "verification_uri": "u", "expires_in": 900},
    )
    client.post(f"{BASE}/signin")
    fake_http.add(TOKEN_URL, {"error": "authorization_pending"}, status=400)

    response = client.post(f"{BASE}/poll", follow_redirects=True)

    assert "Still waiting" in response.get_data(as_text=True)
    assert core.load_token(core_dir) == {}


def test_poll_completes_the_sign_in(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(
        DEVICE_CODE_URL,
        {"device_code": "dc", "user_code": "AAAA", "verification_uri": "u", "expires_in": 900},
    )
    client.post(f"{BASE}/signin")
    fake_http.add(TOKEN_URL, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600})
    fake_http.add("/me?", {"displayName": "Ada", "userPrincipalName": "ada@example.com"})
    fake_http.add(
        CALENDARS_URL, {"value": [{"id": "c1", "name": "Calendar", "isDefaultCalendar": True}]}
    )

    response = client.post(f"{BASE}/poll", follow_redirects=True)

    body = response.get_data(as_text=True)
    assert "Signed in as Ada (ada@example.com)" in body
    assert "Calendar" in body  # the calendar list renders once signed in
    assert core.load_token(core_dir)["refresh_token"] == "rt"


def test_signed_in_page_lists_calendars(
    configured: Flask, client: FlaskClient, signed_in: Any, fake_http: Any
) -> None:
    fake_http.add(
        CALENDARS_URL,
        {
            "value": [
                {"id": "c1", "name": "Calendar", "isDefaultCalendar": True},
                {"id": "c2", "name": "Team events", "isDefaultCalendar": False},
            ]
        },
    )

    body = _page(client)

    assert "Test User (test@example.com)" in body
    assert "Team events" in body
    assert "default" in body


def test_signed_in_page_survives_a_graph_outage(
    configured: Flask, client: FlaskClient, signed_in: Any, fake_http: Any
) -> None:
    fake_http.add(CALENDARS_URL, {"error": {"code": "serviceUnavailable"}}, status=503)

    body = _page(client)

    assert "rate-limiting" in body or "Couldn't load" in body
    assert "Sign out" in body  # the page still works


def test_signout_clears_the_token_and_calendar_cache(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path, signed_in: Any
) -> None:
    (core_dir / "calendars.json").write_text('{"calendars": []}', encoding="utf-8")

    response = client.post(f"{BASE}/signout", follow_redirects=True)

    assert "Signed out" in response.get_data(as_text=True)
    assert core.load_token(core_dir) == {}
    assert not (core_dir / "calendars.json").exists()


def test_refresh_calendars_drops_the_cache(
    configured: Flask, client: FlaskClient, core_dir: Path, signed_in: Any, fake_http: Any
) -> None:
    (core_dir / "calendars.json").write_text('{"calendars": []}', encoding="utf-8")
    fake_http.add(CALENDARS_URL, {"value": []})

    client.post(f"{BASE}/refresh-calendars", follow_redirects=True)

    # Cleared, then re-fetched when the page rendered again.
    assert fake_http.calls_matching("/me/calendars") == 1


def test_poll_without_a_flow_says_so(configured: Flask, client: FlaskClient) -> None:
    response = client.post(f"{BASE}/poll", follow_redirects=True)
    assert "No sign-in is in progress" in response.get_data(as_text=True)
