# Family week — design

`outlook_family` draws the coming days as a family timetable on a Spectra e-ink panel: one column
per day, every appointment carrying who it belongs to. It is built to answer one question from
across a room — **who is busy when** — and everything below is in service of that, or of the second
constraint that follows from it: on a panel you read at a distance, the event's *title* is the
scarcest resource on screen.

Ownership is not something Outlook records. The household encodes it informally — a category, a
word in the title, a letter prefix like `L: Klavier`, or the calendar an event lives on — so the
plugin turns those conventions into configurable rules.

## Where things live

| Path | What it holds |
| --- | --- |
| `outlook_core/family.py` | Members, rules, matching, title stripping. No Flask, no Graph. |
| `outlook_core/server.py` | Graph + token handling, the family API, the admin page routes. |
| `outlook_core/templates/outlook_core/index.html` | Sign-in, calendars, and both family editors. |
| `outlook_family/server.py` | Window, grouping, member filtering. |
| `outlook_family/client.js` | The whole timetable renderer. |
| `data/plugins/outlook_core/family.json` | The saved family (schema `SCHEMA = 1`). |

## The panel

**Window.** Always starts today, 3–7 days wide (default 7). Hours are a fixed window, default
07:00–21:00, set per cell. Sizes `md` and `lg` only.

**Day columns.** Every day in the window gets a column whether or not anything is in it. Weekends
are tinted; today's date sits in an accent-1 chip and carries the now-line.

**Bands** run across the top, spanning the day columns they cover: all-day events, and any timed
event lasting 24 hours or more.

**The routine gutter** is a narrow strip down the left of each day holding events a rule marked
*routine* — school, office hours. Labels are set vertically. Width is 15px per member who has
routine that day, and each member keeps the same slot every day, so the gutter reads as a stable
pattern of who is tied up. A day with no routine has no gutter at all.

**Appointment blocks** fill the rest of the column. Only non-routine events compete for that width;
concurrent ones split it by greedy packing.

**Edges.** An event wholly outside the hour window becomes a counted chevron (`▲2`, `▼1`) at the
column's edge, tinted with its owner's colour. An event that straddles an edge is drawn clamped
with a triangle on the cut edge, pointing the way it continues.

### How a person is drawn

Four marks, any of which can fail:

1. **Accent slot** — one of `--accent-2` … `--accent-6`, never a hex.
2. **Fill pattern** — `solid`, `diag`, `dots`, `horiz`, `cross`.
3. **Owner stripe** — a 4px child element down the block's left edge, split into segments when an
   event has several owners.
4. **Initial** — a one-letter chip in the block's bottom-right corner.

### How much a block says

Decided by the block's own pixel height, via container queries on the block (`ofev`). As it
shrinks: the time goes first, then the owner marks, then the title. Note these query the block's
**content box**, so a tile measures a couple of pixels less than its laid-out height.

| Content height | Title | Time | Marks |
| --- | --- | --- | --- |
| ≥ 62px | 2 lines, if the column is wide enough | yes | yes |
| 35–61px | 1 line | yes | yes |
| 27–34px | 1 line | — | yes |
| 21–26px | 1 line, reserving right-hand room for the marks | — | yes |
| 18–20px | 1 line, full width | — | — |
| ≤ 17px | — | — | — |

Routine bars drop their vertical label below 42px (`ofbar`). The cell's own width rules use a
separate named container (`ofcell`).

## Who owns an event

**Members** carry a name, a letter, an accent slot, a pattern, and an `everyone` flag for the
household member. The letter does double duty: it is drawn on tiles, and it is what a title prefix
matches against, so letters must be unique.

**Rules** are an ordered list. Each has a matcher, the members it points at, and two flags:

| Matcher | Matches on |
| --- | --- |
| `prefix` | A leading run of up to 4 letters — `L: Klavier`, `LN Schwimmbad`, `PM - Elternabend` |
| `category` | An Outlook category, case-insensitive |
| `contains` | A substring of the subject, case-insensitive |
| `regex` | A regular expression against the subject |
| `calendar` | The source calendar's id or name |

- **strip** removes the matched text from the title the panel draws.
- **routine** sends the matches to the gutter.

