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

**Day columns.** Every day in the window gets a column whether or not anything is in it, on a
white ground with a solid dark rule between neighbours. Today's date sits in an accent-1 chip and
carries the now-line.

**Bands** run across the top, spanning the day columns they cover: all-day events, and any timed
event lasting 24 hours or more.

**Routine bars** are events a rule marked *routine* — school, office hours — drawn as bars one
owner mark wide (29px) at the left of the day, over their own hours only, with the label set
vertically at caption size (a narrower bar with smaller type was tried first and read as a smear of
glyph sides). The owners' marks sit at the foot of the bar, stacked when there are several, each
the full width of the bar — which is why a bar has no stripe of its own. A bar shorter than one
mark drops the marks; shorter than about two lines, the label too. Concurrent
routines pack side by side; one that starts after another ends takes the first column again.

**Appointment blocks** take what is left. Only non-routine events compete for that width;
concurrent ones split it by greedy packing, per overlapping cluster — a lone afternoon appointment
stays full width even when the morning is a three-way collision. A block is indented past the
outermost routine bar that overlaps it in time, and not otherwise: a 16:00 appointment on a school
day has the whole column, a 10:00 one sits beside the school bar.

**Edges.** An event wholly outside the hour window becomes a counted chevron (`▲2`, `▼1`) at the
column's edge, tinted with its owner's colour. An event that straddles an edge is drawn clamped
with a triangle on the cut edge, pointing the way it continues.

### How a person is drawn

Four marks, any of which can fail:

1. **Accent slot** — one of `--accent-2` … `--accent-6`, never a hex.
2. **Fill pattern** — `solid`, `diag`, `dots`, `horiz`, `cross`.
3. **Black stripe** — a 5px child element down the block's left edge. Always black: it is the edge
   that makes a white tile a tile, and it says nothing about who (see D13).
4. **Initial** — a one-letter chip in the block's bottom-right corner, painted in the member's tile.

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
- **routine** draws the matches as narrow bars.

`outlook_core.resolve_events()` applies every matching rule and returns each event annotated with
`members`, `routine`, and a `title` that has any stripped spans removed and whitespace collapsed.

## Configuration

The family belongs to the install, not to a dashboard cell, so it is configured once on
**Plugins → Outlook** and read by every `outlook_*` widget. Per-cell options cover only what varies
by panel: layout, days, hour window, which members to show, routine as bars, calendars, and
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
and leaves the time on its own line under the title. The mark wears the member's own tile — the
same one the legend chip and the block wear — with a black letter and a 1px black edge, never the
slot's solid line ink: two slots can share a line ink (red and orange are both `R`, green and lime
both `G`, blue and pink both `B`), and a solid mark could not tell those members apart while their
tiles can. The same goes for the dot on an out-of-window chevron (D6).

**D13 — The stripe is always black; a shared event says so with its fill, never with the title.**
The stripe began as the owner's line ink, split into segments per owner. It went black once the
fill segments (D38) took over saying who shares a block, and once the line-ink collisions (D12)
made it unreliable anyway; a black edge is also what survives the paper theme unchanged. Naming
people in the text would spend the width the title needs, so ownership is still never in the title.

**D14 — Members always appear in configuration order.**
Resolved owners are sorted by their position in the member list, so fill segments put the same
person in the same place every time.

**D15 — Unassigned events render grey, at full width, and are never hidden.**
An event disappearing because a rule stopped firing is the failure nobody would notice. They
survive the member filter for the same reason.

### Spending pixels

**D16 — Routine is a narrow vertical bar over its own hours, not a gutter.**
School and office hours would otherwise flood the grid and squeeze every real appointment into a
sliver. Vertical labels mean a long word costs height, which the bar has, instead of width, which
it does not. The bars began as a gutter — a strip reserved down the whole day, one fixed slot per
member — and that reserved the width all day for something that ends at noon. Now a bar exists only
between its own start and end, routines pack among themselves by time, and an appointment is
indented only past the bars that overlap it. The stable "who is tied up" slot per member went with
the gutter; the fill and the vertical label carry that.

**D17 — Only non-routine events compete for the column width.**
This is what takes a typical weekday from a four-way split to a single full-width block. The width
they compete for is the lane minus whatever routine bars overlap the block, in pixels — so the
split is a `calc()` of a percentage and a pixel indent.

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

**D34 — Packing runs in hours, and the split is scoped to the cluster.**
`packColumns` takes and compares `top`/`bottom` in hours; `pctSpan` runs afterwards, per block.
Feeding it percentages on one edge and hours on the other made every column look free, so
overlapping events stacked at full width instead of sitting side by side — the bug this decision
exists to prevent. Column count is then per run of events that actually touch, not per day, so one
busy morning does not narrow the rest of the column.

