"""Family members, and the rules that decide whose appointment an event is.

Outlook has no notion of "whose event is this" — a household encodes it
informally, in a category, a word in the title, a letter prefix like
``L: Klavier``, or the calendar the event lives on. This module turns those
conventions into data: a list of members, and an ordered list of rules that
map an event onto them.

It is deliberately independent of Flask and of Graph. ``resolve_events`` takes
the event shape ``server.load_events_detailed`` already produces and returns
the same events annotated with:

  ``members``   member ids, in config order, or ``[]`` when nothing matched
  ``routine``   True for recurring background commitments (school, office
                hours) that a widget should de-emphasise rather than draw as
                a full block
  ``title``     ``summary`` with every stripped span removed — this is what a
                panel draws, so a matched ``L:`` prefix costs no width

Rules are **all applied**, not first-match-wins: one event can belong to two
people, which is the whole point of a shared "Elternabend".

Storage is ``family.json`` in the plugin's data dir; ``server.py`` owns the
file I/O so the atomic-write helper stays in one place.
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA = 1
FAMILY_FILE = "family.json"

# Fill patterns. Colour alone can't carry identity: on a black-and-white panel
# every Spectra accent slot collapses to the same black, so each member also
# owns a pattern that survives greyscale and dithering.
PATTERNS = ("solid", "diag", "dots", "horiz", "cross")

RULE_KINDS = ("prefix", "category", "contains", "regex", "calendar")

# Admin-page labels. The hue names are the light theme's; the slot is what is
# actually stored, and a different theme paints a different colour into it.
KIND_LABELS = {
    "prefix": "Title prefix (L: …)",
    "category": "Category is",
    "contains": "Title contains",
    "regex": "Title matches (regex)",
    "calendar": "Calendar is",
}
ACCENT_NAMES = {2: "ochre", 3: "moss", 4: "teal", 5: "slate blue", 6: "plum"}

# Swatch hues for the admin page only, so a colour can be picked by eye. These
# are the light theme's values; the panel's own theme decides the real hue, and
# the slot number is the only thing stored.
ACCENT_HEX = {
    2: "#9A7414",
    3: "#4F6F36",
    4: "#256E6B",
    5: "#3F5A88",
    6: "#7E4068",
}

# accent-1 is the design system's alerts/"now" slot — it paints the now-line
# and today's date chip. Giving it to a person would make every panel look
# like something was wrong, so members get 2..6 only.
ACCENT_MIN = 2
ACCENT_MAX = 6

MAX_PREFIX_LETTERS = 4

# ``L: Klavier``, ``LN: Schwimmbad``, ``L Klavier``, ``PM - Elternabend``.
# The separator is optional, which is what makes the bare-letter form work;
# whether the match is *accepted* is decided by _prefix_members below, not
# here, because "OK Meeting" matches this shape too.
PREFIX_RE = re.compile(rf"^\s*([A-Za-z]{{1,{MAX_PREFIX_LETTERS}}})\s*[:\-–]?\s+")

_WS_RE = re.compile(r"\s+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class FamilyError(ValueError):
    """A member or rule the admin page should refuse to save, with a reason a
    person can act on."""


# ----- config -----------------------------------------------------------


def default_config() -> dict[str, Any]:
    """A fresh install: no members yet, but the prefix rule already in place.

    Letter prefixes are the most common convention and the one that costs the
    most title width when it isn't handled, so it is on from the start. With
    no members it simply never fires.
    """
    return {
        "v": SCHEMA,
        "members": [],
        "rules": [
            {
                "id": "prefix",
                "kind": "prefix",
                "value": "",
                "members": [],
                "strip": True,
                "routine": False,
            }
        ],
    }


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.strip().lower()).strip("-")


def _clean_member(raw: Any, *, seen_ids: set[str], seen_letters: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise FamilyError("A family member has to be a name and a few settings.")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise FamilyError("Every family member needs a name.")

    member_id = _slug(str(raw.get("id") or "")) or _slug(name)
    if not member_id:
        raise FamilyError(f"Couldn't derive an id from the name {name!r}. Use letters or digits.")
    if member_id in seen_ids:
        raise FamilyError(f"There is already a family member called {name}.")

    letter = str(raw.get("letter") or "").strip()[:1].upper()
    if letter and letter in seen_letters:
        raise FamilyError(
            f"The letter {letter} is already taken. Title prefixes would be ambiguous, "
            "so each member needs their own letter."
        )

    try:
        accent = int(raw.get("accent", ACCENT_MIN))
    except (TypeError, ValueError):
        raise FamilyError(f"{name}'s colour has to be a number between 2 and 6.") from None
    if not ACCENT_MIN <= accent <= ACCENT_MAX:
        raise FamilyError(
            f"{name}'s colour has to be accent 2 to 6. Accent 1 is reserved for the "
            "'now' line and today's date."
        )

    pattern = str(raw.get("pattern") or "solid")
    if pattern not in PATTERNS:
        pattern = "solid"

    seen_ids.add(member_id)
    if letter:
        seen_letters.add(letter)
    return {
        "id": member_id,
        "name": name,
        "letter": letter,
        "accent": accent,
        "pattern": pattern,
        "everyone": bool(raw.get("everyone")),
    }


def _clean_rule(raw: Any, *, member_ids: set[str], index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise FamilyError("A rule has to be a matcher and who it points at.")

    kind = str(raw.get("kind") or "").strip()
    if kind not in RULE_KINDS:
        raise FamilyError(f"{kind or 'That'} isn't a kind of rule this plugin knows.")

    value = str(raw.get("value") or "").strip()
    if kind != "prefix" and not value:
        raise FamilyError("A rule needs something to match on.")
    if kind == "regex":
        try:
            re.compile(value)
        except re.error as err:
            raise FamilyError(f"That regular expression doesn't compile: {err}.") from None

    members = [str(m) for m in (raw.get("members") or []) if str(m) in member_ids]
    if kind != "prefix" and not members and not raw.get("routine"):
        raise FamilyError("A rule has to point at someone, or mark its events as routine.")

    return {
        "id": str(raw.get("id") or f"rule-{index + 1}"),
        "kind": kind,
        "value": value,
        "members": members,
        "strip": bool(raw.get("strip")),
        "routine": bool(raw.get("routine")),
    }


def clean_config(raw: Any) -> dict[str, Any]:
    """Validate and normalise a whole config, raising :class:`FamilyError` with
    a sentence the admin page can flash."""
    if not isinstance(raw, dict):
        return default_config()

    seen_ids: set[str] = set()
    seen_letters: set[str] = set()
    members = [
        _clean_member(m, seen_ids=seen_ids, seen_letters=seen_letters)
        for m in (raw.get("members") or [])
    ]
    rules = [
        _clean_rule(r, member_ids=seen_ids, index=i) for i, r in enumerate(raw.get("rules") or [])
    ]
    return {"v": SCHEMA, "members": members, "rules": rules}


def roster(config: dict[str, Any]) -> list[dict[str, Any]]:
    """What a widget needs to paint members: id, name, letter, accent slot,
    pattern, and whether this is the household 'everyone' member."""
    return [dict(m) for m in config.get("members") or []]


# ----- matching ---------------------------------------------------------


def _prefix_members(
    title: str, by_letter: dict[str, str]
) -> tuple[tuple[int, int], list[str]] | None:
    """``LN: Schwimmbad`` → both L and N, and the span to cut.

    Fires only when **every** captured letter maps to a member. Without that
    guard "OK Meeting" and "CH Ferien" look exactly like a prefix, and the
    panel would quietly drop their first word.
    """
    match = PREFIX_RE.match(title)
    if match is None:
        return None
    letters = match.group(1).upper()
    members = [by_letter.get(letter) for letter in letters]
    if not members or any(m is None for m in members):
        return None
    return (match.start(), match.end()), [m for m in members if m is not None]


def _rule_match(
    rule: dict[str, Any], event: dict[str, Any], title: str, by_letter: dict[str, str]
) -> tuple[tuple[int, int] | None, list[str]] | None:
    """``(span_to_strip_or_None, member_ids)`` when the rule fires, else None."""
    kind = rule["kind"]
    value = rule["value"]

    if kind == "prefix":
        hit = _prefix_members(title, by_letter)
        if hit is None:
            return None
        span, members = hit
        return span, members

    if kind == "category":
        wanted = value.casefold()
        if any(str(c).strip().casefold() == wanted for c in event.get("categories") or []):
            return None, list(rule["members"])
        return None

    if kind == "contains":
        found = title.casefold().find(value.casefold())
        if found < 0:
            return None
        return (found, found + len(value)), list(rule["members"])

    if kind == "regex":
        try:
            match = re.search(value, title)
        except re.error:  # a rule saved before the pattern broke
            return None
        if match is None:
            return None
        return (match.start(), match.end()), list(rule["members"])

    if kind == "calendar":
        if value in (event.get("calendar_id"), event.get("calendar_name")):
            return None, list(rule["members"])
        return None

    return None


def _cut(title: str, spans: list[tuple[int, int]]) -> str:
    """Remove every matched span and collapse what's left.

    Spans index the original summary, so they're removed back-to-front and the
    result is whitespace-collapsed — otherwise stripping a prefix leaves the
    title starting with a space, and stripping mid-string leaves a double one.
    """
    if not spans:
        return title.strip()
    out = title
    for start, end in sorted(spans, key=lambda s: s[0], reverse=True):
        out = out[:start] + out[end:]
    return _WS_RE.sub(" ", out).strip()


def _apply(event: dict[str, Any], config: dict[str, Any]) -> tuple[list[str], bool, str, list[str]]:
    """``(member_ids, routine, drawn_title, fired_rule_ids)`` for one event."""
    members = config.get("members") or []
    order = {m["id"]: i for i, m in enumerate(members)}
    by_letter = {m["letter"]: m["id"] for m in members if m.get("letter")}

    summary = str(event.get("summary") or "")
    owners: list[str] = []
    routine = False
    spans: list[tuple[int, int]] = []
    fired: list[str] = []

    for rule in config.get("rules") or []:
        hit = _rule_match(rule, event, summary, by_letter)
        if hit is None:
            continue
        span, rule_members = hit
        fired.append(rule["id"])
        owners.extend(rule_members)
        if rule["routine"]:
            routine = True
        if rule["strip"] and span is not None:
            spans.append(span)

    # Config order, de-duplicated: the stripe segments and the gutter slots
    # both read better when a person always appears in the same position.
    unique = sorted({o for o in owners if o in order}, key=lambda o: order[o])
    return unique, routine, _cut(summary, spans), fired


def resolve_events(events: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Annotate events with ``members`` / ``routine`` / ``title``.

    Returns new dicts; the caller's list is untouched, so a cached event never
    picks up a stale assignment when the rules change.
    """
    out: list[dict[str, Any]] = []
    for event in events:
        owners, routine, title, _ = _apply(event, config)
        out.append({**event, "members": owners, "routine": routine, "title": title})
    return out


def report(events: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """What the admin page shows: how often each rule fired, and everything
    that matched nothing.

    The unmatched tray is what keeps a rule list maintainable — a rule that
    silently stops firing is otherwise invisible until someone misses a
    dentist appointment.
    """
    counts = {rule["id"]: 0 for rule in config.get("rules") or []}
    unmatched: list[dict[str, Any]] = []
    for event in events:
        owners, _routine, _title, fired = _apply(event, config)
        for rule_id in fired:
            counts[rule_id] = counts.get(rule_id, 0) + 1
        if not owners:
            unmatched.append(
                {
                    "summary": event.get("summary") or "",
                    "start": event.get("start") or "",
                    "categories": list(event.get("categories") or []),
                    "calendar_name": event.get("calendar_name") or "",
                }
            )
    return {"counts": counts, "unmatched": unmatched, "total": len(events)}
