/**
 * outlook_week, placeholder render.
 *
 * Structure and data only for now: this lists what fetch() returned so the
 * pipeline is visible end to end. The real per-size layout (archetype body,
 * category colours, density at xs/sm) is a later step.
 */
export default function render(shadow, ctx) {
  const { size } = ctx.cell;
  const data = ctx.data || {};

  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/style/spectra-widgets.css">
      <div class="w size-${size}" data-widget="outlook_week">
        <div class="w-title">Outlook</div>
        <div class="w-body list-body">
          <div class="u-muted"><i class="ph-bold ph-warning-circle"></i> ${esc(data.error)}</div>
        </div>
      </div>`;
    return;
  }

  const days = Array.isArray(data.days) ? data.days : [];
  const rows = days
    .flatMap((day) =>
      (day.events || []).map((event) => `
        <div class="list-row">
          <span>${esc(shortDay(day))} ${esc(time(event))}</span>
          <span>${esc(event.summary || "")}</span>
          <span class="u-muted">${esc(event.location || "")}</span>
        </div>`),
    )
    .join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <div class="w size-${size}" data-widget="outlook_week">
      <div class="w-title">Outlook${data.stale ? " · cached" : ""}</div>
      <div class="w-body list-body">
        ${rows || `<div class="u-muted">Nothing scheduled.</div>`}
      </div>
    </div>`;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function shortDay(day) {
  return String(day.weekday || "").slice(0, 3);
}

function time(event) {
  if (event.all_day) return "all day";
  const start = String(event.start || "");
  const clock = start.slice(11, 16);
  return clock || "";
}
