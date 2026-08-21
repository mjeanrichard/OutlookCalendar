"""outlook_core: the family editors on /plugins/outlook_core/.

Both forms are plain parallel-list POSTs with no JavaScript, so these tests
post the same field lists a browser would and assert on what survives.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.datastructures import MultiDict

BASE = "/plugins/outlook_core"
CALENDARS_URL = "/me/calendars?"
GRAPH_FORMAT = "%Y-%m-%dT%H:%M:%S.0000000"


@pytest.fixture
def configured(app: Flask) -> Flask:
    app.config["SETTINGS_STORE"].patch_section(
        "plugins", {"outlook_core": {"client_id": "test-client-id", "tenant": "common"}}
    )
    return app


def _members_form(*rows: tuple[str, str, str, str]) -> list[tuple[str, str]]:
    """Rows of (name, letter, accent, pattern), plus the blank row a browser
    always submits from the bottom of the table."""
    fields: list[tuple[str, str]] = []
    for name, letter, accent, pattern in rows:
        fields += [
            ("member_id", ""),
            ("member_name", name),
            ("member_letter", letter),
            ("member_accent", accent),
            ("member_pattern", pattern),
        ]
    fields += [
        ("member_id", ""),
        ("member_name", ""),
        ("member_letter", ""),
        ("member_accent", "2"),
        ("member_pattern", "solid"),
    ]
    return fields


def _signed_in(core: Any, core_dir: Path) -> None:
    core.save_token(
        core_dir,
        {"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 3600},
    )


# ----- members --------------------------------------------------------


def test_saving_members_keeps_them_and_drops_the_blank_row(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path
) -> None:
    response = client.post(
        f"{BASE}/family/members",
        data=MultiDict(_members_form(("Papa", "P", "5", "solid"), ("Lea", "L", "3", "dots"))),
    )

    assert response.status_code == 302
    saved = core.family_config(core_dir)
    assert [m["id"] for m in saved["members"]] == ["papa", "lea"]
    assert saved["members"][1]["pattern"] == "dots"


def test_the_everyone_checkbox_marks_the_row_it_names(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path
) -> None:
    """Checkboxes submit their row index, which is how an unticked box (which
    submits nothing at all) still lines up with the right person."""
    client.post(
        f"{BASE}/family/members",
        data=MultiDict(
            [
                *_members_form(("Papa", "P", "5", "solid"), ("Familie", "F", "4", "cross")),
                ("member_everyone", "1"),
            ]
        ),
    )

    saved = core.family_config(core_dir)
    assert saved["members"][0]["everyone"] is False
    assert saved["members"][1]["everyone"] is True


def test_removing_a_member_takes_them_out_of_the_rules_too(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path
) -> None:
    """A rule left pointing at a deleted member fails validation on the next
    save, locking the page up for a reason nobody could see."""
    core.save_family(
        {
            "members": [
                {"name": "Papa", "letter": "P", "accent": 5},
                {"name": "Lea", "letter": "L", "accent": 3},
            ],
            "rules": [
                {"id": "r1", "kind": "contains", "value": "Klavier", "members": ["lea", "papa"]}
            ],
        },
        core_dir,
    )

    client.post(
        f"{BASE}/family/members",
        data=MultiDict(
            [
                *_members_form(("Papa", "P", "5", "solid"), ("Lea", "L", "3", "dots")),
                ("member_delete", "1"),
            ]
        ),
    )

    saved = core.family_config(core_dir)
    assert [m["id"] for m in saved["members"]] == ["papa"]
    assert saved["rules"][0]["members"] == ["papa"]


def test_a_refused_member_is_explained_rather_than_saved(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path
) -> None:
    response = client.post(
        f"{BASE}/family/members",
        data=MultiDict(_members_form(("Lea", "L", "3", "dots"), ("Luca", "L", "4", "solid"))),
        follow_redirects=True,
    )

    assert "already taken" in response.get_data(as_text=True)
    assert core.family_config(core_dir)["members"] == []


# ----- rules ----------------------------------------------------------


def test_saving_rules_orders_them_and_skips_the_blank_row(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path
) -> None:
    core.save_family({"members": [{"name": "Lea", "letter": "L", "accent": 3}]}, core_dir)

    client.post(
        f"{BASE}/family/rules",
        data=MultiDict(
            [
                ("rule_id", "prefix"),
                ("rule_order", "5"),
                ("rule_kind", "prefix"),
                ("rule_value", ""),
                ("rule_id", "klavier"),
                ("rule_order", "1"),
                ("rule_kind", "contains"),
                ("rule_value", "Klavier"),
                ("rule_members_1", "lea"),
                ("rule_id", ""),
                ("rule_order", "9"),
                ("rule_kind", "contains"),
                ("rule_value", ""),
                ("rule_strip", "0"),
            ]
        ),
    )

    saved = core.family_config(core_dir)
    # rule_order decides the list, not the order the rows arrived in.
    assert [r["id"] for r in saved["rules"]] == ["klavier", "prefix"]
    assert saved["rules"][1]["strip"] is True
    assert saved["rules"][0]["members"] == ["lea"]


def test_a_prefix_rule_saves_without_a_value(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path
) -> None:
    """It has nothing to configure: the letters come from the members."""
    client.post(
        f"{BASE}/family/rules",
        data=MultiDict(
            [
                ("rule_id", "prefix"),
                ("rule_order", "0"),
                ("rule_kind", "prefix"),
                ("rule_value", ""),
                ("rule_strip", "0"),
            ]
        ),
    )

    assert [r["kind"] for r in core.family_config(core_dir)["rules"]] == ["prefix"]


def test_a_broken_regex_is_explained_rather_than_saved(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path
) -> None:
    core.save_family({"members": [{"name": "Lea", "letter": "L", "accent": 3}]}, core_dir)

    response = client.post(
        f"{BASE}/family/rules",
        data=MultiDict(
            [
                ("rule_id", "bad"),
                ("rule_order", "0"),
                ("rule_kind", "regex"),
                ("rule_value", "["),
                ("rule_members_0", "lea"),
            ]
        ),
        follow_redirects=True,
    )

    assert "compile" in response.get_data(as_text=True)
    assert core.family_config(core_dir)["rules"] == []


# ----- the page -------------------------------------------------------


def test_the_page_lists_the_family_it_saved(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path
) -> None:
    core.save_family(
        {"members": [{"name": "Lea", "letter": "L", "accent": 3, "pattern": "dots"}], "rules": []},
        core_dir,
    )

    body = client.get(f"{BASE}/").get_data(as_text=True)

    assert 'value="Lea"' in body
    assert "Family members" in body


def test_the_page_costs_no_graph_call_until_the_family_has_members(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path, fake_http: Any
) -> None:
    """The rule counts need a week of events, but a fresh install has nothing
    to count — and no reason to spend a fetch on a page opened to sign in."""
    _signed_in(core, core_dir)
    fake_http.add(
        CALENDARS_URL,
        {"value": [{"id": "cal-primary", "name": "Calendar", "isDefaultCalendar": True}]},
    )

    client.get(f"{BASE}/")

    assert fake_http.calls_matching("calendarView") == 0


def test_the_unmatched_tray_lists_what_no_rule_claimed(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path, fake_http: Any
) -> None:
    _signed_in(core, core_dir)
    core.save_family(
        {
            "members": [{"name": "Lea", "letter": "L", "accent": 3}],
            "rules": [{"id": "prefix", "kind": "prefix", "strip": True}],
        },
        core_dir,
    )
    from app.tz_resolve import app_timezone

    soon = datetime.now(app_timezone()) + timedelta(hours=2)
    fake_http.add(
        CALENDARS_URL,
        {"value": [{"id": "cal-primary", "name": "Calendar", "isDefaultCalendar": True}]},
    )
    fake_http.add(
        "calendarView",
        {
            "value": [
                {
                    "id": f"e{i}",
                    "subject": subject,
                    "start": {
                        "dateTime": soon.strftime(GRAPH_FORMAT),
                        "timeZone": str(soon.tzinfo),
                    },
                    "end": {
                        "dateTime": (soon + timedelta(hours=1)).strftime(GRAPH_FORMAT),
                        "timeZone": str(soon.tzinfo),
                    },
                    "isAllDay": False,
                    "location": {"displayName": ""},
                    "showAs": "busy",
                    "responseStatus": {"response": "accepted"},
                    "categories": [],
                    "isCancelled": False,
                }
                for i, subject in enumerate(["L: Klavier", "Zahnarzt Kontrolle"])
            ]
        },
    )

    body = client.get(f"{BASE}/").get_data(as_text=True)
    tray = body.split("Matched nothing this week")[1]

    assert "Zahnarzt Kontrolle" in tray
    # The one the prefix rule claimed isn't in the tray.
    assert "Klavier" not in tray


def test_the_page_still_renders_when_graph_is_down(
    configured: Flask, client: FlaskClient, core: Any, core_dir: Path, fake_http: Any
) -> None:
    """The counts are best-effort; the editors have to work during an outage."""
    _signed_in(core, core_dir)
    core.save_family({"members": [{"name": "Lea", "letter": "L", "accent": 3}]}, core_dir)
    fake_http.add(CALENDARS_URL, {"error": "boom"}, 503)
    fake_http.add("calendarView", {"error": "boom"}, 503)

    response = client.get(f"{BASE}/")

    assert response.status_code == 200
    assert "Family members" in response.get_data(as_text=True)
