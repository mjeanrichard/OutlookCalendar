/**
 * outlook_family, the family timetable.
 *
 * One column per day starting today, a fixed hour window down the side, and
 * every block wearing its owner's accent slot, fill pattern and — when more
 * than one person is involved — their initials.
 *
 * Three rules about spending pixels, because the title has first claim on all
 * of them:
 *
 *   1. Routine (school, office hours) is drawn as a narrow bar with its
 *      label set vertically, and only over its own hours: an appointment
 *      beside it is indented past it, one after it has the whole column.
 *   2. A shared event splits its owner stripe between the people involved
 *      rather than prefixing the title with their names.
 *   3. Out-of-window events become a counted chevron at the column's edge.
 *      The grid keeps its shape between refreshes; nothing rescales.
 *
 * Ownership is resolved server-side by outlook_core's family rules, so every
 * event arrives with `members`, `routine` and an already-stripped `title`.
 */

// One routine bar: exactly one owner mark wide (1.3em of the mark's type,
// see .of-mark), which is also a caption-size line of vertical text plus a
// little air. Narrower than this and the label is a smear of glyph sides.
const BAR_PX = 29;
const DOW = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MONTH = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

// How much text a block can hold is decided in CSS, by container queries on
// the block itself — see styles(). It has to be: a threshold in percent of
// the visible window means 40px at lg and 17px at md, and the md case then
// runs the title and the time into each other. The block's own height in
// pixels is the only thing that actually answers the question.

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const size = ctx?.cell?.size ?? "lg";
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w size-${size}" data-widget="outlook_family">
        <div class="w-title"><h3>Familie</h3></div>
        <div class="w-body list-body">
          <div class="u-muted"><i class="ph-bold ph-warning-circle"></i> ${esc(data.error)}</div>
        </div>
      </div>`;
    return;
  }

  const members = new Map((data.members || []).map((m) => [m.id, m]));
  const days = Array.isArray(data.days) ? data.days : [];
  const hours = data.hours || { start: 7, end: 21 };
  const span = Math.max(1, hours.end - hours.start);
  const narrowRoutine = data.routine_gutter !== false;

  if (!days.length) {
    shadow.innerHTML = `
      ${css}
      <div class="w size-${size}" data-widget="outlook_family">
        <div class="w-title"><h3>Familie</h3></div>
        <div class="w-body list-body"><div class="u-muted">Nothing scheduled.</div></div>
      </div>`;
    return;
  }

  const lanes = days
    .map((day) => laneHtml(day, { members, hours, narrowRoutine, data }))
    .join("");

  const heads = days.map((day) => headHtml(day)).join("");
  const bands = bandsHtml(data.bands || [], days.length, members);

  // The zoom-locked .w-title rather than the .cal-head hero: that header is
  // sized with --fs-jumbo, which is right when the date is the content (as in
  // calendar_day) and wrong here, where it would eat a third of the panel the
  // grid needs.
  shadow.innerHTML = `
    ${css}
    <style>${styles(span)}</style>
    <div class="w size-${size}" data-widget="outlook_family">
      <div class="w-title">
        <i class="ph-bold ph-users-three" style="color:#000"></i>
        <h3>Familie</h3>
        ${legendHtml(data.members || [])}
        <span class="w-title-meta">${esc(rangeLabel(days))}${data.stale ? " · cached" : ""}</span>
      </div>
      <div class="w-body cal-body">
        <div class="of-grid" style="grid-template-columns:2.6em repeat(${days.length},minmax(0,1fr));
             grid-template-rows:auto ${bands ? "auto" : ""} minmax(0,1fr)">
          <div></div>
          ${heads}
          ${bands ? `<div></div><div class="of-bands"
             style="grid-column:2/-1;grid-template-columns:repeat(${days.length},minmax(0,1fr))">${bands}</div>` : ""}
          <div class="tt-hours">${hourLabels(hours)}</div>
          ${lanes}
        </div>
      </div>
    </div>`;
}

/* ---------- members ---------- */

// The panel's six inks. Everything the widget paints is one of these, pure,
// or a 2x2 tile of them (D36/D37): the quantiser leaves a native pixel alone
// and dithers anything else.
const INK = { K: "#000000", W: "#ffffff", Y: "#ffff00", R: "#ff0000", B: "#0000ff", G: "#00ff00" };

// An n x n pixel tile as an SVG background, from a string of n*n ink letters
// in reading order (the labels on tools/tile_test.py). crispEdges plus a
// background-size equal to the tile keeps every pixel on its ink; the panel
// then paints the tile verbatim. Returns the url() and the size to pair it
// with, since a 2x2 and a 4x4 tile need different background-size values.
function tile(spec) {
  const n = Math.round(Math.sqrt(spec.length));
  const rects = [...spec].map((c, i) =>
    `<rect x="${i % n}" y="${Math.floor(i / n)}" width="1" height="1" fill="${INK[c]}"/>`).join("");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${n}" height="${n}" shape-rendering="crispEdges">${rects}</svg>`;
  // Percent-encoded, so the value carries no quote of either kind: it is
  // written into an inline style attribute.
  return { url: `url('data:image/svg+xml,${encodeURIComponent(svg)}')`, size: `${n}px ${n}px` };
}