`outlook_core.resolve_events()` applies every matching rule and returns each event annotated with
`members`, `routine`, and a `title` that has any stripped spans removed and whitespace collapsed.

## Configuration

The family belongs to the install, not to a dashboard cell, so it is configured once on
**Plugins → Outlook** and read by every `outlook_*` widget. Per-cell options cover only what varies
by panel: layout, days, hour window, which members to show, the routine gutter, calendars, and
locations.

The rules editor previews against real events from the coming week: a per-rule hit count, a
calendar picker to scope the preview, and a tray listing events no rule claimed.

---

## Decisions

### Scope and shape

**D1 — A separate `outlook_family` plugin, not a mode on `outlook_week`.**
Both read the same `load_events_detailed()` contract from `outlook_core`. Neither grows a mode
switch, and the core keeps one stable event shape.

**D2 — The timetable is one of several possible renderers.**
A `layout` cell option ships even though it has a single value, so another renderer can be added
later against the same resolved data without a second widget.

**D3 — `md` and `lg` only.**
Seven columns and a fourteen-hour axis cannot work at `xs` (180×180) or `sm` (380×240).

**D4 — The window always starts today.**
A family panel answers "what is coming". A fixed Mon–Sun week empties out as the week wears on and
spends columns on days that have passed.

**D5 — The hour window is fixed, never auto-fitted.**
Auto-fitting to the busiest event makes the grid change height between refreshes, which reads as
instability on a panel you walk past. Out-of-window events are signalled instead (D6, D7).

**D6 — Events wholly outside the window become counted edge chevrons.**
Enough to make you look; never enough to rescale the panel.

**D7 — Events straddling the window are clamped and marked with a triangle on the cut edge.**
Clamped without one, an event starting at 05:30 looks exactly like one starting at 07:00. The
triangle is centred, so it lands in the gap between the left-aligned time and the right-aligned
owner mark. It gets no reserved band of padding — that cost the block a line of title, which is the
worse trade.

**D8 — Anything lasting a day or more is a band, not a block.**
A trip from Sunday afternoon to Friday morning is not "busy 07:00–21:00" on the Wednesday between;
as a block it fills the column and pushes out what that day really holds. The threshold is a full
24 hours rather than "touches two dates", so an overnight train at 22:00 still draws as a block on
both its days — that is real, readable timing.

**D9 — Every day gets a column, even an empty one.**
A week that silently loses Wednesday is worse than an empty column.

### Identity

**D10 — Members take accent slots 2–6; accent-1 is reserved.**
Spectra fixes accent roles by position, and accent-1 is alerts / "now". It paints the now-line and
today's chip. `clean_config` refuses it for a member.

**D11 — Colour is never enough on its own.**
Every member also carries a pattern and a letter. On a black-and-white panel all six accent slots
collapse to the same black and every `-soft` tint to white; the pattern and the letter are what
still tell people apart. Any widget change must be checked against the `paper` theme.

**D12 — The owner's initial is on every tile that has room for it, bottom-right.**
Five hues are more than anyone wants to memorise, and the legend at the top of the panel is a long
way from a block in Thursday's column. It is absolutely positioned, so it costs the title no width
and leaves the time on its own line under the title.

**D13 — A shared event splits its owner stripe rather than prefixing the title.**
The stripe is a child element, not a `border-left`, so several owners can be painted as hard-stop
gradient segments. Naming people in the text would spend the width the title needs.

**D14 — Members always appear in configuration order.**
Resolved owners are sorted by their position in the member list, so stripe segments and gutter
slots put the same person in the same place every time.

**D15 — Unassigned events render grey, at full width, and are never hidden.**
An event disappearing because a rule stopped firing is the failure nobody would notice. They
survive the member filter for the same reason.

### Spending pixels

**D16 — Routine goes to a vertical gutter.**
School and office hours would otherwise flood the grid and squeeze every real appointment into a
sliver. Vertical labels mean a long word costs height, which the bar has, instead of width, which
it does not.

**D17 — Only non-routine events compete for the column width.**
This is what takes a typical weekday from a four-way split to a single full-width block.

