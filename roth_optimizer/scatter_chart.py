"""Renders optimize()'s own iteration_log (see optimizer.py's optimize()
docstring) as a self-contained, dependency-free HTML scatter chart --
lifetime taxes paid on the x-axis, after-tax estate value on the y-axis,
one dot per candidate schedule the pattern search evaluated. No
matplotlib/numpy -- plain Python string templating producing inline
SVG + vanilla JS (hover tooltip, nearest-point lookup), consistent with
the rest of this project's dependency-free, easily-auditable design (see
pt/charts.py's own docstring for the ONE place in this project that does
reach for an optional dependency, and why this deliberately doesn't).

Public entry point: render_scatter_html(). cli.py calls this from
`optimize --save-iterations foo.html` (see build_parser()'s help text for
that flag) -- writing to a .csv path instead goes through
_save_iteration_log() in cli.py, a plain csv.writer dump of the same
iteration_log with no chart involved."""
import html as html_lib
import json
import re


def _fmt_short(value: float) -> str:
    """1234567 -> '$1.23M', 45000 -> '$45K' -- for axis ticks/reference
    labels, where full comma-formatted amounts would crowd the chart."""
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{sign}${value / 1_000:,.0f}K"
    return f"{sign}${value:,.0f}"


def _fmt_full(value: float) -> str:
    return f"${value:,.0f}"


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Roth Optimizer Scatter</title>
<style>
  :root {
    color-scheme: light;
    --surface-1:      #fcfcfb;
    --page:           #f9f9f7;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --gridline:       #e1e0d9;
    --baseline:       #c3c2b7;
    --border:         rgba(11,11,11,0.10);
    --series-1:       #2a78d6;
    --accent:         #eb6834;
    --good:           #006300;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --surface-1:      #1a1a19;
      --page:           #0d0d0d;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #898781;
      --gridline:       #2c2c2a;
      --baseline:       #383835;
      --border:         rgba(255,255,255,0.10);
      --series-1:       #3987e5;
      --accent:         #d95926;
      --good:           #0ca30c;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --surface-1:      #1a1a19;
    --page:           #0d0d0d;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #898781;
    --gridline:       #2c2c2a;
    --baseline:       #383835;
    --border:         rgba(255,255,255,0.10);
    --series-1:       #3987e5;
    --accent:         #d95926;
    --good:           #0ca30c;
  }

  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--page);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  body { padding: 24px 16px 40px; }
  .wrap { max-width: 980px; margin: 0 auto; }
  header h1 { font-size: 20px; font-weight: 650; margin: 0 0 4px; letter-spacing: -0.01em; }
  header p { margin: 0; color: var(--text-secondary); font-size: 13.5px; line-height: 1.5; max-width: 70ch; }
  .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 22px 0 20px; }
  @media (max-width: 720px) { .kpis { grid-template-columns: repeat(2, 1fr); } }
  .tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
  .tile .label { font-size: 11.5px; color: var(--text-secondary); margin-bottom: 6px; }
  .tile .value { font-size: 20px; font-weight: 650; letter-spacing: -0.01em; }
  .tile .value.good { color: var(--good); }
  .tile .sub { font-size: 11.5px; color: var(--text-muted); margin-top: 3px; }
  .card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 18px 18px 8px; }
  .card h2 { font-size: 14.5px; font-weight: 650; margin: 0 0 2px; }
  .card .card-sub { font-size: 12px; color: var(--text-secondary); margin: 0 0 8px; }
  svg { display: block; width: 100%; height: auto; overflow: visible; }
  .axis-label { font-size: 11px; fill: var(--text-muted); }
  .tick-label { font-size: 10.5px; fill: var(--text-muted); }
  .grid-line { stroke: var(--gridline); stroke-width: 1; }
  .axis-line { stroke: var(--baseline); stroke-width: 1; }
  .pt { fill: var(--series-1); fill-opacity: 0.42; }
  .ref-hline { stroke: var(--text-muted); stroke-width: 1.25; stroke-dasharray: 4 3; }
  .ref-ring { fill: none; stroke: var(--surface-1); stroke-width: 2; }
  .ref-mark { fill: var(--text-muted); }
  .ref-label { font-size: 11px; font-weight: 600; fill: var(--text-secondary); }
  .winner-ring { fill: none; stroke: var(--surface-1); stroke-width: 2; }
  .winner-dot { fill: var(--accent); }
  .winner-label { font-size: 11px; font-weight: 600; fill: var(--text-primary); }
  .age-ref-leader { stroke: var(--text-muted); stroke-width: 1; stroke-dasharray: 2 3; opacity: 0.55; fill: none; }
  .age-ref-mark { fill: none; stroke: var(--text-muted); stroke-width: 1.5; }
  .age-ref-label { font-size: 9.5px; font-weight: 600; fill: var(--text-muted); }
  .age-winner-leader { stroke: var(--accent); stroke-width: 1; stroke-dasharray: 2 3; opacity: 0.55; fill: none; }
  .age-winner-mark { fill: none; stroke: var(--accent); stroke-width: 1.5; }
  .age-winner-label { font-size: 9.5px; font-weight: 600; fill: var(--accent); }
  .crosshair-ring { fill: none; stroke: var(--surface-1); stroke-width: 2; }
  .crosshair-dot { fill: var(--text-primary); }
  #tooltip {
    position: absolute; pointer-events: none;
    background: var(--text-primary); color: var(--page);
    border-radius: 8px; padding: 7px 10px; font-size: 11.5px; line-height: 1.45;
    white-space: nowrap; opacity: 0;
    transform: translate(-50%, calc(-100% - 10px));
    transition: opacity 0.08s ease; z-index: 5;
  }
  #tooltip .v { font-weight: 650; font-size: 12.5px; }
  #tooltip .k { opacity: 0.72; }
  .chart-pos { position: relative; }
  footer { margin-top: 18px; font-size: 11.5px; color: var(--text-muted); line-height: 1.6; max-width: 76ch; }
  footer strong { color: var(--text-secondary); font-weight: 600; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Lifetime taxes vs. after-tax estate value</h1>
    <p>__SUBTITLE__</p>
  </header>

  <div class="kpis">
__KPI_TILES__
  </div>

  <div class="card">
    <h2>Lifetime taxes paid vs. after-tax estate value</h2>
    <p class="card-sub">__CARD_SUB__</p>
    <div class="chart-pos">
      <svg id="chart" viewBox="0 0 940 520" role="img" aria-label="Scatter plot of lifetime taxes paid versus after-tax estate value for every schedule the Roth conversion optimizer evaluated"></svg>
      <div id="tooltip"><div class="v" id="tt-y"></div><div class="k" id="tt-x"></div></div>
    </div>
  </div>

  <footer>
    <strong>After-tax estate value</strong> = taxable + Roth + inherited-Roth balances, plus
    Traditional (owned + inherited) balances taxed at the __HEIR_RATE_PCT__ assumed heir rate --
    valued as of the household's own stated life expectancy, not necessarily the projection's last
    row. <strong>Lifetime taxes paid</strong> sums the household's own federal tax owed every year
    from the start of the projection through that same horizon.<br><br>
    The search is coordinate-ascent / pattern search (nudge one year's amount at a time from a few
    starting points, keep whatever improves the objective), not a random Monte Carlo sweep -- so
    this cloud traces the paths the search explored on its way to a good schedule, rather than a
    uniform random sample of the whole space. See roth_optimizer/README.md.
  </footer>
</div>

<script>
const DATA = __POINTS_JSON__;
const WINNER = __WINNER_JSON__;
const REFS = __REFS_JSON__;
const WINNER_AGE = __WINNER_AGE_JSON__;
const AGE_SERIES = __AGE_SERIES_JSON__;

const svg = document.getElementById('chart');
const svgNS = 'http://www.w3.org/2000/svg';
const W = 940, H = 520;
const M = { top: 16, right: 28, bottom: 52, left: 84 };
const plotW = W - M.left - M.right;
const plotH = H - M.top - M.bottom;

function niceStep(rawStep) {
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  let step;
  if (norm < 1.5) step = 1;
  else if (norm < 3) step = 2;
  else if (norm < 7) step = 5;
  else step = 10;
  return step * mag;
}
function niceTicks(min, max, count) {
  const span = max - min || 1;
  const step = niceStep(span / count);
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step * 0.5; v += step) ticks.push(Math.round(v));
  return ticks;
}

