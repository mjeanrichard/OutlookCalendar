# AGENTS.md

Instructions for AI agents working in this repository.

## What this is

Outlook widgets for [Tesserae](https://tesserae.ink), a self-hosted e-ink dashboard. Tesserae
renders widgets headless in a browser and pushes the screenshot to an e-ink panel.

Repo root is `Calendar/` (this file's directory). The plugins live under `OutlookWidget/`:

| Path | Kind | What it is |
| --- | --- | --- |
| `OutlookWidget/outlook_core/` | `data` | Microsoft Graph sign-in, tokens, calendar list, caching, the family config |
| `OutlookWidget/outlook_week/` | `widget` | Next N days, grouped by day |
| `OutlookWidget/outlook_family/` | `widget` | The family timetable: a column per day, colour/pattern per person |
| `OutlookWidget/outlook_month/` | `widget` | The family wall calendar: a row per week from the current one, a chip per event |

`OutlookWidget/DESIGN.md` is the design record for the family widgets: what the panels do now, and
the numbered decisions behind them. Read it before changing `outlook_family/client.js`,
`outlook_month/client.js` or the family rules — most of what looks arbitrary in there is load-bearing, and the reason is written down.

**The folder name is the plugin id** — there is no `id` field in `plugin.json`. Everything else
under `OutlookWidget/` is scaffolding: `devserver.py`, `conftest.py`, `_devsupport.py`, `_tests/`,
`_docs/` (a copy of the Tesserae docs; `_docs/widgets.md` is the authoritative widget contract),
and `ruff.toml`, which mirrors the clone's lint/format settings.

Status: `outlook_core`, `outlook_family` and `outlook_month` are complete and tested. `outlook_week/client.js` is
still a placeholder list — its real per-size layout is a separate task.

## Working agreements

- **Do not run git commands** (`init`, `add`, `commit`, `push`, `tag`, …) unless explicitly asked.
- **Every Python module carries tests.** Nothing ships untested, including the dev scaffolding.
  No test may touch the network: `conftest.py`'s `fake_http` fixture replaces
  `urllib.request.urlopen` and raises on any unrouted host.
- **Never modify the Tesserae clone.** It is a read-only dependency; the only thing that belongs in
  it is its `.venv/`. Read its source freely — it is more authoritative than the docs. The one
  sanctioned touch is `devserver.py --update` (`_devsupport.update_clone`), a fast-forward pull plus
  `pip install -e ".[dev]"` and `playwright install chromium`; run it at the start of a session so
  the host you develop against is the one production runs.
- **Prefix non-plugin folders under `OutlookWidget/` with `_` or `.`.** That directory is a plugin
  scan root, so a plain `docs/` or `tests/` makes the loader log "plugin.json missing".
  `_tests/test_devsupport.py` asserts this stays true.

## Environment

| Thing | Path |
| --- | --- |
| Tesserae clone | `C:\Users\mjean\Documents\Sources\Forks\tesserae` (override with `TESSERAE_REPO`) |
| Python | `<clone>\.venv\Scripts\python.exe` (3.11, `pip install -e ".[dev]"`) |
| Plugins | `OutlookWidget\outlook_core\`, `OutlookWidget\outlook_week\` |

```powershell
cd OutlookWidget
C:\Users\mjean\Documents\Sources\Forks\tesserae\.venv\Scripts\python.exe devserver.py --update  # pull the clone, then serve
C:\Users\mjean\Documents\Sources\Forks\tesserae\.venv\Scripts\python.exe devserver.py
C:\Users\mjean\Documents\Sources\Forks\tesserae\.venv\Scripts\python.exe -m pytest . -q
C:\Users\mjean\Documents\Sources\Forks\tesserae\.venv\Scripts\ruff.exe check .
```

Iterate at `http://127.0.0.1:8765/_test/render?plugin=outlook_week&size=md` (`xs|sm|lg` too),
gallery at `/_test/widgets`, theme × style grid at `/_test/matrix`. `client.js` edits need only a
page refresh; Python edits auto-reload. The admin page is at `/plugins/outlook_core/`.

### How the plugins load from outside the clone

Tesserae scans its own `plugins/` plus `<data_root>/authored` and `<data_root>/marketplace`, and has
**no config flag** for extra directories. `_devsupport.py` therefore wraps
`app.plugin_loader.discover` to append `OutlookWidget/` to `additional_plugins_dirs` before
`create_app()` runs; `app_factory` calls it by module attribute, so the patch also covers the
in-process rediscover. `devserver.py` and `conftest.py` both go through that one module. No symlink,
junction or copy is involved. If upstream changes `discover()`'s signature,
`_tests/test_devsupport.py` fails immediately; fallbacks are a directory junction
(`cmd /c mklink /J`, no admin needed) or a Docker bind-mount onto `/app/data/marketplace`.

## Architecture notes

- Plugins cannot import each other. `outlook_week` reaches the core through
  `current_app.config["PLUGIN_REGISTRY"].get("outlook_core").server_module`, the same way
  `calendar_day` reaches `calendar_core` in the clone.
- **A plugin's own modules can't import each other either.** The host loads `server.py` by file
  path as `_tesserae_plugins.<id>.server` and never creates the parent packages, so `from . import
  family` has nothing to resolve against and the plugin folder isn't on `sys.path`. `server.py`'s
  `_sibling()` loads `family.py` explicitly; that is the only supported route to a second module.
- **`load_events_detailed(calendar_ids, start, end, *, cfg, data_dir)` is the contract** between the
  two plugins. Keep its event shape stable — later views (`outlook_next`, `outlook_day`) reuse it.
  Cancelled and declined events are filtered there, once, so every widget agrees.
- **`resolve_events(events)` is the second half of that contract.** It annotates each event with
  `members` / `routine` / `title` from the family rules in `outlook_core/family.py`, so no widget
  needs to know how ownership is configured. Rules are *all applied*, not first-match-wins: an
  event can belong to two people. The letter-prefix matcher (`L: Klavier`, `PM: Elternabend`) only
  fires when **every** captured letter maps to a member — that guard is what stops `OK Meeting`
  losing its first word, and it has a test.
- The family lives in `data/plugins/outlook_core/family.json`, not in `settings`: the manifest
  schema can't express a nested list of members and rules. Members take accent slots **2-6**;
  slot 1 is the design system's alerts/"now" role and `clean_config` refuses it.
- Auth is the OAuth device-code flow. Only the refresh token persists
  (`data/plugins/outlook_core/token.json`); refresh tokens rotate, so always store what comes back.
- `GET /me/calendars/{id}/calendarView` expands recurrences server-side — no RRULE handling here.
  `Prefer: outlook.timezone="<IANA>"` returns local times; a fixed-offset tz has no IANA key, so
  `_zone_name()` falls back to UTC.
- Graph responses are cached per calendar and window for 10 minutes, keyed on a day-snapped window
  so the key doesn't move with the clock. On an upstream failure a **stale cache is served rather
  than an error** — the right trade for a panel.
- `capability_scope()` is applied in exactly one place upstream (`app/composer.py:718`, around the
  widget's `fetch()`), so **`outlook_week` must declare the Graph hosts** in `requires:` even though
  the HTTP code lives in the core. Admin routes and `choices()` run unscoped.
- Reusable host helpers: `app.tz_resolve.app_timezone`, `app.calendar_time.all_day_event_overlaps_date`,
  `app.plugin_http.decode_content_encoding`, `current_app.config["SETTINGS_STORE"]`.

## Widget contract essentials

Authoritative: `_docs/widgets.md` and `_docs/widget-design-system.md`. Schema:
`<clone>/schema/plugin.schema.json`. Reference widgets: `<clone>/plugins/weather_now`,
`calendar_day`.

- `client.js` exports `default function render(shadow, ctx)` painting into a Shadow DOM. Be
  **idempotent** — overwrite `shadow.innerHTML`, never append.
- `ctx` = `{ cell: {w, h, size, plugin_id, options}, panel, font, data, preview }`; `ctx.data` is
  `fetch()`'s return value. The test-render page stamps it on the cell as `data-data`, which is what
  the smoke tests assert against.
- `server.py:fetch()` must **never raise** — return `{"error": "..."}` with a sentence a person can
  act on ("Not signed in to Outlook yet…", not `HTTPError: 401`).
- Link `/static/style/spectra-widgets.css` inside the shadow root; use the `.w` / `.w-title` /
  `.w-body` shell with one archetype body class (`.list-body` or `.cal-body` suit these widgets).
- Paint only from Spectra semantic tokens (`--bg`, `--surface`, `--surface-sunken`,
  `--text-primary/-secondary/-muted`, `--accent-1..6`, `--on-accent`). No hard-coded hex.
- **No** borders, animations, transitions, `requestAnimationFrame`, client-side `fetch`, or font
  loading. `ph-bold` for big icons, never `ph-fill`.
- Design for all four sizes (xs 180×180, sm 380×240, md 640×400, lg 1200×800) and check each —
  `sm`/`xs` are where layouts break. Hero-over-list layouts use
  `grid-template-rows: auto minmax(0, 1fr)`, never `1fr auto`.
- Don't add a `variant` cell option, and don't name one `label` (the host overwrites it with the
  app-level place name).
- Panel text goes through `ctx.t(key, fallback)` with the English string as the fallback, and every
  key lives in `strings/en.json` + `strings/de.json` (`_tests/test_manifests.py` keeps the three in
  step). Weekday/month labels come from `Intl.DateTimeFormat(ctx.locale, …)`, never a `["MON", …]`
  table. `tools/*_render.py --locale de` screenshots a translation against the older dev clone.

## Publishing

Community catalog, as a bundle: `id: "outlook"`, `folders: ["outlook_core", "outlook_family", "outlook_month"]`.
`outlook_week` is deliberately out until its `client.js` is a real render rather than the
placeholder debug list; adding it is one word in `BUNDLE_FOLDERS` plus the catalog entry.

**Releases are automatic.** `.github/workflows/release.yml` runs on every push to `main`, derives
the version from the commit log and publishes a tagged GitHub release with the bundle tarball
attached, then prints the ready-to-paste `widgets.json` block (version + URL + sha256) into the run
summary. Nobody edits a version number: `plugin.json` carries the sentinel `0.0.0-dev` in git and CI
stamps the real value into the staged copy.

| Commit | Result |
| --- | --- |
| `feat!:` or a `BREAKING CHANGE:` footer | major |
| `feat:` | minor |
| `fix:` / `perf:` | patch |
| anything else (`docs:`, `chore:`, …) | no release at all |

Shipping is then: merge a conventional commit, copy the printed `release` block into a one-hunk PR
against [`dmellok/tesserae-widgets`](https://github.com/dmellok/tesserae-widgets). Only the first
submission is bigger — it needs `screenshots/outlook/lg.png` and the full entry.

**Two distribution paths, one release.** `catalog/widgets.json` is the source of a private one-entry
index; each release republishes it to the **`catalog` branch**, and production (the Home Assistant
add-on, which has no filesystem you can reach) points `marketplace_index_url` at
`raw.githubusercontent.com/.../OutlookCalendar/catalog/widgets.json` and installs through Browse.
Same release asset, same install path, no waiting on review — see `catalog/README.md`. The official
catalog PR is the same entry pasted somewhere else.

The separate branch is not a preference: `github-actions[bot]` cannot push to a protected `main` on
a personal repo (classic `bypass_pull_request_allowances` and ruleset `Integration` bypass actors
both 422 there). It also means the publish cannot re-trigger the pipeline, since nothing triggers on
that branch.

Two constraints the workflow asserts, because both are easy to re-break:

- **The tarball's direct children must be the plugin folders.** `_detect_layout` in the host's
  `app/marketplace.py` unwraps a single-entry root envelope, then requires every remaining child to
  hold a `plugin.json`. GitHub's own `archive/refs/tags/*.tar.gz` therefore *cannot* work here —
  this repo's root has `AGENTS.md` beside `OutlookWidget/` and the plugins sit two levels deeper.
  The release asset is built from a staging dir for exactly this reason (and is byte-reproducible,
  so the pinned sha256 can be re-derived from the tag).
- **The entry's `folders` must match those children exactly.** Catalog CI and the installer both
  check it.
