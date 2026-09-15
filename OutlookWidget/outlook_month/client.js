/**
 * outlook_month, the family wall calendar.
 *
 * A column per weekday, a row per week, starting with the current week. Every
 * appointment is a chip carrying its owner's fill, pattern and initial; trips
 * and all-day events are bands across the days they cover.
 *
 * Three rules about spending pixels:
 *
 *   1. No times. The week widget answers "when today"; this one answers
 *      "which day", and the title is what earns the width the clock would
 *      have taken.
 *   2. A day with room lets a long title wrap; a busy day keeps one line per
 *      chip, and what does not fit becomes a "+n" rather than a row of
 *      clipped letter-tops.
 *   3. Days already over are drawn like any other — at most six of them are
 *      on the panel, and a chip without its colour reads as a mistake.
 *
 * Ownership is resolved server-side by outlook_core's family rules, so every
 * event arrives with `members` and an already-stripped `title`.
 */

const DOW = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MONTH = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

// A day with this many chips or fewer lets each title wrap to two lines.
const WRAP_UP_TO = 4;

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const size = ctx?.cell?.size ?? "lg";
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w size-${size}" data-widget="outlook_month">
        <div class="w-title"><h3>Familie</h3></div>
        <div class="w-body list-body">
          <div class="u-muted"><i class="ph-bold ph-warning-circle"></i> ${esc(data.error)}</div>
        </div>
      </div>`;
    return;
  }

  const members = new Map((data.members || []).map((m) => [m.id, m]));
  const days = Array.isArray(data.days) ? data.days : [];

  if (!days.length) {
    shadow.innerHTML = `
      ${css}
      <div class="w size-${size}" data-widget="outlook_month">
        <div class="w-title"><h3>Familie</h3></div>
        <div class="w-body list-body"><div class="u-muted">Nothing scheduled.</div></div>
      </div>`;
    return;
  }

  const weeks = [];
  for (let i = 0; i < days.length; i += 7) weeks.push(days.slice(i, i + 7));
  const heads = weeks[0]
    .map((day) => `<div class="om-dow">${esc(DOW[dayOfWeek(day.date)] || "")}</div>`)
    .join("");
  const rows = weeks
    .map((week, w) => weekHtml(week, w * 7, data.bands || [], members))
    .join("");

  // The zoom-locked .w-title rather than the .cal-head hero: that header is
  // sized with --fs-jumbo, which is right when the date is the content and
  // wrong here, where it would eat a row of the grid.
  shadow.innerHTML = `
    ${css}
    <style>${styles()}</style>
    <div class="w size-${size}" data-widget="outlook_month">
      <div class="w-title">
        <i class="ph-bold ph-calendar-dots" style="color:#000"></i>
        <h3>Familie</h3>
        ${legendHtml(data.members || [])}
        <span class="w-title-meta">${esc(rangeLabel(days))}${data.stale ? " · cached" : ""}</span>
      </div>
      <div class="w-body cal-body">
        <div class="om-grid">
          <div class="om-dows">${heads}</div>
          ${rows}
        </div>
      </div>
    </div>`;

  watchOverflow(shadow);
}

/* ---------- members ---------- */

// The panel's six inks. Everything the widget paints is one of these, pure,
// or a 2x2 tile of them: the quantiser leaves a native pixel alone and
// dithers anything else. Same table as outlook_family (its DESIGN.md, D36/D37).
const INK = { K: "#000000", W: "#ffffff", Y: "#ffff00", R: "#ff0000", B: "#0000ff", G: "#00ff00" };

// An n x n pixel tile as an SVG background, from a string of n*n ink letters
// in reading order. crispEdges plus a background-size equal to the tile keeps
// every pixel on its ink; the panel then paints the tile verbatim.
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

// Member slots, shared with outlook_family so a person looks the same on
// both panels. `line` is the solid ink for the stripe, marks and pattern
// stroke. Slot 1 is the today chip and is never a person.
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
  return `--om-line:${line};--om-fill:${fill};--om-fill-size:${fillSize};--om-stroke:${line};--om-text:${text}`;
}

function ownersOf(event, members) {
  return (event.members || []).map((id) => members.get(id)).filter(Boolean);
}

function patternClass(owners) {
  const pattern = owners.length ? owners[0].pattern : "solid";
  return `om-p-${["solid", "diag", "dots", "horiz", "cross"].includes(pattern) ? pattern : "solid"}`;
}

// The stripe down the chip's edge is always black. It used to carry the
// owner's line ink, split per owner on a shared chip, but two slots can share
// a line ink (see marksHtml) and the fill and marks already say whose the
// chip is; a black edge is what survives on a black-and-white panel.
function stripeHtml() {
  return `<span class="om-stripe"></span>`;
}

// A shared event shows every owner's fill under the text: one segment per
// owner, each with its own tile and pattern, and the chip's own fill switched
// off (outlook_family D38). Stacked top to bottom rather than side by side,
// unlike the timetable: a chip is one line tall and wide, so a vertical
// seam cut the title in two colours mid-word, while a horizontal one runs
// along it.
function fillsHtml(owners) {
  if (owners.length < 2) return "";
  const step = 100 / owners.length;
  return owners
    .map((m, i) => `<span class="om-fillseg ${patternClass([m])}" style="${paint(m)};top:${(i * step).toFixed(2)}%;height:${step.toFixed(2)}%"></span>`)
    .join("");
}

function chipPaint(owners) {
  const style = paint(owners[0]);
  return owners.length > 1 ? `${style};--om-fill:none` : style;
}

function chipPattern(owners) {
  return owners.length > 1 ? "om-p-solid" : patternClass(owners);
}

// Every owner's initial, on every chip. Colour alone asks you to remember
// five hues and go back to the legend; the letter is what reads from the
// other side of the room. On an md cell it is the whole chip.
//
// The mark wears the member's own tile, the same one the legend chip and the
// event chip wear — not the slot's solid "line" ink. Two slots can share a
// line ink (red and orange are both R, green and lime both G), and a solid
// mark cannot tell them apart; the tile can, and the panel paints it
// verbatim. Black letter on every tile (D37), with a 1px black edge so it is
// a mark on a chip of the same fill too.
function marksHtml(owners) {
  const marks = owners.length
    ? owners.map((m) => `<span class="om-mark" style="${paint(m)}">${esc(m.letter || "·")}</span>`).join("")
    : `<span class="om-mark is-none"></span>`;
  return `<span class="om-marks">${marks}</span>`;
}

function legendHtml(roster) {
  if (!roster.length) return "";
  const chips = roster
    .map((m) => `
      <span class="om-key">
        <span class="om-key-chip ${patternClass([m])}" style="${paint(m)};color:${accent(m).text}">${esc(m.letter || "·")}</span>
        <span class="om-key-name">${esc(m.name || "")}</span>
      </span>`)
    .join("");
  return `<div class="om-legend">${chips}</div>`;
}

/* ---------- grid ---------- */

function dayOfWeek(isoDate) {
  return new Date(`${isoDate}T00:00:00`).getDay();
}

function chipHtml(event, members, canWrap) {
  const owners = ownersOf(event, members);
  const classes = ["om-chip", chipPattern(owners)];
  if (!owners.length) classes.push("is-unowned");
  if (owners.length > 1) classes.push("is-shared");
  if (canWrap) classes.push("can-wrap");
  return `
    <div class="${classes.join(" ")}" style="${chipPaint(owners)}">
      ${fillsHtml(owners)}
      ${stripeHtml()}
      <span class="om-name">${esc(event.title || event.summary || "")}</span>
      ${marksHtml(owners)}
    </div>`;
}

// One week: seven day cells, and over them the bands that touch the week,
// packed into lanes. Each cell reserves room under its number for the lanes
// that actually cross it — down to the deepest one that does — so a weekend
// trip costs Monday nothing and its chips start at the top.
function weekHtml(week, offset, bands, members) {
  const laneEnds = [];
  const placed = [];
  for (const band of bands.slice().sort((a, b) => a.first - b.first)) {
    const a = Math.max(band.first, offset) - offset;
    const b = Math.min(band.last, offset + week.length - 1) - offset;
    if (b < a) continue;
    let lane = laneEnds.findIndex((end) => end < a);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(b);
    } else {
      laneEnds[lane] = b;
    }
    placed.push({ band, a, b, lane });
  }
  const lanes = laneEnds.length;

  const cells = week.map((day, i) => {
    const depth = placed.reduce((d, p) => (p.a <= i && i <= p.b ? Math.max(d, p.lane + 1) : d), 0);
    const classes = ["om-day"];
    if (day.is_today) classes.push("is-today");
    if (day.is_weekend) classes.push("is-weekend");
    if (day.is_past) classes.push("is-past");
    const events = Array.isArray(day.events) ? day.events : [];
    const canWrap = events.length <= WRAP_UP_TO;
    const chips = events.map((ev) => chipHtml(ev, members, canWrap)).join("");
    const listClass = canWrap ? "om-list can-wrap" : "om-list";
    // The first of a month carries its name; nothing else says which month
    // a row is in.
    const num = day.day === 1
      ? `<span class="om-mon">${esc(MONTH[new Date(`${day.date}T00:00:00`).getMonth()] || "")}</span>1`
      : esc(String(day.day ?? ""));
    return `
      <div class="${classes.join(" ")}">
        <div class="om-num"><span class="om-num-text">${num}</span></div>
        <div class="om-bandroom" style="height:calc(${depth} * (var(--om-band) + var(--om-gap)))"></div>
        <div class="${listClass}">${chips}</div>
      </div>`;
  }).join("");

  const bandLayer = placed.length
    ? `<div class="om-bands" style="grid-template-rows:repeat(${lanes},var(--om-band))">${
        placed.map(({ band, a, b, lane }) => {
          const owners = ownersOf(band, members);
          return `
            <div class="om-chip om-band ${chipPattern(owners)}${owners.length ? "" : " is-unowned"}"
                 style="${chipPaint(owners)};grid-column:${a + 1} / span ${b - a + 1};grid-row:${lane + 1}">
              ${fillsHtml(owners)}
              ${stripeHtml()}
              <span class="om-name">${esc(band.title || band.summary || "")}</span>
              ${marksHtml(owners)}
            </div>`;
        }).join("")
      }</div>`
    : "";

  return `<div class="om-week">${cells}${bandLayer}</div>`;
}

// What does not fit in a cell becomes a count. The lists are laid out by the
// browser, so this reads the real boxes: every chip whose bottom edge falls
// past its list is cut, and one "+n" takes the first cut slot (which frees
// the line it needs). It is re-run whenever the grid changes size — the host
// sizes the cell, applies the shared stylesheet and loads fonts *after*
// render() returns, and screenshots only once those have settled, so a
// single measurement at render time would see a layout nobody ever sees.
// Each run starts from the untrimmed state, so it can run any number of times.
function trimOverflow(shadow) {
  for (const list of shadow.querySelectorAll(".om-list")) {
    list.querySelector(":scope > .om-more")?.remove();
    const chips = [...list.querySelectorAll(":scope > .om-chip")];
    if (!chips.length) continue;
    const wrap = list.classList.contains("can-wrap");
    for (const chip of chips) {
      chip.classList.remove("is-cut");
      chip.classList.toggle("can-wrap", wrap);
    }
    const limit = () => list.getBoundingClientRect().bottom + 0.5;
    const fits = (el) => el.getBoundingClientRect().bottom <= limit();
    if (fits(chips[chips.length - 1])) continue;

    // Wrapped titles are the first thing to give up, one line each is worth
    // more than two lines for some and a "+n" for the rest.
    if (wrap) {
      for (const chip of chips) chip.classList.remove("can-wrap");
      if (fits(chips[chips.length - 1])) continue;
    }
    const more = document.createElement("div");
    more.className = "om-more";
    list.appendChild(more);
    // Cut what overflowed, then one more at a time until the counter fits
    // too: in a column it needs a line of its own, in the md marks row it
    // usually fits beside what is left.
    const overflowed = chips.filter((c) => !fits(c)).length;
    for (let cut = Math.max(1, overflowed); cut <= chips.length; cut++) {
      for (const [i, chip] of chips.entries()) chip.classList.toggle("is-cut", i >= chips.length - cut);
      more.textContent = `+${cut}`;
      if (fits(more)) break;
    }
  }
}

function watchOverflow(shadow) {
  trimOverflow(shadow);
  const grid = shadow.querySelector(".om-grid");
  if (grid && typeof ResizeObserver === "function") {
    // Observing the grid, not the lists: trimming never changes the grid's
    // size, so this cannot feed itself.
    new ResizeObserver(() => trimOverflow(shadow)).observe(grid);
  }
  document.fonts?.ready?.then(() => trimOverflow(shadow));
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

function styles() {
  return `
    /* Plain white, not the theme's --surface, and every text role solid
       black: a tint or a grey is a dither field on the panel. Hierarchy is
       size and weight only. The today chip is black, never a theme accent. */
    .w[data-widget="outlook_month"] {
      background: #fff; --bg: #fff;
      color: #000;
      --text-primary: #000; --text-secondary: #000; --text-muted: #000;
      --accent-1: #000000; --accent-1-soft: #ffffff;
      --on-accent: #ffffff;
      --surface-sunken: #ffffff;
      /* The three heights the grid is built from, in the cell's fluid type
         size: the day number's line, a band, and the gap between rows. A
         band is exactly one chip tall: 1.3 lines of the label size (0.8em),
         written out here because an em inside a custom property resolves
         where it is used, and the band lanes are sized from the cell. */
      --om-num: 1.15em;
      --om-band: 1.04em;
      --om-gap: 0.15em;
    }

    .cal-body {
      gap: var(--space-2);
      container-type: inline-size;
      container-name: omcell;
    }

    /* Legend, in the title bar between the name and the date range, so it
       costs the grid no height. */
    .om-legend {
      display: flex; flex-wrap: nowrap; align-items: center;
      gap: var(--space-3);
      margin-left: var(--space-4);
      flex: 1 1 auto; min-width: 0; overflow: hidden;
    }
    .w-title h3 { flex: 0 0 auto; }
    .w-title .w-title-meta { flex: 0 0 auto; white-space: nowrap; }
    .om-key { display: inline-flex; align-items: center; gap: 0.4em; }
    .om-key-chip {
      width: 1.35em; height: 1.35em;
      display: inline-grid; place-items: center;
      font-size: var(--fs-caption); font-weight: var(--fw-black);
      background-color: #fff;
      background-image: var(--om-fill); background-size: var(--om-fill-size, 2px 2px);
      border-radius: var(--radius-0, 2px);
      box-shadow: inset 0 0 0 1px #000;
    }
    .om-key-name { font-size: var(--fs-caption); font-weight: var(--fw-bold); }

    /* The grid: a header row, then one flex row per week sharing the height
       equally. Solid black rules throughout — a faint one dithers away, and
       twenty-eight cells of chips without one read as a single field. */
    .om-grid {
      flex: 1 1 auto; min-height: 0;
      display: flex; flex-direction: column;
      border: 1px solid #000;
    }
    /* Header and week rows are grids of seven equal tracks, and so is the
       band layer over each week, so a band's edges land exactly on the
       cell's. (Flex cells with borders come out a pixel apart.) Every cell
       has a left border — the first one transparent — so their content
       boxes are identical and chips line up column to column. */
    .om-dows { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr));
               flex: 0 0 auto; border-bottom: 1px solid #000; }
    .om-dow {
      min-width: 0;
      font-size: var(--fs-caption); font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      text-align: center; padding: 2px 0 3px;
      border-left: 1px solid #000;
    }
    .om-dow:first-child { border-left-color: transparent; }
    .om-week {
      flex: 1 1 0; min-height: 0;
      display: grid; grid-template-columns: repeat(7, minmax(0, 1fr));
      position: relative;
      border-top: 1px solid #000;
    }
    .om-dows + .om-week { border-top: 0; }
    .om-day {
      min-width: 0; min-height: 0; box-sizing: border-box;
      display: flex; flex-direction: column;
      border-left: 1px solid #000;
    }
    .om-day:first-child { border-left-color: transparent; }
    /* The weekend ground is a 4x4 tile (red at 12.5%), not a wash. */
    .om-day.is-weekend {
      background-image: ${WEEKEND.url}; background-size: ${WEEKEND.size};
    }
    /* The number row is exactly --om-num tall in the cell's own type size —
       the same size the band layer's offset resolves in — so chips start at
       the same height whether or not the cell has a band above them. The
       type size is set on the inner span for that reason. */
    .om-num {
      height: var(--om-num); box-sizing: border-box; flex: 0 0 auto;
      display: flex; justify-content: flex-end; align-items: flex-start;
      padding: 2px 6px 0;
    }
    .om-num-text { font-size: var(--fs-label); font-weight: var(--fw-bold); line-height: 1.1; }
    .om-mon {
      font-size: var(--fs-caption); font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      margin-right: 0.35em;
    }
    /* Today's chip fits inside the number row, so it moves nothing. */
    .om-day.is-today .om-num { padding: 1px 4px 0; }
    .om-day.is-today .om-num-text {
      background: var(--accent-1); color: var(--on-accent);
      width: 1.3em; height: 1.3em;
      display: grid; place-items: center;
      border-radius: 999px; font-weight: var(--fw-black);
    }
    /* A day already over keeps its chips as they are; only its number goes
       to regular weight. Hollow chips were tried and read as missing colour. */
    .om-day.is-past .om-num-text { font-weight: var(--fw-medium); }
    .om-bandroom { flex: 0 0 auto; }
    .om-list {
      flex: 1 1 auto; min-height: 0; overflow: hidden;
      display: flex; flex-direction: column; gap: var(--om-gap);
      padding: 0 5px 5px;
    }

    /* Chips. The stripe is a child, so it can be split per owner; a shared
       chip carries one fill segment per owner under the text. White chips
       need an edge to be chips: an outline, so the box keeps its size. */
    .om-chip {
      position: relative; overflow: hidden; isolation: isolate;
      flex: 0 0 auto; min-height: 1.3em; min-width: 0;
      display: flex; align-items: center; gap: 0.3em;
      padding: 0 0.25em 0 calc(0.25em + 5px);
      background-color: #fff;
      background-image: var(--om-fill); background-size: var(--om-fill-size, 2px 2px);
      color: var(--om-text, #000);
      outline: 1px solid #000; outline-offset: -1px;
      font-size: var(--fs-label); font-weight: var(--fw-black); line-height: 1.15;
    }
    .om-stripe { position: absolute; left: 0; top: 0; bottom: 0; width: 5px; background: #000; }
    .om-fillseg {
      position: absolute; left: 0; right: 0; z-index: -1;
      background-color: #fff;
      background-image: var(--om-fill); background-size: var(--om-fill-size, 2px 2px);
    }
    .om-name {
      flex: 1 1 auto; min-width: 0;
      color: inherit;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    /* A day with room lets a long title take a second line. */
    .om-chip.can-wrap .om-name {
      white-space: normal; word-break: break-word; hyphens: auto;
      /* One line plus this padding is exactly the chip's min-height, so a
         short title on a quiet day is the same height as on a busy one. */
      padding: 0.075em 0;
      display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2;
      -webkit-box-orient: vertical;
    }
    .om-marks { display: inline-flex; gap: 2px; flex: 0 0 auto; margin-left: auto; }
    .om-mark {
      display: inline-grid; place-items: center;
      width: 1.25em; height: 1.25em; flex: 0 0 auto;
      border-radius: var(--radius-0, 2px);
      font-size: 0.95em; font-weight: var(--fw-black);
      color: #000;
      background-color: #fff;
      background-image: var(--om-fill); background-size: var(--om-fill-size, 2px 2px);
      box-shadow: inset 0 0 0 1px #000;
    }
    /* An unassigned event has no letter; on an lg chip the empty mark is
       simply not there, on an md cell it is a hollow square. */
    .om-mark.is-none { display: none; background: #fff; }
    .om-chip.is-cut { display: none; }
    .om-more {
      flex: 0 0 auto;
      font-size: var(--fs-label); font-weight: var(--fw-black); line-height: 1.15;
      padding: 0 0.25em;
    }

    /* Bands over the week, one grid row per lane, starting under the day
       numbers. pointer-events off so they never sit in front of a cell in a
       way that matters; z-index so they paint over the cell rules. */
    .om-bands {
      position: absolute; left: 0; right: 0; top: var(--om-num);
      display: grid; grid-template-columns: repeat(7, minmax(0, 1fr));
      row-gap: var(--om-gap); z-index: 1; pointer-events: none;
    }
    /* A band fills its lane row, which is one chip tall (see --om-band). */
    .om-band { margin: 0 5px 0 6px; min-height: 0; height: auto; align-self: stretch; }

    /* Fill patterns: redundant with colour on a Spectra panel, and the only
       thing telling two people apart on a black-and-white one. The stroke is
       the line ink itself, so it stays a native pixel. */
    .om-p-solid { }
    .om-p-diag {
      background-image: repeating-linear-gradient(45deg,
        var(--om-stroke) 0 2px, transparent 2px 11px), var(--om-fill);
      background-size: auto, var(--om-fill-size, 2px 2px);
    }
    .om-p-dots {
      background-image: radial-gradient(var(--om-stroke) 1.4px, transparent 1.5px), var(--om-fill);
      background-size: 9px 9px, var(--om-fill-size, 2px 2px);
    }
    .om-p-horiz {
      background-image: repeating-linear-gradient(0deg,
        var(--om-stroke) 0 2px, transparent 2px 10px), var(--om-fill);
      background-size: auto, var(--om-fill-size, 2px 2px);
    }
    .om-p-cross {
      background-image:
        repeating-linear-gradient(45deg, var(--om-stroke) 0 2px, transparent 2px 12px),
        repeating-linear-gradient(-45deg, var(--om-stroke) 0 2px, transparent 2px 12px),
        var(--om-fill);
      background-size: auto, auto, var(--om-fill-size, 2px 2px);
    }

    /* md: a cell is ~90px wide by ~60px tall and the type does not shrink
       with it, so a title is out of the question. The chip collapses to its
       owner marks, laid out in a row, everything vertical takes the caption
       size, and the legend goes — the marks are the legend. The legend lives
       in the title bar, outside the container the query measures, so it is
       hidden by the size class instead. */
    .w.size-md .om-legend { display: none; }
    @container omcell (max-width: 700px) {
      .om-grid { --om-num: 0.85em; --om-band: 0.22em; --om-gap: 0.08em; }
      .om-dow { padding: 1px 0 2px; }
      .om-num { padding: 1px 4px 0; }
      .om-num-text { font-size: var(--fs-caption); }
      .om-day.is-today .om-num { padding: 0 2px 0; }
      .om-day.is-today .om-num-text { width: 1.15em; height: 1.15em; }
      .om-list { flex-direction: row; flex-wrap: wrap; align-content: flex-start;
                 gap: 1px; padding: 0 2px 1px; }
      .om-chip {
        min-height: 0; padding: 0; outline: none;
        background: none !important; gap: 0;
        font-size: var(--fs-caption);
      }
      .om-chip .om-stripe, .om-chip .om-fillseg,
      .om-chip .om-name, .om-chip.can-wrap .om-name { display: none; }
      .om-marks { margin-left: 0; }
      .om-mark { width: 0.95em; height: 0.95em; font-size: 1em; }
      .om-mark.is-none { display: inline-grid; }
      /* A band is a bar in its owner's fill, nothing more: there is no
         height for a mark, and the fill and stripe still say whose it is. */
      .om-band { margin: 0 2px 0 3px; outline: 1px solid #000;
                 background-image: var(--om-fill) !important;
                 background-size: var(--om-fill-size, 2px 2px) !important; }
      .om-band .om-stripe, .om-band .om-fillseg { display: block; }
      .om-band .om-marks { display: none; }
      .om-more { font-size: var(--fs-caption); padding: 0 2px; line-height: 1.05em; }
    }
  `;
}