// The weekend ground: red at 12.5% on a 4x4 lattice, picked on the panel.
const WEEKEND = tile("RWWWWWWWWWRWWWWW");

// A solid black triangle, w x h pixels, pointing up or down, as an SVG
// background. Not a border trick: a border triangle's diagonals are
// anti-aliased, the up and down paths do not get the same grey pixels, and
// the panel's quantiser then turns those greys into two visibly different
// shapes. crispEdges gives a pixel-exact polygon, and the down one is the
// up one mirrored, so they are the same ink.
function tri(direction, w, h) {
  const points = direction === "up" ? `0,${h} ${w},${h} ${w / 2},0` : `0,0 ${w},0 ${w / 2},${h}`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" shape-rendering="crispEdges"><polygon points="${points}" fill="#000"/></svg>`;
  return `url('data:image/svg+xml,${encodeURIComponent(svg)}')`;
}

// Member slots, picked on the panel from the tile test (tools/tile_test.py),
// light enough that text is black on every one of them. `line` is the solid
// ink for the stripe, marks and pattern stroke. Slot 1 is the now-line /
// today chip and is never a person.
const SLOTS = {
  2: { fill: tile("YWWY"), line: "Y", text: "K" }, // yellow 50%
  3: { fill: tile("GWWG"), line: "G", text: "K" }, // green 50%
  4: { fill: tile("BWWB"), line: "B", text: "K" }, // blue 50%
  5: { fill: tile("RWWR"), line: "R", text: "K" }, // red 50%
  6: { fill: tile("RYYR"), line: "R", text: "K" }, // orange, R+Y 50
  7: { fill: tile("GYWG"), line: "G", text: "K" }, // lime, G+Y+W 2:1:1
  8: { fill: tile("RWWB"), line: "B", text: "K" }, // pink, R+B+W 1:1:2
  9: { fill: tile("YKKY"), line: "K", text: "K" }, // olive, Y+K 50
};

function accent(member) {
  const slot = Math.min(9, Math.max(2, Number(member?.accent) || 4));
  const s = SLOTS[slot];
  return {
    line: INK[s.line],
    fill: s.fill.url,
    fillSize: s.fill.size,
    text: INK[s.text],
  };
}

// Unassigned events are drawn, never hidden: an event vanishing because a
// rule stopped firing is the failure nobody would notice. Plain white, black
// stripe.
const UNOWNED = { line: INK.K, fill: "none", fillSize: "2px 2px", text: INK.K };

function paint(member) {
  const { line, fill, fillSize, text } = member ? accent(member) : UNOWNED;
  // The pattern stroke is the line ink itself: a colour-mixed stroke would
  // be a second dither field on top of the fill.
  return `--of-line:${line};--of-fill:${fill};--of-fill-size:${fillSize};--of-stroke:${line};--of-text:${text}`;
}

function ownersOf(event, members) {
  return (event.members || []).map((id) => members.get(id)).filter(Boolean);
}

function patternClass(owners) {
  const pattern = owners.length ? owners[0].pattern : "solid";
  return `of-p-${["solid", "diag", "dots", "horiz", "cross"].includes(pattern) ? pattern : "solid"}`;
}

// The stripe down the block's edge is always black. It used to carry the
// owner's line ink, split per owner on a shared block, but two slots share a
// line ink (red and orange are both R, green and lime both G, blue and pink
// both B), so it could not tell those members apart — and since D38 the fill
// segments say who shares a block. A black edge also survives the paper theme
// and any tile unchanged.
function stripeHtml() {
  return `<span class="of-stripe"></span>`;
}

// A shared event shows every owner's fill, side by side in the order the
// stripe uses (D13/D38): one absolutely positioned segment per owner, each
// carrying its own tile and pattern, painted under the text. The element's
// own fill is switched off so the first owner's colour does not show through
// between segments. One owner (or none) needs no segments.
function fillsHtml(owners) {
  if (owners.length < 2) return "";
  const step = 100 / owners.length;
  return owners
    .map((m, i) => `<span class="of-fillseg ${patternClass([m])}" style="${paint(m)};left:${(i * step).toFixed(2)}%;width:${step.toFixed(2)}%"></span>`)
    .join("");
}

// The style and class an owned block itself carries: its first owner's paint
// (line ink, text colour, and the fill when it is the only owner) and its
// pattern — or, when shared, no fill and no pattern of its own, since the
// segments bring theirs.
function blockPaint(owners) {
  const style = paint(owners[0]);
  return owners.length > 1 ? `${style};--of-fill:none` : style;
}

function blockPattern(owners) {
  return owners.length > 1 ? "of-p-solid" : patternClass(owners);
}

// Every owner's initial, on every block that has room for it. Colour alone
// asks you to remember five hues and go back to the legend to decode them; the
// letter says who outright.
function initialsHtml(owners) {
  if (!owners.length) return "";
  // The mark wears the member's own tile — the same one the legend chip and
  // the block wear — not the slot's solid line ink, which two slots can
  // share. Black letter on every tile (D37), 1px black edge so it is still a
  // mark on a block of the same fill.
  return owners
    .map((m) => `<span class="of-mark" style="${paint(m)}">${esc(m.letter || "·")}</span>`)
    .join("");
}