**D35 — The timetable paints its own white ground and solid black day rules.**
Every theme's `--surface` is an off-white tint, and on a Spectra panel a tint is a field of
dithered dots behind the whole grid. The widget overrides it to `#fff` (and `--bg`, which the edge
chips use to mask the hour rule). The 3% weekend wash went for the same reason, and the day
separators are `#000` rather than `--surface-sunken`: a faint rule dithers away, and seven columns
of tiles without one read as a single field. Spectra's "no internal borders" rule is deliberately
broken here — on this panel the border is the thing that survives. The same goes for text: every
text role (`--text-primary/-secondary/-muted`) is forced to `#000` and the 75% opacity on routine
blocks is gone, because a grey label is a dithered label; hierarchy is size and weight only.

**D36 — Everything the widget paints is a native ink; no theme accent, no tint.**
Spectra 6 lays down black, white, yellow, red, blue and green and nothing else, and Tesserae's
quantiser leaves a pixel alone only when it already sits exactly on one of those. Every theme
accent (ochre, moss, teal, …) and every `-soft` tint is therefore a dither field on the panel, so
the widget stops reading the accent tokens altogether: accent-1 — the now-line and today chip — is
black, and members come from the widget's own `SLOTS` table (D37). White tiles need an edge to be
tiles, so blocks, bars and bands carry a 1px black outline (an outline, not a border, so the size
container's height is unchanged). The hour rules are a 1px black line cut into dashes — the only "faint" this panel can do — drawn
on a `::before` of the lane and masked to 2px-on / 3px-off, so whatever ground is underneath shows
through the gaps. Every chip and mark has a 1px black edge.

**D37 — Member colours are 2×2 dither tiles, chosen by eye on the panel.**
`tools/patch_test.py` renders a card of pure inks, tints and two-ink mixes with nothing left for
the pipeline to dither; sent through Tesserae with a nominal palette it arrives byte-exact, so
what it shows is what the panel can do. The eight fills picked from it are four inks at 50 % over
white (slots 2–5: yellow, green, blue, red) and four mixes (6 orange `RY/YR`, 7 lime `GY/WG`,
8 pink `RW/WB`, 9 olive `YK/KY`), all light enough that text is black on every one — a single
text colour is what lets a shared event show two fills under one title (D38). Each is a 2×2 SVG tile
with `shape-rendering: crispEdges` at a 2px `background-size`, percent-encoded so it can sit in an
inline style attribute; Chromium paints it pixel-exact and the quantiser passes it through. Each
slot also names a solid `line` ink for the stripe, marks, edge dots and pattern stroke, and the
mark letter is black on yellow, white otherwise. `tile()` takes any n×n spec and returns the url
with its matching `background-size`, so 4×4 tiles work the same way; the weekend ground is one
(`RWWW/WWWW/WWRW/WWWW`, red at 12.5 %), which is what replaced the old 3 % wash. `family.ACCENT_MAX` is 9; `ACCENT_HEX` gives the
admin page the tile's average colour, which is what the eye sees. Note the dependency this creates:
the device must be on a nominal palette (a profile with the pure primaries, or none, and no legacy
`calibrated` flag), otherwise even pure inks get speckle — see the calibration notes in the README.

**D38 — A shared event shows every owner's fill, side by side.**
Painting a shared event in its first owner's colour made it that person's event; the split stripe
(D13) alone was too thin to correct that. Now a block with two or more owners carries one fill
segment per owner — equal vertical bands in stripe order, each with that owner's tile and pattern —
under the text, and the block's own fill is switched off. This is only possible because every slot
takes black text (D37); with owners disagreeing on text colour, the title would have crossed a
ground it could not be read on. The alternatives were mocked on the panel with
`tools/shared_mock.py`: a diagonal split (two owners only), an interleaved blend (a colour nobody
owns), a white fill (indistinguishable from unassigned). Bands and routine bars split the same way.

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
  members become indistinguishable there, the patterns are not doing their job (D11). Since D36
  the widget paints its own palette, so the theme only changes fonts; the render should look the
  same everywhere.
- A 1:1 screenshot of `/_test/render?plugin=outlook_family&size=lg` should contain only the six
  inks outside text edges: a colour census of a legend chip is the quick check that the D37 tiles
  are still pixel-exact (a stray `background-size`, a quote in the data URI, or a scaled viewport
  all break it silently).
- The admin page with a family configured and a week of real events, so the counts and the
  unmatched tray are populated (D32).

---

# Family month — design

`outlook_month` draws the coming weeks as a family wall calendar: a column per weekday, a row per
week, a chip per appointment. It answers a different question from the timetable — **which day**,
not **when today** — and the decisions below are the ones that differ. Everything about how a person
is painted (D10–D14, D36–D38), ownership (D21–D26) and the admin page (D27–D32) is shared verbatim:
the widget carries the same `SLOTS` table, tiles, stripe, fill segments and marks, so a person looks
the same on both panels.

## Where things live

| Path | What it holds |
| --- | --- |
| `outlook_month/server.py` | Window, routine filter, grouping by day. |
| `outlook_month/client.js` | The grid renderer. |
| `tools/month_mock.html` | The layout mock-ups the grid was chosen from (grid vs. Familienplaner). |

## The panel

**Window.** The current week and the next few (2–6 weeks, default 4), starting on Monday or Sunday.
Always the *whole* current week, so at most six days on the panel are already over.

**Weeks** are rows sharing the height equally; **days** are cells with the number top-right, today's
in a black chip, the first of a month prefixed with its name. Weekends carry the same red tile
ground as the timetable.

**Chips** are one line of title with the owner's stripe at the left and marks at the right, in the
label size. A day with four chips or fewer lets each title wrap to a second line. What does not fit
becomes a `+n`.

**Bands** — all-day events and anything lasting 24 hours or more — run across the cells they cover
in a lane strip under the day numbers, cut per week row. A cell reserves room only for the lanes
that cross it, so a weekend trip costs Monday nothing.

**Past days** are drawn like any other day; only the number drops to regular weight.

**md** (640×400) keeps the grid and drops the titles: each chip collapses to its owner marks, laid
out in a row, and the legend goes because the marks are the legend.

## Decisions

**M1 — A separate plugin, not a `layout` value on `outlook_family`.**
D2 anticipated a second renderer on the same data, and this one is that — but its window (weeks,
not days), its cell options (no hour window, no gutter) and its size support are all different, so
sharing a manifest would have meant every option carrying a "not for this layout" note. The grouping
helpers are copied, not imported: plugins cannot import each other (D33), and moving them into the
core was not worth touching the week widget for.

**M2 — The window starts with the current week, not on the first of the month.**
A calendar month is mostly history by the 20th. Starting on this week's Monday means the panel is
never more than six days stale and the grid always has the same number of rows, so it keeps its
shape between refreshes (the same argument as D4 and D5).

**M3 — No times.**
At 226px a column cannot hold a clock, a title and two marks; the title wins (it is the scarce
resource, as in the week widget), and the clock is what the week widget is for. Dropping it is what
let the chips go up to label size and the titles wrap.

**M4 — Routine is dropped, not drawn small.**
School and office hours on every weekday of four weeks say nothing and would take the first two
lines of every cell. The narrow-bar trick (D16) needs a time axis to sit beside. An option brings them
back as ordinary chips for anyone who wants them.

**M5 — Past days are painted like every other day.**
They were hollow at first — outline, stripe and initial, no fill — on the argument that the panel
has no grey (D35) and a fill-less chip reads as "done". On the panel it read as a chip that had
lost its colour: the first event of the week looked broken while the same event a week later was
fine. With at most six past days on the panel there is little to gain from marking them, so the
treatment and its option went; the today chip is enough.

**M6 — Overflow is measured, not predicted.**
The chips are laid out by the browser and then trimmed: any chip whose bottom edge falls past its
cell is hidden and a `+n` takes the first hidden slot. Container queries could not do this — they
know a chip's height, not how many siblings sit above it — and predicting it from a font size would
break the moment the base size changes.

**M7 — Shared events span nothing.**
In the grid a shared event is one chip in one cell with a split fill and two marks (D38). The
split is stacked — one horizontal band per owner — where the timetable's is side by side: a chip
is one line tall and wide, so a vertical seam cut the title into two colours mid-word, while a
horizontal one runs along it, and on a two-line chip each owner simply gets a line. The
Familienplaner layout in `tools/month_mock.html` — a column per person — is the one where a shared
event spans two columns; it was mocked and not chosen, because its title budget only pays off at
`lg` and it has no `md` form at all.

**M8 — A month change is flagged on the cell, not on the row.**
The first of a month carries its name in the corner. A heavier rule between the rows would be the
paper convention, but every week row already has a 1px black rule and the panel has no second
weight to give it — a 2px rule next to a 1px one reads as a mistake, not a boundary.

**M9 — Same title, same day: one chip, every owner.**
Without times, `L: Klavier` and `N: Klavier` on a Tuesday say nothing a single `Klavier` with two
marks does not, and the same event on two people's calendars is the same thing twice. The server
folds them after the rules have run (so the comparison is on the stripped title, case-insensitive),
takes the union of owners in roster order, and marks the survivor `merged: n`. Bands fold only when
they cover the same days. This is a month-only rule: on the timetable two copies at different
times are different information.

**M10 — Marks wear the member's tile and the stripe is black, as on the timetable.**
Two slots can share a line ink — red and orange are both `R`, green and lime both `G`, blue and
pink both `B` — so a solid mark cannot tell those members apart, while their chips and legend tiles
can. The mark is therefore painted exactly like the legend chip: the member's tile, black letter,
1px black edge (D12). The stripe is plain black (D13). The month grid is where this was noticed —
four weeks of chips put red and orange marks side by side far more often than one week of blocks.

## Checking a change

- `/_test/render?plugin=outlook_month&size=lg` at `weeks=4` and `weeks=6`, then `size=md`.
- A week with a band crossing the row boundary (a holiday starting Saturday), a weekend band next
  to a Monday with chips (they must start at the top), a day with seven or more chips (the `+n`),
  and a first-of-the-month inside the window. `tools/month_render.py --fixture` has all of these.
- The same paper-theme and colour-census checks as the week widget: the tiles are the same code.
