"""Screenshot outlook_month through the running dev server, at the panel's size.

    <tesserae-clone>\\.venv\\Scripts\\python tools/month_render.py            # real data
    <tesserae-clone>\\.venv\\Scripts\\python tools/month_render.py --fixture  # a dense sample

Writes tools/out/real_month_{lg,md}.png. ``--fixture`` re-renders the widget
in place with a synthetic fetch() payload — a busy day with more chips than
fit, titles that wrap, a band crossing a week row, shared and unassigned
events, and days already over — so every rule in client.js can be looked at
without waiting for the real calendar to contain such a week.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent / "out"
BASE = "http://127.0.0.1:8765"
SIZES = {"lg": (1600, 1200), "md": (640, 400)}

MEMBERS = [
    {"id": "lea", "name": "Lea", "letter": "L", "accent": 2, "pattern": "solid", "everyone": False},
    {"id": "noah", "name": "Noah", "letter": "N", "accent": 3, "pattern": "diag", "everyone": False},
    {"id": "mama", "name": "Mama", "letter": "M", "accent": 4, "pattern": "dots", "everyone": False},
    {"id": "papa", "name": "Papa", "letter": "P", "accent": 5, "pattern": "horiz", "everyone": False},
    {"id": "alle", "name": "Alle", "letter": "A", "accent": 6, "pattern": "cross", "everyone": True},
]

# (day offset from the window's Monday, title, owners); today is offset 2.
EVENTS = [
    (0, "Kinderarzt", ["noah", "mama"]),
    (0, "Klavier", ["lea"]),
    (0, "Velo Service", ["papa"]),
    (0, "Yoga", ["mama"]),
    (1, "Turnen", ["lea", "noah"]),
    (2, "Sitzung Bern", ["papa"]),
    (2, "Coiffeur", ["mama"]),
    (2, "Elternabend der fünften Klasse", ["mama", "papa"]),
    (2, "Kaminfeger", []),
    (2, "Logopädie", ["noah"]),
    (2, "Schwimmen", ["noah"]),
    (2, "Physio", ["mama"]),
    (2, "Apéro", ["papa"]),
    (2, "Lesenacht", ["lea"]),
    (3, "Geburtstag Oma", ["alle"]),
    (4, "Kino mit Mia", ["lea"]),
    (7, "Zahnarzt", ["papa"]),
    (7, "Klavier", ["lea"]),
    (8, "Elternabend Noah", ["mama"]),
    (9, "Schulreise", ["noah"]),
    (10, "Lesenacht", ["lea"]),
    (12, "Konzert", ["mama", "papa"]),
    (14, "Klavier", ["lea"]),
    (16, "Abgabe Projekt", ["papa"]),
    (16, "Schwimmen", ["noah"]),
    (18, "Nachtessen bei Fischers", ["mama", "papa"]),
    (19, "Herbstmesse", ["alle"]),
    (22, "Zoo", ["alle"]),
    (23, "Offsite", ["papa"]),
    (25, "Bei Oma und Opa", ["lea", "noah"]),
]
# (first, last, title, owners)
BANDS = [
    (5, 6, "Tessin", ["alle"]),
    (7, 11, "Zürich", ["papa"]),
    (19, 27, "Herbstferien", ["lea", "noah"]),
    (12, 13, "Hüttenwochenende", []),
]


def fixture() -> dict:
    monday = date.today() - timedelta(days=date.today().weekday())
    today = monday + timedelta(days=2)
    days = []
    for i in range(28):
        d = monday + timedelta(days=i)
        days.append(
            {
                "date": d.isoformat(),
                "weekday": d.strftime("%A"),
                "day": d.day,
                "is_today": d == today,
                "is_past": d < today,
                "is_weekend": d.weekday() >= 5,
                "events": [
                    {"title": t, "summary": t, "members": m, "routine": False, "all_day": False}
                    for off, t, m in EVENTS
                    if off == i
                ],
            }
        )
    bands = [
        {"title": t, "summary": t, "members": m, "all_day": True, "first": a, "last": b}
        for a, b, t, m in BANDS
    ]
    return {
        "days": days,
        "bands": bands,
        "members": MEMBERS,
        "weeks": 4,
        "week_start": "monday",
        "count": len(EVENTS) + len(BANDS),
        "stale": False,
    }


RERENDER_JS = """async ([data, size, loc]) => {
  const host = [...document.querySelectorAll('.cell-content')].find((e) => e.shadowRoot);
  const mod = await import('/plugins/outlook_month/client.js');
  const ctx = { data, cell: { size }, ...loc };
  if (loc.strings) ctx.t = (key, fallback) => loc.strings[key] ?? fallback ?? key;
  await mod.default(host.shadowRoot, ctx);
  await document.fonts.ready;
}"""


def locale_ctx(argv: list[str], plugin_id: str) -> dict:
    """``--locale de`` re-renders with the host's locale contract filled in
    from the widget's own strings file, so a translation can be checked
    against a dev clone that predates ``ctx.t``."""
    if "--locale" not in argv:
        return {}
    tag = argv[argv.index("--locale") + 1]
    strings_dir = Path(__file__).parent.parent / "OutlookWidget" / plugin_id / "strings"
    path = strings_dir / f"{tag}.json"
    strings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {"locale": tag, "strings": strings}


def main(argv: list[str]) -> int:
    use_fixture = "--fixture" in argv or "--locale" in argv
    loc = locale_ctx(argv, "outlook_month")
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for size, (w, h) in SIZES.items():
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(
                f"{BASE}/_test/render?plugin=outlook_month&size={size}&w={w}&h={h}",
                wait_until="networkidle",
                timeout=90_000,
            )
            if use_fixture:
                page.evaluate(RERENDER_JS, [fixture(), size, loc])
            page.wait_for_timeout(300)
            path = OUT / f"real_month_{size}{'_' + loc['locale'] if loc else ''}.png"
            page.screenshot(path=str(path))
            print(path, "errors:" if errors else "", *errors)
            page.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