// The same marks parked in the tile's bottom-right corner. Absolutely
// positioned, so they cost the title no width and leave the time exactly
// where it was, on its own line under the title.
function marksHtml(owners) {
  const marks = initialsHtml(owners);
  return marks ? `<span class="of-marks">${marks}</span>` : "";
}

function legendHtml(roster) {
  if (!roster.length) return "";
  const chips = roster
    .map((m) => `
      <span class="of-key">
        <span class="of-key-chip ${patternClass([m])}" style="${paint(m)};color:${accent(m).text}">${esc(m.letter || "·")}</span>
        <span class="of-key-name">${esc(m.name || "")}</span>
      </span>`)
    .join("");
  return `<div class="of-legend">${chips}</div>`;
}

/* ---------- time ---------- */

// Parse through Date so the hour is in the renderer's local timezone rather
// than whatever offset is baked into the ISO string.
function hourOf(iso) {
  const d = new Date(String(iso));
  if (!Number.isFinite(d.getTime())) return null;
  return d.getHours() + d.getMinutes() / 60;
}

function dateKey(iso) {
  const d = new Date(String(iso));
  if (!Number.isFinite(d.getTime())) return "";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function fmtHm(iso) {
  const d = new Date(String(iso));
  if (!Number.isFinite(d.getTime())) return "";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// A timed event can run past midnight, and the server hands the same event to
// every day it touches. Only the real start day keeps the real start hour;
// only the real end day keeps the real end hour. Without this a Sunday 16:00
// → Friday 10:00 trip would redraw a 16:00 block on every day in between.
export function clampToDay(event, date) {
  const start = hourOf(event.start);
  if (start == null) return null;
  const startsHere = dateKey(event.start) === date;
  const endsHere = dateKey(event.end) === date;
  const top = startsHere ? start : 0;
  let bottom = endsHere ? hourOf(event.end) : 24;
  if (bottom == null || bottom <= top) bottom = Math.min(24, top + 0.5);
  return { top, bottom };
}

// Clamp each edge into the window *before* deriving the height, so a
// pass-through day of a multi-day event stops at the lane's edge instead of
// spilling past it.
export function pctSpan(top, bottom, hours) {
  const size = Math.max(1, hours.end - hours.start);
  const a = Math.max(0, Math.min(100, ((top - hours.start) / size) * 100));
  const b = Math.max(0, Math.min(100, ((bottom - hours.start) / size) * 100));
  return { top: a, height: Math.max(1, b - a) };
}

/* ---------- packing ---------- */

// An event that starts before the window, or ends after it, is drawn clamped
// to the edge — which on its own looks exactly like an event that really does
// begin at 07:00. These classes let the CSS put a small triangle on the cut
// edge so "there is more of this above/below" is visible at a glance.
export function clipClasses(top, bottom, hours) {
  const out = [];
  if (top < hours.start) out.push("clip-top");
  if (bottom > hours.end) out.push("clip-bottom");
  return out.join(" ");
}

// Greedy interval packing: concurrent events split the column between them.
// Routine is packed separately, into narrow bars, which is what usually keeps
// this down to a single full-width block on an ordinary weekday.
//
// `top`/`bottom` are hours, and they have to stay hours all the way through —
// packing against a percentage on one edge and an hour on the other silently
// finds every column free and stacks the day into one pile. The caller
// converts to percent *after* placement, per block.
//
// The split is per cluster of events that actually touch, not per day: a lone
// 16:00 appointment keeps the full width even when the morning is a three-way
// collision. Each placed item therefore carries its own `columns`.
export function packColumns(items) {
  const placed = [];
  let cluster = [];
  let ends = [];
  let clusterEnd = -Infinity;

  const flush = () => {
    const columns = Math.max(1, ends.length);
    for (const item of cluster) placed.push({ ...item, columns });
    cluster = [];
    ends = [];
  };

  items
    .slice()
    .sort((a, b) => a.top - b.top || b.bottom - a.bottom)
    .forEach((item) => {
      // Sorted by start, so once a start clears every end seen so far,
      // nothing later can reach back into the cluster we were building.
      if (item.top >= clusterEnd) flush();
      let column = ends.findIndex((end) => end <= item.top);
      if (column === -1) {
        column = ends.length;
        ends.push(item.bottom);
      } else {
        ends[column] = item.bottom;
      }
      cluster.push({ ...item, column });
      clusterEnd = Math.max(clusterEnd, item.bottom);
    });
  flush();
  return placed;
}

/* ---------- rendering ---------- */

function headHtml(day) {
  const classes = ["of-head"];
  if (day.is_today) classes.push("is-today");
  if (day.is_weekend) classes.push("is-weekend");
  const dow = DOW[new Date(`${day.date}T00:00:00`).getDay()] || "";
  return `
    <div class="${classes.join(" ")}">
      <span class="of-dow">${esc(dow)}</span>
      <span class="of-num">${esc(String(day.day ?? ""))}</span>
    </div>`;
}

function bandsHtml(bands, dayCount, members) {
  if (!bands.length) return "";
  // Greedy lane packing so two overlapping all-day bars stack instead of
  // colliding on the same row.
  const laneEnds = [];
  return bands
    .slice()
    .sort((a, b) => a.first - b.first)
    .map((band) => {
      let lane = laneEnds.findIndex((end) => end < band.first);
      if (lane === -1) {
        lane = laneEnds.length;
        laneEnds.push(band.last);
      } else {
        laneEnds[lane] = band.last;
      }
      const owners = ownersOf(band, members);
      const columns = Math.min(dayCount - band.first, band.last - band.first + 1);
      return `
        <div class="of-band ${blockPattern(owners)}"
             style="${blockPaint(owners)};grid-column:${band.first + 1} / span ${columns};grid-row:${lane + 1}">
          ${fillsHtml(owners)}
          ${stripeHtml()}
          ${initialsHtml(owners)}
          <span class="of-band-name">${esc(band.title || band.summary || "")}</span>
        </div>`;
    })
    .join("");
}

function laneHtml(day, opts) {
  const { members, hours, narrowRoutine, data } = opts;
  const all = (day.events || []).map((event) => {
    const clamped = clampToDay(event, day.date);
    return clamped ? { event, ...clamped } : null;
  }).filter(Boolean);

  const inside = all.filter((item) => item.bottom > hours.start && item.top < hours.end);
  const before = all.filter((item) => item.bottom <= hours.start);
  const after = all.filter((item) => item.top >= hours.end);

  const routine = narrowRoutine ? inside.filter((item) => item.event.routine) : [];
  const appointments = narrowRoutine ? inside.filter((item) => !item.event.routine) : inside;

  // Routine bars are packed among themselves, by time, into fixed-width
  // columns down the left of the lane. Two overlapping routines sit side by
  // side; one that starts after another ends takes the first column again.
  const bars = packColumns(routine);
  const barsHtml = bars.map((item) => {
    const owners = ownersOf(item.event, members);
    const { top, height } = pctSpan(item.top, item.bottom, hours);
    return `
      <div class="of-bar ${blockPattern(owners)} ${clipClasses(item.top, item.bottom, hours)}"
           style="${blockPaint(owners)};top:${top.toFixed(2)}%;height:${height.toFixed(2)}%;
                  left:${item.column * BAR_PX}px;width:${BAR_PX - 1}px">
        ${fillsHtml(owners)}
        <span class="of-bar-name">${esc(item.event.title || item.event.summary || "")}</span>
        ${owners.length ? `<span class="of-bar-marks">${initialsHtml(owners)}</span>` : ""}
      </div>`;
  }).join("");

  // How far an appointment has to move right to clear the routine bars that
  // overlap it in time: past the outermost of them. Outside routine hours
  // that is nothing at all, which is the point of not having a gutter.
  const indentFor = (item) => {
    const overlapping = bars.filter((bar) => bar.top < item.bottom && bar.bottom > item.top);
    return overlapping.length ? (Math.max(...overlapping.map((bar) => bar.column)) + 1) * BAR_PX : 0;
  };

  // Packed in hours; pctSpan runs per block below. Handing pctSpan's output
  // to the packer is what broke this before — see packColumns.
  const placed = packColumns(appointments);

  const blocks = placed.map((item) => {
    const event = item.event;
    const owners = ownersOf(event, members);
    const columns = item.columns;
    const share = item.column / columns;
    const indent = indentFor(item);
    const { top, height } = pctSpan(item.top, item.bottom, hours);
    // The block's slice of what is left beside the routine bars: the lane
    // minus the indent, split by the cluster's column count. Mixed units, so
    // it stays a calc() — the indent is pixels, the split is a percentage.
    const left = `calc(${indent}px + ${(share * 100).toFixed(3)}% - ${(share * indent).toFixed(2)}px + 1px)`;
    const width = `calc(${(100 / columns).toFixed(3)}% - ${(indent / columns).toFixed(2)}px - 2px)`;
    // Everything is emitted; the CSS drops the meta line, then the title, as
    // the block gets shorter. Wrapping is a CSS decision too, by height: a
    // narrow column in a three-way cluster gets a word broken over its lines
    // rather than one letter and an ellipsis.
    return `
      <div class="of-ev ${blockPattern(owners)} can-wrap ${event.routine ? "is-routine" : ""} ${owners.length ? "has-marks" : ""} ${clipClasses(item.top, item.bottom, hours)}"
           style="${blockPaint(owners)};top:${top.toFixed(2)}%;height:${height.toFixed(2)}%;
                  left:${left};width:${width}">
        ${fillsHtml(owners)}
        ${stripeHtml()}
        <span class="of-name">${esc(event.title || event.summary || "")}</span>
        <span class="of-meta"><span class="of-time">${esc(fmtHm(event.start))}</span></span>
        ${data.show_location && event.location
          ? `<span class="of-loc"><i class="ph-bold ph-map-pin"></i>${esc(event.location)}</span>`
          : ""}
        ${marksHtml(owners)}
      </div>`;
  }).join("");

  const nowHour = data.now ? hourOf(data.now) : null;
  const showNow = day.is_today && nowHour != null && nowHour >= hours.start && nowHour <= hours.end;
  const nowPct = showNow ? ((nowHour - hours.start) / Math.max(1, hours.end - hours.start)) * 100 : 0;

  const classes = ["of-lane"];
  if (day.is_weekend) classes.push("is-weekend");
  if (day.is_today) classes.push("is-today");

  return `
    <div class="${classes.join(" ")}">
      <div class="of-main">
        ${barsHtml}
        ${blocks}
        ${showNow ? `<div class="tt-now" style="top:${nowPct.toFixed(2)}%"></div>` : ""}
        ${edgeHtml(before, members, "up")}
        ${edgeHtml(after, members, "down")}
      </div>
    </div>`;
}

// A counted chevron rather than a rescaled grid: the panel keeps its shape,
// and "▲1" is enough to make you go and look.
function edgeHtml(items, members, direction) {
  if (!items.length) return "";
  const owners = ownersOf(items[0].event, members);
  // A border-drawn triangle, not a ▲/▼ glyph: the two glyphs are different
  // sizes in most faces, and a border triangle is the same shape either way up.
  return `
    <div class="of-edge is-${direction}">
      <span class="of-edge-chip">
        <span class="of-edge-dot" style="${paint(owners[0])}"></span><span class="of-edge-tri"></span>${items.length}
      </span>
    </div>`;
}

// One label per hour, each placed at the exact height of its rule rather than
// spread by flexbox: space-between plus the base stylesheet's edge tweaks put
// the first and last label half a line off their lines.
function hourLabels(hours) {
  const span = Math.max(1, hours.end - hours.start);
  const out = [];
  for (let h = hours.start; h <= hours.end; h++) {
    const pct = ((h - hours.start) / span) * 100;
    out.push(`<span style="top:${pct.toFixed(3)}%">${String(h).padStart(2, "0")}</span>`);
  }
  return out.join("");
}

function rangeLabel(days) {
  const first = days[0];
  const last = days[days.length - 1];
  if (!first || !last) return "";
  const a = new Date(`${first.date}T00:00:00`);
  const b = new Date(`${last.date}T00:00:00`);
  const head = `${MONTH[a.getMonth()] || ""} ${a.getDate()}`;
  const tail = a.getMonth() === b.getMonth()
    ? `${b.getDate()}`
    : `${MONTH[b.getMonth()] || ""} ${b.getDate()}`;
  return `${head} → ${tail} · ${b.getFullYear()}`;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

/* ---------- styles ---------- */

function styles(span) {
  return `
    /* Plain white, not the theme's --surface. Every theme surface is an
       off-white tint, and on a Spectra panel a tint is a field of dithered
       dots behind the whole timetable. The edge chips paint the same white
       so they still mask the rule they sit on. */
    .w[data-widget="outlook_family"] {
      background: #fff; --bg: #fff;
      /* And every text role collapses to solid black: a grey label is a
         dithered label. Hierarchy comes from size and weight alone. */
      color: #000;
      --text-primary: #000; --text-secondary: #000; --text-muted: #000;
      /* Spectra 6 has six inks, and the quantiser leaves a pixel alone only
         when it already sits on one of them. Members are painted from the
         SLOTS table above (pure inks and 2x2 tiles of them), never from a
         theme accent. Slot 1, the now-line and today chip, is black. */
      --accent-1: #000000; --accent-1-soft: #ffffff;
      --on-accent: #ffffff;
      --surface-sunken: #ffffff;
    }

    /* Our own named container for the cell-width rules. The blocks below are
       size containers too, and an *unnamed* @container query would resolve
       against the nearest one of those — a 130px block — instead of the cell,
       silently applying the narrow-cell rules at every size. Naming both ends
       keeps the two scales apart. */
    .cal-body {
      gap: var(--space-2);
      container-type: inline-size;
      container-name: ofcell;
    }
    .of-grid {
      display: grid;
      flex: 1 1 auto;
      min-height: 0;
    }

    /* Legend, in the title bar between the name and the date range, so it
       costs the grid no height. Patterns are the redundancy that keeps
       members apart on a black-and-white panel. */
    .of-legend {
      display: flex; flex-wrap: nowrap; align-items: center;
      gap: var(--space-3);
      margin-left: var(--space-4);
      flex: 1 1 auto; min-width: 0; overflow: hidden;
    }
    /* The legend is what gives way, never the widget name or the range. */
    .w-title h3 { flex: 0 0 auto; }
    .w-title .w-title-meta { flex: 0 0 auto; white-space: nowrap; }
    .of-key { display: inline-flex; align-items: center; gap: 0.4em; }
    .of-key-chip {
      width: 1.35em; height: 1.35em;
      display: inline-grid; place-items: center;
      font-size: var(--fs-caption); font-weight: var(--fw-black);
      background-color: #fff;
      background-image: var(--of-fill); background-size: var(--of-fill-size, 2px 2px);
      border-radius: var(--radius-0, 2px);
      box-shadow: inset 0 0 0 1px #000;
    }
    .of-key-name {
      font-size: var(--fs-caption); font-weight: var(--fw-bold);
      color: var(--text-secondary);
    }

    /* Column heads. accent-1 is the design system's "now"/today role. */
    .of-head {
      display: flex; flex-direction: column; align-items: center;
      gap: 0.1em; padding-bottom: var(--space-1); min-width: 0;
    }
    .of-dow {
      font-size: var(--fs-caption); font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-muted);
    }
    .of-num { font-size: var(--fs-body); font-weight: var(--fw-bold); line-height: 1.1; }
    .of-head.is-today .of-dow { color: var(--accent-1); }
    .of-head.is-today .of-num {
      background: var(--accent-1); color: var(--on-accent);
      width: 1.6em; height: 1.6em;
      display: inline-grid; place-items: center;
      border-radius: 999px; font-weight: var(--fw-black);
    }

    /* Solid dark rules between the days. A faint --surface-sunken line
       dithers to nothing on the panel, and without it seven columns of
       tiles read as one field. */
    .of-lane {
      position: relative; display: flex; min-width: 0;
      border-left: 1px solid #000;
    }
    .of-lane:last-child { border-right: 1px solid #000; }
    /* The weekend ground is a 4x4 tile (red at 12.5%), not a wash: a wash
       is dither, a tile is painted verbatim. */
    .of-lane.is-weekend {
      background-image: ${WEEKEND.url}; background-size: ${WEEKEND.size};
    }
    .of-main { position: relative; flex: 1 1 auto; min-width: 0; }
    /* One rule per hour so the eye can sweep a time across all the
       columns. Percentages resolve against the lane's height. There is no
       faint ink on this panel, so "faint" is a 1px black line cut into
       dashes: the rules are drawn on a pseudo-element and masked to
       2px-on / 3px-off, so the ground underneath (white, or the weekend
       tile) shows through the gaps. Tiles are positioned later in the DOM
       and paint over it. */
    .of-main::before {
      content: ""; position: absolute; inset: 0; pointer-events: none;
      background-image: repeating-linear-gradient(
        to bottom,
        transparent 0,
        transparent calc((100% / ${span}) - 1px),
        #000 calc((100% / ${span}) - 1px),
        #000 calc(100% / ${span})
      );
      -webkit-mask-image: repeating-linear-gradient(to right, #000 0 2px, transparent 2px 5px);
      mask-image: repeating-linear-gradient(to right, #000 0 2px, transparent 2px 5px);
    }

    /* Hour labels sit on their rules: absolute at the rule's height, centred
       on it. The base sheet lays them out with flexbox and nudges the first
       and last, which is what left them half a line off. */
    .tt-hours { display: block; position: relative; }
    .tt-hours span,
    .tt-hours span:first-child,
    .tt-hours span:last-child {
      position: absolute; right: var(--space-3);
      line-height: 1; transform: translateY(-50%);
    }

    /* Appointments. The stripe is a child, so it can be split per owner.
       Each block is its own size container, which is what lets the rules
       further down drop text by the block's real pixel height. */
    .of-ev {
      position: absolute; overflow: hidden; isolation: isolate;
      background-color: #fff;
      background-image: var(--of-fill); background-size: var(--of-fill-size, 2px 2px);
      color: var(--of-text, #000);
      /* White tiles need an edge to read as tiles. An outline, not a
         border, so the size container's height is unchanged. */
      outline: 1px solid #000; outline-offset: -1px;
      padding: 1px var(--space-1) 1px calc(var(--space-1) + 5px);
      line-height: 1.1;
      display: flex; flex-direction: column; gap: 0;
      container-type: size;
      container-name: ofev;
    }
    /* No opacity on routine blocks: 75% black text is grey, and grey dithers. */
    .of-stripe { position: absolute; left: 0; top: 0; bottom: 0; width: 5px; background: #000; }
    /* Shared events: one fill segment per owner, under the text (z-index -1
       inside the block's own stacking context), each with its own tile and
       pattern, in stripe order. */
    .of-fillseg {
      position: absolute; top: 0; bottom: 0; z-index: -1;
      background-color: #fff;
      background-image: var(--of-fill); background-size: var(--of-fill-size, 2px 2px);
    }
    .of-name {
      font-size: var(--fs-caption); font-weight: var(--fw-black);
      color: inherit;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }

    .of-meta {
      display: flex; align-items: center; gap: 0.2em;
      min-width: 0; flex: 0 0 auto;
      /* Keep the clock clear of the corner marks below. */
      padding-right: 2.7em;
    }
    .of-time {
      font-size: calc(var(--fs-caption) * 0.85); font-weight: var(--fw-bold);
      color: inherit; font-feature-settings: "tnum";
    }
    .of-loc {
      display: inline-flex; align-items: center; gap: 0.2em;
      font-size: calc(var(--fs-caption) * 0.8); font-weight: var(--fw-bold);
      color: inherit;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    /* "There is more of this than you can see." A block that starts before the
       window or ends after it is drawn clamped to the edge, which otherwise
       looks identical to one that genuinely begins at 07:00. The triangle sits
       on the cut edge and points the way the event continues. Drawn with
       borders rather than a glyph so it stays a crisp solid shape on e-ink at
       any size, and centred so it never lands on the title or the corner
       marks. Black, not the owner's line ink: that ink is the fill's own
       colour, so a yellow triangle on Lea's yellow tile simply vanished. */
    .of-ev.clip-top::before,
    .of-ev.clip-bottom::after,
    .of-bar.clip-top::before,
    .of-bar.clip-bottom::after {
      content: "";
      position: absolute;
      left: calc(50% - 5px);
      width: 10px; height: 6px;
      background-repeat: no-repeat;
      z-index: 2;
    }
    /* No padding to clear the triangle: reserving a band for it costs the
       block a line of title, and the title is worth more. The triangle is
       horizontally centred while the time is left-aligned and the owner marks
       are right-aligned, so in practice it lands in the gap between them. */
    .of-ev.clip-top::before,
    .of-bar.clip-top::before { top: 1px; background-image: ${tri("up", 10, 6)}; }
    .of-ev.clip-bottom::after,
    .of-bar.clip-bottom::after { bottom: 1px; background-image: ${tri("down", 10, 6)}; }
    /* A bar is only one mark wide, so its triangle is smaller and hugs the
       left instead of trying to centre in a space narrower than itself. */
    .of-bar.clip-top::before,
    .of-bar.clip-bottom::after {
      left: 2px; width: 6px; height: 6px;
    }
    .of-bar.clip-top::before { background-image: ${tri("up", 6, 6)}; }
    .of-bar.clip-bottom::after { background-image: ${tri("down", 6, 6)}; }

    /* Owner marks, bottom-right. Absolute, so they take nothing from the
       title's width and nothing from the layout of the lines above them. */
    .of-marks {
      position: absolute;
      right: 2px; bottom: 1px;
      display: flex; gap: 2px;
      pointer-events: none;
    }

    /* Twice the size it started at, so the letter reads from across the
       room; the container rules below hide it on tiles too short to share. */
    .of-mark {
      display: inline-grid; place-items: center;
      width: 1.3em; height: 1.3em; flex: 0 0 auto;
      border-radius: var(--radius-0, 2px);
      font-size: calc(var(--fs-caption) * 1.1); font-weight: var(--fw-black);
      color: #000;
      background-color: #fff;
      background-image: var(--of-fill); background-size: var(--of-fill-size, 2px 2px);
      box-shadow: inset 0 0 0 1px #000;
    }

    /* Routine bars: narrow, down the left of the lane, only over their own
       hours. Vertical labels, so a long word like "Fussballtraining" costs
       height (which the bar has) instead of width (which it doesn't). */
    .of-bar {
      position: absolute; overflow: hidden; isolation: isolate;
      background-color: #fff;
      background-image: var(--of-fill); background-size: var(--of-fill-size, 2px 2px);
      color: var(--of-text, #000);
      outline: 1px solid #000; outline-offset: -1px;
      /* No stripe: the bar is one mark wide, and the marks at its foot take
         the whole width, so an edge would only steal from them. */
      display: flex; flex-direction: column; align-items: center;
      padding-top: 2px;
      container-type: size;
      container-name: ofbar;
    }
    .of-bar-name {
      flex: 0 1 auto; min-height: 0;
      writing-mode: vertical-rl; text-orientation: mixed;
      font-size: var(--fs-caption); font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      color: inherit;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    /* Owner marks at the foot of the bar, stacked, each as wide as the bar. */
    .of-bar-marks {
      flex: 0 0 auto; margin-top: auto;
      display: flex; flex-direction: column;
      width: 100%;
    }
    .of-bar-marks .of-mark { width: 100%; border-radius: 0; }

    /* All-day / multi-day bars across the top. */
    .of-bands { display: grid; gap: 2px; padding-bottom: var(--space-1); }
    .of-band {
      position: relative; overflow: hidden; isolation: isolate;
      display: flex; align-items: center; gap: 0.25em;
      background-color: #fff;
      background-image: var(--of-fill); background-size: var(--of-fill-size, 2px 2px);
      color: var(--of-text, #000);
      outline: 1px solid #000; outline-offset: -1px;
      padding: 1px var(--space-2) 1px calc(var(--space-2) + 5px);
      min-width: 0;
    }
    .of-band-name {
      font-size: var(--fs-caption); font-weight: var(--fw-black);
      color: inherit;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }

    /* Out-of-window markers. */
    .of-edge {
      position: absolute; left: 0; right: 0;
      display: flex; justify-content: center;
      z-index: 3; pointer-events: none;
    }
    .of-edge.is-up { top: 1px; }
    .of-edge.is-down { bottom: 1px; }
    .of-edge-chip {
      display: inline-flex; align-items: center; gap: 0.2em;
      background: var(--bg);
      padding: 0 0.3em;
      font-size: calc(var(--fs-caption) * 0.8); font-weight: var(--fw-black);
      color: var(--text-muted);
      font-feature-settings: "tnum";
    }
    /* The same pixel-exact polygon either way up (see tri()). */
    .of-edge-tri { width: 12px; height: 11px; background-repeat: no-repeat; }
    .of-edge.is-up .of-edge-tri { background-image: ${tri("up", 12, 11)}; }
    .of-edge.is-down .of-edge-tri { background-image: ${tri("down", 12, 11)}; }
    .of-edge-dot {
      width: 0.5em; height: 0.5em; border-radius: var(--radius-0, 2px);
      background-color: #fff;
      background-image: var(--of-fill); background-size: var(--of-fill-size, 2px 2px);
      box-shadow: inset 0 0 0 1px #000;
    }

    /* Fill patterns. Redundant with colour on a Spectra panel, and the only
       thing telling two people apart on a black-and-white one. */
    .of-p-solid { }
    .of-p-diag {
      background-image: repeating-linear-gradient(45deg,
        var(--of-stroke) 0 2px, transparent 2px 11px), var(--of-fill);
      background-size: auto, var(--of-fill-size, 2px 2px);
    }
    .of-p-dots {
      background-image: radial-gradient(var(--of-stroke) 1.4px, transparent 1.5px), var(--of-fill);
      background-size: 9px 9px, var(--of-fill-size, 2px 2px);
    }
    .of-p-horiz {
      background-image: repeating-linear-gradient(0deg,
        var(--of-stroke) 0 2px, transparent 2px 10px), var(--of-fill);
      background-size: auto, var(--of-fill-size, 2px 2px);
    }
    .of-p-cross {
      background-image:
        repeating-linear-gradient(45deg, var(--of-stroke) 0 2px, transparent 2px 12px),
        repeating-linear-gradient(-45deg, var(--of-stroke) 0 2px, transparent 2px 12px),
        var(--of-fill);
      background-size: auto, auto, var(--of-fill-size, 2px 2px);
    }

    /* How much a block says, by how tall it actually is. What goes first as it
       shrinks is what we are most willing to lose: the time (its position on
       the axis already says roughly when), then the owner marks, then the
       title — a row of clipped letter-tops reads worse than a clean bar. */
    @container ofev (max-height: 34px) {
      .of-meta { display: none; }
    }
    /* Down to here the title sits on the tile's top line and the marks sit
       under it, so they never meet and the title keeps its full width. Below
       it there is only room for one line, and the marks would land on the
       title's last characters — so that is where they get their own room, and
       only on tiles that actually have a mark to place. */
    @container ofev (max-height: 36px) {
      .of-ev.has-marks .of-name { padding-right: 2.4em; }
    }
    @container ofev (max-height: 24px) {
      .of-marks { display: none; }
      .of-ev.has-marks .of-name { padding-right: 0; }
    }
    @container ofev (max-height: 17px) {
      .of-name { display: none; }
    }
    /* As many title lines as the tile has room for. A title line is 0.79em
       of the tile's font (caption 0.72 x line-height 1.1) and the time line
       under it ~0.8em with its padding, so N lines need about N x 0.79 +
       0.8em; the thresholds are in em so they follow the cell's fluid type
       size instead of being right at one panel size only. Clamping rather
       than letting the box overflow: a row of clipped letter-tops reads
       worse than an ellipsis. Wrapping any earlier than two lines fit
       clamps the second line to a lone ellipsis and pushes the time out. */
    @container ofev (min-height: 2.4em) {
      .of-ev.can-wrap .of-name {
        white-space: normal; word-break: break-word; hyphens: auto;
        display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2;
        -webkit-box-orient: vertical;
      }
    }
    @container ofev (min-height: 3.2em) {
      .of-ev.can-wrap .of-name { -webkit-line-clamp: 3; line-clamp: 3; }
    }
    @container ofev (min-height: 4em) {
      .of-ev.can-wrap .of-name { -webkit-line-clamp: 4; line-clamp: 4; }
    }
    @container ofev (min-height: 4.8em) {
      .of-ev.can-wrap .of-name { -webkit-line-clamp: 5; line-clamp: 5; }
    }
    @container ofev (min-height: 5.6em) {
      .of-ev.can-wrap .of-name { -webkit-line-clamp: 6; line-clamp: 6; }
    }
    /* A tile narrower than about 3.5em (a three-way cluster in a
       seven-day week) cannot wrap into anything readable: one or two letters
       a line. It runs the title down the tile instead, the way the routine
       bars do (D16), stopping above the marks; the time goes, its position
       on the axis says roughly when. Placed after the wrap rules so it wins. */
    @container ofev (max-width: 3.5em) {
      .of-ev { display: block; }
      .of-meta, .of-loc { display: none; }
      .of-ev .of-name, .of-ev.can-wrap .of-name, .of-ev.has-marks .of-name {
        display: block; padding-right: 0;
        /* vertical-lr, not -rl: the block's first (only) line then sits at
           the tile's left edge, beside the stripe, instead of at the right. */
        writing-mode: vertical-lr; text-orientation: mixed;
        font-size: calc(var(--fs-caption) * 0.8);
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        height: calc(100% - 1.5em); width: auto;
        -webkit-line-clamp: unset; line-clamp: unset;
      }
    }
    /* A routine bar too short for its vertical label keeps the bar; the
       label would only be a smear of glyph tops. */
    @container ofbar (max-height: 42px) {
      .of-bar-name { display: none; }
    }
    /* Shorter than one mark: the bar is a coloured tick, nothing more. */
    @container ofbar (max-height: 1.5em) {
      .of-bar-marks { display: none; }
    }

    /* md is where this gets tight, and it is tight in the one direction that
       matters: the font base doesn't shrink with the cell, so at 640x400 the
       chrome would leave under 100px for fourteen hours of timetable. Every
       rule here buys the lane back some height, in the order we're willing to
       lose things: locations, then the legend, then the day-header lockup. */
    @container ofcell (max-width: 700px) {
      .of-loc { display: none; }
      .of-legend { display: none; }
      .of-head {
        flex-direction: row; align-items: baseline;
        justify-content: center; gap: 0.35em;
      }
      .of-head.is-today .of-num {
        width: auto; height: auto; background: none;
        color: var(--accent-1); border-radius: 0;
      }
      .of-num { font-size: var(--fs-caption); }
      .tt-hours { font-size: calc(var(--fs-caption) * 0.85); }
    }
    @container ofcell (max-width: 480px) {
      .of-meta { display: none; }
    }
  `;
}