const refX = REFS.map(function (r) { return r.x; });
const refY = REFS.map(function (r) { return r.y; });
const ageSeriesX = [];
const ageSeriesY = [];
['zero', 'original', 'winner'].forEach(function (key) {
  (AGE_SERIES[key] || []).forEach(function (pt) { ageSeriesX.push(pt.x); ageSeriesY.push(pt.y); });
});
const allX = DATA.map(function (p) { return p[0]; }).concat(refX, ageSeriesX, [WINNER[0]]);
const allY = DATA.map(function (p) { return p[1]; }).concat(refY, ageSeriesY, [WINNER[1]]);
const xTicks = niceTicks(Math.min.apply(null, allX), Math.max.apply(null, allX), 7);
const yTicks = niceTicks(Math.min.apply(null, allY), Math.max.apply(null, allY), 7);
const xMin = xTicks[0], xMax = xTicks[xTicks.length - 1];
const yMin = yTicks[0], yMax = yTicks[yTicks.length - 1];

function xPix(v) { return M.left + ((v - xMin) / (xMax - xMin)) * plotW; }
function yPix(v) { return M.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH; }

function fmtMoney(v) {
  const abs = Math.abs(v);
  if (abs >= 1000000) return '$' + (v / 1000000).toFixed(2) + 'M';
  if (abs >= 1000) return '$' + Math.round(v / 1000) + 'K';
  return '$' + Math.round(v);
}
function fmtFull(v) { return '$' + Math.round(v).toLocaleString('en-US'); }

