"""outlook_core: family members, ownership rules, and what they do to a title.

These are pure-function tests against ``core.family`` plus a round-trip through
the on-disk config, so nothing here needs Graph or a signed-in account.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

MEMBERS = [
    {"name": "Papa", "letter": "P", "accent": 5, "pattern": "solid"},
    {"name": "Mama", "letter": "M", "accent": 6, "pattern": "diag"},
    {"name": "Lea", "letter": "L", "accent": 3, "pattern": "dots"},
    {"name": "Nils", "letter": "N", "accent": 2, "pattern": "horiz"},
    {"name": "Familie", "letter": "F", "accent": 4, "pattern": "cross", "everyone": True},
]

RULES = [
    {"id": "prefix", "kind": "prefix", "strip": True},
    {
        "id": "schule",
        "kind": "category",
        "value": "Schule",
        "members": ["lea", "nils"],
        "routine": True,
    },
    {"id": "fussball", "kind": "contains", "value": "Fussball", "members": ["nils"]},
    {"id": "familie", "kind": "calendar", "value": "cal-familie", "members": ["familie"]},
]


@pytest.fixture
def config(core: Any) -> dict[str, Any]:
    return core.family.clean_config({"members": MEMBERS, "rules": RULES})


def _event(summary: str, **over: Any) -> dict[str, Any]:
    raw = {
        "summary": summary,
        "categories": [],
        "calendar_id": "cal-primary",
        "calendar_name": "Calendar",
    }
    raw.update(over)
    return raw


def _resolve(core: Any, config: dict[str, Any], summary: str, **over: Any) -> dict[str, Any]:
    return core.family.resolve_events([_event(summary, **over)], config)[0]


# ----- members --------------------------------------------------------


def test_a_member_gets_an_id_derived_from_their_name(core: Any) -> None:
    cleaned = core.family.clean_config({"members": [{"name": "Anne-Marie", "accent": 3}]})

    assert cleaned["members"][0]["id"] == "anne-marie"


def test_accent_one_is_refused_because_it_is_the_now_colour(core: Any) -> None:
    with pytest.raises(core.FamilyError, match="reserved"):
        core.family.clean_config({"members": [{"name": "Lea", "accent": 1}]})


@pytest.mark.parametrize("accent", [0, 10, "purple"])
def test_an_accent_outside_the_member_slots_is_refused(core: Any, accent: Any) -> None:
    with pytest.raises(core.FamilyError):
        core.family.clean_config({"members": [{"name": "Lea", "accent": accent}]})


def test_two_members_cannot_share_a_letter(core: Any) -> None:
    with pytest.raises(core.FamilyError, match="already taken"):
        core.family.clean_config(
            {
                "members": [
                    {"name": "Lea", "letter": "L", "accent": 3},
                    {"name": "Luca", "letter": "L", "accent": 4},
                ]
            }
        )


def test_a_member_needs_a_name(core: Any) -> None:
    with pytest.raises(core.FamilyError, match="needs a name"):
        core.family.clean_config({"members": [{"name": "  ", "accent": 3}]})


def test_an_unknown_pattern_falls_back_to_solid(core: Any) -> None:
    cleaned = core.family.clean_config(
        {"members": [{"name": "Lea", "accent": 3, "pattern": "zig"}]}
    )

    assert cleaned["members"][0]["pattern"] == "solid"


# ----- rules ----------------------------------------------------------


def test_a_rule_pointing_at_nobody_is_refused(core: Any) -> None:
    with pytest.raises(core.FamilyError, match="point at someone"):
        core.family.clean_config(
            {"members": MEMBERS, "rules": [{"kind": "contains", "value": "Klavier"}]}
        )


def test_a_routine_rule_may_point_at_nobody(core: Any) -> None:
    cleaned = core.family.clean_config(
        {"members": MEMBERS, "rules": [{"kind": "contains", "value": "Büro", "routine": True}]}
    )

    assert cleaned["rules"][0]["routine"] is True


def test_a_broken_regex_is_refused_at_save_time(core: Any) -> None:
    with pytest.raises(core.FamilyError, match="doesn't compile"):
        core.family.clean_config(
            {"members": MEMBERS, "rules": [{"kind": "regex", "value": "[", "members": ["lea"]}]}
        )


def test_a_rule_forgets_a_member_who_no_longer_exists(core: Any) -> None:
    cleaned = core.family.clean_config(
        {
            "members": [{"name": "Lea", "letter": "L", "accent": 3}],
            "rules": [{"kind": "contains", "value": "Klavier", "members": ["lea", "ghost"]}],
        }
    )

    assert cleaned["rules"][0]["members"] == ["lea"]


# ----- matching -------------------------------------------------------


@pytest.mark.parametrize(
    ("summary", "owners", "title"),
    [
        ("L: Klavier", ["lea"], "Klavier"),
        ("L Turnen", ["lea"], "Turnen"),
        ("PM: Elternabend 5b", ["papa", "mama"], "Elternabend 5b"),
        ("LN - Schwimmbad", ["lea", "nils"], "Schwimmbad"),
        ("l: klavier", ["lea"], "klavier"),
    ],
)
def test_a_letter_prefix_names_its_people_and_leaves_the_title(
    core: Any, config: dict[str, Any], summary: str, owners: list[str], title: str
) -> None:
    resolved = _resolve(core, config, summary)

    assert resolved["members"] == owners
    assert resolved["title"] == title
    # The original is kept: the panel draws `title`, but nothing is lost.
    assert resolved["summary"] == summary


@pytest.mark.parametrize("summary", ["OK Meeting", "CH Ferien", "A Team Meeting", "Zahnarzt"])
def test_words_that_only_look_like_a_prefix_survive_intact(
    core: Any, config: dict[str, Any], summary: str
) -> None:
    """The guard that makes the bare-letter form safe: unless *every* captured
    letter is a member's, the rule doesn't fire and nothing is stripped."""
    resolved = _resolve(core, config, summary)

    assert resolved["members"] == []
    assert resolved["title"] == summary


def test_members_come_back_in_config_order_however_the_prefix_is_written(
    core: Any, config: dict[str, Any]
) -> None:
    """Stripe segments and gutter slots both read better when a person always
    appears in the same position."""
    assert _resolve(core, config, "MP: Elternabend")["members"] == ["papa", "mama"]
    assert _resolve(core, config, "PM: Elternabend")["members"] == ["papa", "mama"]


def test_a_category_rule_can_mark_its_events_routine(core: Any, config: dict[str, Any]) -> None:
    resolved = _resolve(core, config, "Schule", categories=["Schule"])

    assert resolved["members"] == ["lea", "nils"]
    assert resolved["routine"] is True


def test_a_category_matches_whatever_case_outlook_stored(core: Any, config: dict[str, Any]) -> None:
    assert _resolve(core, config, "Schule", categories=["schule"])["members"] == ["lea", "nils"]


def test_a_contains_rule_leaves_the_title_alone_unless_it_strips(
    core: Any, config: dict[str, Any]
) -> None:
    resolved = _resolve(core, config, "Fussball Training")

    assert resolved["members"] == ["nils"]
    assert resolved["title"] == "Fussball Training"


def test_a_calendar_rule_matches_on_id_or_name(core: Any, config: dict[str, Any]) -> None:
    by_id = _resolve(core, config, "Brunch", calendar_id="cal-familie")
    by_name = _resolve(core, config, "Brunch", calendar_name="cal-familie")

    assert by_id["members"] == ["familie"]
    assert by_name["members"] == ["familie"]


def test_every_matching_rule_applies_so_an_event_can_have_two_owners(
    core: Any, config: dict[str, Any]
) -> None:
    """A prefix *and* a category rule both firing is how a shared event ends
    up belonging to more people than either rule names."""
    resolved = _resolve(core, config, "L: Fussball", categories=[])

    assert resolved["members"] == ["lea", "nils"]


def test_stripping_mid_title_does_not_leave_a_double_space(core: Any) -> None:
    config = core.family.clean_config(
        {
            "members": [{"name": "Lea", "letter": "L", "accent": 3}],
            "rules": [{"kind": "contains", "value": "[privat]", "members": ["lea"], "strip": True}],
        }
    )

    resolved = core.family.resolve_events([_event("Zahnarzt [privat] Kontrolle")], config)[0]

    assert resolved["title"] == "Zahnarzt Kontrolle"


def test_an_event_nothing_claims_keeps_its_title_and_owns_nobody(
    core: Any, config: dict[str, Any]
) -> None:
    resolved = _resolve(core, config, "Zahnarzt Kontrolle")

    assert resolved["members"] == []
    assert resolved["routine"] is False
    assert resolved["title"] == "Zahnarzt Kontrolle"


def test_resolving_does_not_mutate_the_events_it_was_given(
    core: Any, config: dict[str, Any]
) -> None:
    """Events come out of a shared cache; annotating one in place would leak a
    stale assignment into the next render after the rules change."""
    events = [_event("L: Klavier")]

    core.family.resolve_events(events, config)

    assert "members" not in events[0]
    assert events[0]["summary"] == "L: Klavier"


# ----- report ---------------------------------------------------------


def test_the_report_counts_hits_and_collects_what_nothing_claimed(
    core: Any, config: dict[str, Any]
) -> None:
    events = [
        _event("L: Klavier", start="2026-08-21T16:00:00+02:00"),
        _event("Fussball", start="2026-08-21T17:30:00+02:00"),
        _event("Zahnarzt", start="2026-08-24T16:00:00+02:00", categories=["Termine"]),
    ]

    report = core.family.report(events, config)

    assert report["counts"]["prefix"] == 1
    assert report["counts"]["fussball"] == 1
    assert report["counts"]["schule"] == 0
    assert [item["summary"] for item in report["unmatched"]] == ["Zahnarzt"]
    assert report["total"] == 3


# ----- persistence ----------------------------------------------------


def test_the_family_round_trips_through_disk(core: Any, core_dir: Path) -> None:
    core.save_family({"members": MEMBERS, "rules": RULES}, core_dir)

    loaded = core.family_config(core_dir)

    assert [m["id"] for m in loaded["members"]] == ["papa", "mama", "lea", "nils", "familie"]
    assert [r["id"] for r in loaded["rules"]] == ["prefix", "schule", "fussball", "familie"]
    assert loaded["members"][4]["everyone"] is True


def test_a_fresh_install_gets_the_prefix_rule_and_no_members(core: Any, core_dir: Path) -> None:
    """Letter prefixes are the most common convention and the one that costs
    the most title width unhandled, so the rule is there from the start. With
    no members it can never fire."""
    loaded = core.family_config(core_dir)

    assert loaded["members"] == []
    assert [r["kind"] for r in loaded["rules"]] == ["prefix"]


def test_an_unreadable_family_file_falls_back_instead_of_breaking_the_panel(
    core: Any, core_dir: Path
) -> None:
    (core_dir / core.family.FAMILY_FILE).parent.mkdir(parents=True, exist_ok=True)
    (core_dir / core.family.FAMILY_FILE).write_text('{"members": [{"accent": 99}]}', "utf-8")

    loaded = core.family_config(core_dir)

    assert loaded == core.family.default_config()


def test_the_roster_is_what_a_widget_needs_to_paint(core: Any, core_dir: Path) -> None:
    core.save_family({"members": MEMBERS, "rules": []}, core_dir)

    roster = core.family_roster(core_dir)

    assert roster[2] == {
        "id": "lea",
        "name": "Lea",
        "letter": "L",
        "accent": 3,
        "pattern": "dots",
        "everyone": False,
    }