**D18 — Text density is decided in CSS by the block's pixel height, not in JS by percentages.**
A threshold in percent of the window is 40px at `lg` and 17px at `md`; the `md` case then runs the
title into the time. Each block is its own size container.

**D19 — Named containers on both scales.**
The blocks are size containers, so an *unnamed* cell-width query would resolve against a 130px
block instead of the cell. `ofcell`, `ofev` and `ofbar` keep the scales apart.

**D20 — The widget uses the zoom-locked `.w-title` bar, not the calendar archetype's header.**
`.cal-head-title` is sized with `--fs-jumbo`, which is right where the date is the content and
wrong here, where it eats a third of the panel the grid needs.

### Ownership rules

**D21 — Every matching rule applies; it is not first-match-wins.**
That is how one "Elternabend" belongs to two people.

**D22 — A prefix rule fires only when every letter it captured maps to a member.**
Otherwise `OK Meeting` and `CH Ferien` look exactly like a prefix and silently lose their first
word. This guard is the reason the bare-letter form (`L Turnen`) is safe to enable at all.

**D23 — Stripping is per rule, and the original subject is kept.**
The panel draws `title`; `summary` still carries what Outlook holds. A matched prefix moves out of
the text and into the block's colour and stripe, buying the title back its width.

**D24 — The family lives in `family.json`, not in the manifest settings.**
The plugin settings schema cannot express a nested list of members and rules.

**D25 — Ownership is resolved server-side, once.**
`resolve_events()` hands widgets events that already know their members, so no widget needs to know
how ownership is configured, and `load_events_detailed()`'s shape stays unchanged.

**D26 — Deleting a member takes their rule references with them.**
Any rule left pointing at nobody is dropped rather than failing validation — removing a person
should never leave the page refusing to save.

### The admin page

**D27 — The family is per-install, not per-cell.**
You would never want to re-enter it for a second panel. Cell options cover only what genuinely
varies by panel.

**D28 — No JavaScript.**
Both editors are plain parallel-list POSTs: rows submit their fields in DOM order, checkboxes
submit their row index, and the blank row at the bottom is what "add" means.

**D29 — Fields are borderless until hovered or focused.**
At rest the page reads as a list of people and a list of sentences; the controls appear only where
you are working.

**D30 — Members are chips, not a multi-select.**
The whole family fits on one line and which people a rule points at reads at a glance. They are
still checkboxes, so `getlist` sees exactly what a multi-select would.

**D31 — Delete marks the row; nothing is destroyed until you save.**
The bin is a styled checkbox. The pending row is tinted, struck through and dimmed, so a mis-click
is visible and reversible.

**D32 — The rules editor previews against real events, scoped to a calendar.**
Per-rule hit counts and an "matched nothing this week" tray are what keep a rule list maintainable:
a rule that quietly stops firing is otherwise invisible until someone misses a dentist appointment.
The preview costs one Graph window, cached ten minutes, and is skipped entirely until the family
has members.

### Structure

**D33 — A plugin's own modules are loaded explicitly.**
The host loads `server.py` by file path as `_tesserae_plugins.<id>.server` and never creates the
parent packages, so `from . import family` has nothing to resolve against. `server.py`'s
`_sibling()` is the only supported route to a second module.

## Known limits

- At `md` with a wide hour window a one-hour tile is about 14px — room for the title *or* the owner
  mark, not both, and the title wins. Narrowing `hour_start`/`hour_end` brings the marks back.
  `lg` is unaffected.
- A timed event belonging to the household member draws as an ordinary block, not as a band across
  the day. Only all-day and ≥24h events band (D8).
- All-day bands carry their owner mark inline at the left, before the label, rather than in a
  corner: a single-line bar has no meaningful bottom-right.
- Row tinting for a row marked for deletion uses `:has()`. Where that is unsupported the bin still
  works; the row simply does not tint.

## Checking a change

Beyond `pytest` and `ruff`, the things that break quietly:

- `/_test/render?plugin=outlook_family&size=lg` at `days=7` and `days=5`, then `size=md`.
- `/_test/matrix` — in particular the **`paper`** theme, where every accent is black. If two
  members become indistinguishable there, the patterns are not doing their job (D11).
- The admin page with a family configured and a week of real events, so the counts and the
  unmatched tray are populated (D32).