function el(tag, attrs) {
  const e = document.createElementNS(svgNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

yTicks.forEach(function (t) {
  const y = yPix(t);
  svg.appendChild(el('line', { x1: M.left, x2: W - M.right, y1: y, y2: y, class: 'grid-line' }));
  const label = el('text', { x: M.left - 10, y: y + 3.5, class: 'tick-label', 'text-anchor': 'end' });
  label.textContent = fmtMoney(t);
  svg.appendChild(label);
});
xTicks.forEach(function (t) {
  const x = xPix(t);
  const label = el('text', { x: x, y: H - M.bottom + 20, class: 'tick-label', 'text-anchor': 'middle' });
  label.textContent = fmtMoney(t);
  svg.appendChild(label);
});
svg.appendChild(el('line', { x1: M.left, x2: W - M.right, y1: M.top + plotH, y2: M.top + plotH, class: 'axis-line' }));
svg.appendChild(el('line', { x1: M.left, x2: M.left, y1: M.top, y2: M.top + plotH, class: 'axis-line' }));
const xTitle = el('text', { x: M.left + plotW / 2, y: H - 8, class: 'axis-label', 'text-anchor': 'middle' });
xTitle.textContent = 'Lifetime taxes paid \\u2192';
svg.appendChild(xTitle);
const yTitle = el('text', {
  x: -(M.top + plotH / 2), y: 18, class: 'axis-label', 'text-anchor': 'middle', transform: 'rotate(-90)'
});
yTitle.textContent = 'After-tax estate value \\u2192';
svg.appendChild(yTitle);

// horizontal dashed reference lines (e.g. "no conversions", "current
// plan") -- drawn full-width, under the data cloud, so the cloud and the
// reference markers below stay on top of them
function refHLine(point) {
  const y = yPix(point.y);
  svg.appendChild(el('line', { x1: M.left, x2: W - M.right, y1: y, y2: y, class: 'ref-hline' }));
  const label = el('text', { x: W - M.right, y: y - 6, class: 'ref-label', 'text-anchor': 'end' });
  label.textContent = point.label + ' \\u2014 ' + fmtMoney(point.y);
  svg.appendChild(label);
}
REFS.forEach(refHLine);

// data cloud
const dotsGroup = el('g', {});
DATA.forEach(function (p) {
  dotsGroup.appendChild(el('circle', { cx: xPix(p[0]), cy: yPix(p[1]), r: 2.6, class: 'pt' }));
});
svg.appendChild(dotsGroup);

// reference markers (e.g. "no conversions", "current plan") -- neutral
// shape-coded glyphs, distinct from the data hue and the winner accent
// pinpointing exactly where that reference schedule sits (its own lifetime
// taxes) -- refHLine() below already carries the y-value label, so these are
// shape-only, no text of their own (avoids repeating it).
function refMarker(point) {
  const x = xPix(point.x), y = yPix(point.y);
  if (point.shape === 'diamond') {
    const r = 6;
    const pts = [[x, y - r], [x + r, y], [x, y + r], [x - r, y]].map(function (pr) { return pr.join(','); }).join(' ');
    svg.appendChild(el('polygon', { points: pts, class: 'ref-ring' }));
    svg.appendChild(el('polygon', { points: pts, class: 'ref-mark' }));
  } else {
    const s = 8;
    svg.appendChild(el('rect', { x: x - s / 2, y: y - s / 2, width: s, height: s, rx: 1.5, class: 'ref-ring' }));
    svg.appendChild(el('rect', { x: x - s / 2, y: y - s / 2, width: s, height: s, rx: 1.5, class: 'ref-mark' }));
  }
}
REFS.forEach(refMarker);

// age-projection trajectories -- optional (AGE_SERIES entries are only
// non-empty when optimize()'s own projection_ages was given). For each
// of the three schedules: a short dotted polyline from its
// life-expectancy marker through each requested age (ascending), a
// small hollow marker (same shape as that schedule's own marker above)
// at every stop, and a compact age number beside each one -- the exact
// dollar numbers live in a native tooltip (hover/focus) rather than more
// on-chart text, so an arbitrarily long age list never floods the chart
// the way labeling every point with its value would.
function polyline(points, cls) {
  if (points.length < 2) return;
  const attrPoints = points.map(function (p) { return xPix(p.x) + ',' + yPix(p.y); }).join(' ');
  svg.appendChild(el('polyline', { points: attrPoints, class: cls }));
}
function ageMarker(pt, shape, baseCls, seriesLabel) {
  const x = xPix(pt.x), y = yPix(pt.y);
  let mark;
  if (shape === 'circle') {
    mark = el('circle', { cx: x, cy: y, r: 4.5, class: baseCls + '-mark' });
  } else if (shape === 'diamond') {
    const r = 5;
    const pts = [[x, y - r], [x + r, y], [x, y + r], [x - r, y]].map(function (pr) { return pr.join(','); }).join(' ');
    mark = el('polygon', { points: pts, class: baseCls + '-mark' });
  } else {
    const s = 7;
    mark = el('rect', { x: x - s / 2, y: y - s / 2, width: s, height: s, rx: 1.5, class: baseCls + '-mark' });
  }
  const title = document.createElementNS(svgNS, 'title');
  title.textContent = seriesLabel + ' \\u2014 age ' + pt.age + ': ' + fmtFull(pt.y) + ' (' + fmtFull(pt.x) + ' lifetime taxes)';
  mark.appendChild(title);
  svg.appendChild(mark);
  const flip = x > W - M.right - 150;
  const label = el('text', {
    x: x + (flip ? -8 : 8), y: y + 3, class: baseCls + '-label', 'text-anchor': flip ? 'end' : 'start'
  });
  label.textContent = String(pt.age);
  svg.appendChild(label);
}
function drawAgeSeries(key, originPoint, shape, baseCls, seriesLabel) {
  const pts = AGE_SERIES[key];
  if (!pts || pts.length === 0) return;
  const sorted = pts.slice().sort(function (a, b) { return a.age - b.age; });
  polyline([originPoint].concat(sorted), baseCls + '-leader');
  sorted.forEach(function (pt) { ageMarker(pt, shape, baseCls, seriesLabel); });
}
drawAgeSeries('zero', REFS[0], 'diamond', 'age-ref', REFS[0].label);
if (REFS.length > 1) {
  drawAgeSeries('original', REFS[1], 'square', 'age-ref', REFS[1].label);
}

// winner marker
const wx = xPix(WINNER[0]), wy = yPix(WINNER[1]);
svg.appendChild(el('circle', { cx: wx, cy: wy, r: 7, class: 'winner-ring' }));
svg.appendChild(el('circle', { cx: wx, cy: wy, r: 5, class: 'winner-dot' }));
const wFlip = wx > W - M.right - 150;
const wLabel = el('text', {
  x: wx + (wFlip ? -12 : 12), y: wy - 10, class: 'winner-label', 'text-anchor': wFlip ? 'end' : 'start'
});
wLabel.textContent = (WINNER_AGE !== null ? 'Optimized (age ' + WINNER_AGE + ') ' : 'Optimized ') + '\\u2014 ' + fmtMoney(WINNER[1]);
svg.appendChild(wLabel);

drawAgeSeries('winner', { x: WINNER[0], y: WINNER[1] }, 'circle', 'age-winner', 'Optimized');

// hover: nearest-point crosshair + tooltip
const hoverRing = el('circle', { r: 6, class: 'crosshair-ring', style: 'display:none' });
const hoverDot = el('circle', { r: 4, class: 'crosshair-dot', style: 'display:none' });
svg.appendChild(hoverRing);
svg.appendChild(hoverDot);

const tooltip = document.getElementById('tooltip');
const ttY = document.getElementById('tt-y');
const ttX = document.getElementById('tt-x');
const chartPos = document.querySelector('.chart-pos');

function nearestPoint(px, py) {
  let best = null, bestD = Infinity;
  for (let i = 0; i < DATA.length; i++) {
    const dx = xPix(DATA[i][0]) - px, dy = yPix(DATA[i][1]) - py;
    const d = dx * dx + dy * dy;
    if (d < bestD) { bestD = d; best = DATA[i]; }
  }
  return best;
}

function handleMove(evt) {
  const rect = svg.getBoundingClientRect();
  const scale = W / rect.width;
  const px = (evt.clientX - rect.left) * scale;
  const py = (evt.clientY - rect.top) * scale;
  if (px < M.left || px > W - M.right || py < M.top || py > M.top + plotH) {
    hoverRing.style.display = 'none';
    hoverDot.style.display = 'none';
    tooltip.style.opacity = 0;
    return;
  }
  const p = nearestPoint(px, py);
  const cx = xPix(p[0]), cy = yPix(p[1]);
  hoverRing.setAttribute('cx', cx); hoverRing.setAttribute('cy', cy);
  hoverDot.setAttribute('cx', cx); hoverDot.setAttribute('cy', cy);
  hoverRing.style.display = ''; hoverDot.style.display = '';

  ttY.textContent = fmtFull(p[1]) + ' estate value';
  ttX.textContent = fmtFull(p[0]) + ' lifetime taxes';
  const tipRect = chartPos.getBoundingClientRect();
  tooltip.style.left = ((cx / W) * tipRect.width) + 'px';
  tooltip.style.top = ((cy / H) * tipRect.height) + 'px';
  tooltip.style.opacity = 1;
}
svg.addEventListener('pointermove', handleMove);
svg.addEventListener('pointerleave', function () {
  hoverRing.style.display = 'none';
  hoverDot.style.display = 'none';
  tooltip.style.opacity = 0;
});
</script>
</body>
</html>
"""


def _kpi_tile(label: str, value: str, sub: str, good: bool = False) -> str:
    cls = "value good" if good else "value"
    return (f'    <div class="tile"><div class="label">{label}</div>'
            f'<div class="{cls}">{value}</div><div class="sub">{sub}</div></div>')


def render_scatter_html(iteration_log: list, winner: tuple, zero_point: tuple, original_point,
                         heir_tax_rate: float, scenario_label: str, seed, restarts: int,
                         evaluations: int, winner_age, age_projections: dict = None) -> str:
    """Returns a complete, self-contained HTML document (inline CSS/JS, no
    external dependencies or network calls) rendering `iteration_log` --
    optimize()'s own return value's namesake parameter (see that
    function's docstring): a list of {"lifetime_taxes",
    "after_tax_estate_value"} dicts, one per candidate schedule the
    pattern search evaluated -- as a scatter, x=lifetime_taxes,
    y=after_tax_estate_value.

    winner: (lifetime_taxes, after_tax_estate_value) for the schedule
    optimize() actually returned -- optimize()'s own "lifetime_taxes"/
    "value" keys, paired. Drawn as a distinct accent-colored marker.
    zero_point: same shape, for optimize()'s "zero_conversions_
    lifetime_taxes"/"zero_conversions_value" (no discretionary
    conversions/distributions at all) -- always given. original_point:
    same shape for "original_lifetime_taxes"/"original_value" (the
    scenario's OWN configuration, run as-is), or None if optimize()'s
    "original_value" was None (nothing to compare against -- see that
    key's docstring). Both are drawn as neutral shape-coded markers
    (diamond / square), distinct from the data cloud's hue and the
    winner's accent, so reference points read as annotations, not more
    data.

    heir_tax_rate: a fraction (e.g. 0.24), matching optimize()'s own
    parameter -- shown in the footer/subtitle as a percentage.
    scenario_label, seed, restarts, evaluations: shown in the header/KPI
    row for context (what was searched, how, how much of it).

    winner_age: optimize()'s own life_expectancy_age -- the primary
    person's age in the year `winner`/`zero_point`/`original_point` are
    valued as of -- shown alongside the winner's own label, or omitted
    (None) if life_expectancy isn't configured for anyone.

    age_projections (optional): optimize()'s own age_projections return
    value -- None (the default) if optimize() wasn't given
    projection_ages, in which case the chart looks exactly like it did
    before this parameter existed (just the two life-expectancy
    reference points/lines above). Otherwise a dict with "winner"/
    "zero_conversions"/"original" keys, each a list of {"age",
    "lifetime_taxes", "after_tax_estate_value"} dicts (see optimize()'s
    docstring) -- rendered as a short dotted trajectory from that
    schedule's own life-expectancy marker through each requested age in
    order, with a small hollow marker and a compact age number at every
    stop (exact numbers on hover, native tooltip -- see _TEMPLATE's own
    script). "original" is None exactly when original_point is."""
    points = [[round(e["lifetime_taxes"]), round(e["after_tax_estate_value"])] for e in iteration_log]

    subtitle = (
        f"Every Roth-conversion schedule the optimizer's search evaluated for {html_lib.escape(scenario_label)} "
        f"— heir tax rate {heir_tax_rate * 100:g}%"
        + (f", seed {seed}" if seed is not None else ", no fixed seed")
        + f", {restarts} restart{'s' if restarts != 1 else ''}, {evaluations:,} candidate schedules scored."
    )

    tiles = [
        _kpi_tile("Best schedule found", _fmt_full(winner[1]), "After-tax estate value"),
    ]
    if original_point is not None:
        delta = winner[1] - original_point[1]
        tiles.append(_kpi_tile("vs. current plan", f"+{_fmt_full(delta)}" if delta >= 0 else _fmt_full(delta),
                                "This scenario's own configuration", good=delta >= 0))
    delta_zero = winner[1] - zero_point[1]
    tiles.append(_kpi_tile("vs. no conversions", f"+{_fmt_full(delta_zero)}" if delta_zero >= 0
                            else _fmt_full(delta_zero), "Doing nothing at all", good=delta_zero >= 0))
    tiles.append(_kpi_tile("Schedules evaluated", f"{evaluations:,}", f"Across {restarts} search restart"
                            + ("s" if restarts != 1 else "")))

    refs = [{"x": round(zero_point[0]), "y": round(zero_point[1]), "label": "No conversions", "shape": "diamond"}]
    if original_point is not None:
        refs.append({"x": round(original_point[0]), "y": round(original_point[1]), "label": "Current plan",
                      "shape": "square"})

    def _series_points(entries):
        if not entries:
            return []
        return [{"age": e["age"], "x": round(e["lifetime_taxes"]), "y": round(e["after_tax_estate_value"])}
                for e in entries]

    age_series = {
        "winner": _series_points((age_projections or {}).get("winner")),
        "zero": _series_points((age_projections or {}).get("zero_conversions")),
        "original": _series_points((age_projections or {}).get("original")),
    }
    requested_ages = sorted({pt["age"] for pts in age_series.values() for pt in pts})

    card_sub = ("Each blue dot is one candidate conversion schedule the search tried. Hover to inspect a point.")
    if requested_ages:
        card_sub += (" Hollow markers trace the optimized/current-plan/no-conversions schedules through age "
                     + ", ".join(str(a) for a in requested_ages) + " -- hover a marker for its exact numbers.")

    html = _TEMPLATE
    html = html.replace("__SUBTITLE__", subtitle)
    html = html.replace("__KPI_TILES__", "\n".join(tiles))
    html = html.replace("__HEIR_RATE_PCT__", f"{heir_tax_rate * 100:g}%")
    html = html.replace("__CARD_SUB__", card_sub)
    html = html.replace("__POINTS_JSON__", json.dumps(points, separators=(",", ":")))
    html = html.replace("__WINNER_JSON__", json.dumps([round(winner[0]), round(winner[1])]))
    html = html.replace("__REFS_JSON__", json.dumps(refs, separators=(",", ":")))
    html = html.replace("__WINNER_AGE_JSON__", json.dumps(winner_age))
    html = html.replace("__AGE_SERIES_JSON__", json.dumps(age_series, separators=(",", ":")))
    return html


class ScatterChartParseError(Exception):
    pass


def parse_scatter_html(text: str) -> dict:
    """The data render_scatter_html() embedded in `text` (a chart file it
    wrote), read back out -- what pt/report.py uses to redraw the chart as
    a native Excel scatter (Excel can't show an HTML page in a sheet).
    Returns {"points": [[lifetime_taxes, after_tax_estate_value], ...] --
    every candidate schedule the search scored, "winner": [x, y],
    "refs": [{"x", "y", "label", "shape"}, ...] -- the No conversions/
    Current plan reference points, "winner_age": int or None,
    "age_series": {"winner"/"zero"/"original": [{"age", "x", "y"}, ...]}}.
    Raises ScatterChartParseError if `text` isn't a chart this module
    wrote (a constant is missing or isn't valid JSON) -- the constants
    are exactly the ones _TEMPLATE's own script declares."""
    def _const(name: str):
        match = re.search(rf"^const {name} = (.*);\s*$", text, flags=re.MULTILINE)
        if not match:
            raise ScatterChartParseError(f"no 'const {name}' found -- not a scatter chart this tool wrote.")
        try:
            return json.loads(match.group(1))
        except ValueError as e:
            raise ScatterChartParseError(f"'const {name}' isn't valid JSON: {e}")
    return {
        "points": _const("DATA"),
        "winner": _const("WINNER"),
        "refs": _const("REFS"),
        "winner_age": _const("WINNER_AGE"),
        "age_series": _const("AGE_SERIES"),
    }
