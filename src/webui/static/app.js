/* grid-search webui — scatter of every run + combo-level Pareto envelope.
 *
 * Metrics come from /api/runs (one point per run) and /api/pareto (combo-level
 * aggregates).  The Pareto front is recomputed client-side because it depends
 * on the selected X/Y axes; the server front (fps vs power) drives the side
 * panel marker.
 */
"use strict";

// ── metric catalogue ────────────────────────────────────────────────────
// dir: which end of the axis is "good" — drives the dominance test.
// Chinese-only labels (deliverable audience); min-metrics get an inverted
// axis so "up/right = better" always holds visually.
const METRICS = {
  fps_avg:      { label: "平均帧率",     short: "平均帧率",   dir: "max", digits: 2 },
  jank_rate:    { label: "掉帧率 /s",    short: "掉帧率 /s",  dir: "min", digits: 2 },
  jank_l_rate:  { label: "大掉帧率 /s",  short: "大掉帧率 /s", dir: "min", digits: 3 },
  fps_std:      { label: "帧率波动",     short: "帧率波动",   dir: "min", digits: 2 },
  power_avg_mw: { label: "平均功耗 (mW)", short: "平均功耗",   dir: "min", digits: 0 },
};
const AXES = ["fps_avg", "jank_rate", "jank_l_rate", "fps_std", "power_avg_mw"];

// series order is fixed; render() rebuilds the option with notMerge so the
// indices stay stable and can be used in chart.on(...) queries.
const S = { ENVELOPE: 0, RUNS: 1, FRONT: 2, RING: 3, SPREAD: 4, SELECTED: 5 };

const CSS = {
  runs: "#2a78d6",
  front: "#eb6834",
  select: "#0b0b0b",
  surface: "#fcfcfb",
  ink: "#0b0b0b",
  ink2: "#52514e",
  muted: "#898781",
  grid: "#e1e0d9",
  axis: "#c3c2b7",
};

const state = {
  runs: [],
  abbr: {},
  serverFront: new Set(),   // combo keys on the server-side (fps vs power) front
  x: "fps_avg",
  y: "power_avg_mw",
  group: "0",               // "all" | "0" | "1" | "2" (replay group filter)
  selected: -1,             // index into state.runs
  loaded: false,
};

let chart = null;
let combos = [];            // current combo aggregates
let plotted = [];           // index of state.runs -> index in the "runs" series
let flashTimer = 0;

// ── helpers ─────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

function metricValue(run, key) {
  if (key === "jank_rate" || key === "jank_l_rate") {
    const n = key === "jank_rate" ? run.jank : run.jank_l;
    if (n === null || n === undefined || !(run.dur_s > 0)) return null;
    return n / run.dur_s;
  }
  const v = run[key];
  return v === null || v === undefined || Number.isNaN(v) ? null : v;
}

function fmtNum(v, digits) {
  if (v === null || v === undefined) return "—";
  return v.toFixed(digits);
}

function fmtMetric(run, key) {
  const m = METRICS[key];
  const v = metricValue(run, key);
  if (v === null) return "—";
  return fmtNum(v, m.digits) + (key === "power_avg_mw" ? " mW" : "");
}

function abbrOf(key) {
  return state.abbr[key] || key;
}

function comboKey(params) {
  const p = params || {};
  return Object.keys(p).sort().map((k) => k + "=" + p[k]).join("|");
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

function axisFormatter(key) {
  const m = METRICS[key];
  return (v) => fmtNum(v, m.digits);
}

// Monotone cubic interpolation (Fritsch-Carlson): the curve passes exactly
// through every (xs, ys) point and never overshoots between them — the
// right shape for a Pareto front, where a polynomial fit would invent
// phantom "better-than-front" regions between points. Returns dense
// [x, y] pairs for a line series. Handles the 2-point case as a segment.
function monotoneCurve(xs, ys) {
  const n = xs.length;
  if (n < 2) return xs.map((x, i) => [x, ys[i]]);
  if (n === 2) return [[xs[0], ys[0]], [xs[1], ys[1]]];

  // slopes of the secant lines
  const dx = [], dy = [], m = [];
  for (let i = 0; i < n - 1; i++) {
    dx.push(xs[i + 1] - xs[i]);
    dy.push(ys[i + 1] - ys[i]);
    m.push(dy[i] / (dx[i] || 1e-12));
  }
  // tangents at the knots
  const t = [m[0]];
  for (let i = 1; i < n - 1; i++) {
    if (m[i - 1] * m[i] <= 0) {
      t.push(0);  // local extremum: flat tangent keeps it monotone
    } else {
      const w1 = 2 * dx[i] + dx[i - 1];
      const w2 = dx[i] + 2 * dx[i - 1];
      t.push((w1 + w2) / (w1 / m[i - 1] + w2 / m[i]));
    }
  }
  t.push(m[n - 2]);

  // dense samples per segment
  const out = [];
  for (let i = 0; i < n - 1; i++) {
    const steps = 24;
    for (let s = 0; s < steps; s++) {
      const u = s / steps;
      const h00 = 2 * u ** 3 - 3 * u ** 2 + 1;
      const h10 = u ** 3 - 2 * u ** 2 + u;
      const h01 = -2 * u ** 3 + 3 * u ** 2;
      const h11 = u ** 3 - u ** 2;
      out.push([
        xs[i] + u * dx[i],
        h00 * ys[i] + h10 * dx[i] * t[i] + h01 * ys[i + 1] + h11 * dx[i] * t[i + 1],
      ]);
    }
  }
  out.push([xs[n - 1], ys[n - 1]]);
  return out;
}

// ── combos + Pareto front (client-side, axis dependent) ─────────────────

// Replay group of a run: meta.replay_index (null = the pre-grouping era,
// which played card 0 by definition) -> "0" / "1" / "2" ...
function groupOf(run) {
  return String(run.replay_index ?? 0);
}

// Runs visible under the current group filter
function visibleRuns() {
  if (state.group === "all") return state.runs;
  return state.runs.filter((r) => groupOf(r) === state.group);
}

function buildCombos(runs) {
  const byKey = new Map();
  for (const run of runs) {
    const k = comboKey(run.params);
    let c = byKey.get(k);
    if (!c) {
      c = { key: k, params: run.params, runs: [], mean: {} };
      byKey.set(k, c);
    }
    c.runs.push(run);
  }
  for (const c of byKey.values()) {
    for (const key of AXES) {
      let sum = 0;
      let n = 0;
      for (const r of c.runs) {
        const v = metricValue(r, key);
        if (v !== null) { sum += v; n += 1; }
      }
      c.mean[key] = n ? sum / n : null;
    }
  }
  return [...byKey.values()];
}

/** Combos not dominated on (x, y); direction per axis. Ties survive. */
function paretoCombos(list, xk, yk) {
  const pts = list.filter((c) => c.mean[xk] !== null && c.mean[yk] !== null);
  const dx = METRICS[xk].dir;
  const dy = METRICS[yk].dir;
  const better = (a, b, d) => (d === "max" ? a > b : a < b);
  const atLeast = (a, b, d) => (d === "max" ? a >= b : a <= b);
  return pts.filter((p, i) => !pts.some((q, j) => (
    j !== i &&
    atLeast(q.mean[xk], p.mean[xk], dx) &&
    atLeast(q.mean[yk], p.mean[yk], dy) &&
    (better(q.mean[xk], p.mean[xk], dx) || better(q.mean[yk], p.mean[yk], dy))
  )));
}

// ── tooltips ────────────────────────────────────────────────────────────

function row(label, value) {
  return `<div class="tt-row"><span>${esc(label)}</span><b>${esc(value)}</b></div>`;
}

function paramLines(params) {
  return Object.keys(params || {}).sort().map((k) => {
    const a = abbrOf(k);
    const full = a === k ? "" : ` <span class="tt-full">${esc(k)}</span>`;
    return `<div class="tt-kv"><span>${esc(a)}${full}</span><b>${esc(params[k])}</b></div>`;
  }).join("");
}

function runTooltip(run) {
  const rate = (key) => {
    const v = metricValue(run, key);
    return v === null ? "—" : fmtNum(v, METRICS[key].digits) + " /s";
  };
  return `<div class="tt">
    <div class="tt-title">run ${esc(run.time)}</div>
    ${row("平均帧率", fmtMetric(run, "fps_avg") + " fps")}
    ${row("帧率std", fmtMetric(run, "fps_std"))}
    ${row("掉帧率/s", rate("jank_rate"))}
    ${row("大掉帧率/s", rate("jank_l_rate"))}
    ${row("平均功耗", run.power_avg_mw === null ? "—" : fmtNum(run.power_avg_mw, 1) + " mW")}
    ${row("功耗中位数", run.power_median_mw === null ? "—" : fmtNum(run.power_median_mw, 1) + " mW")}
    ${row("放电占比", run.discharge_ratio === null ? "—" : (run.discharge_ratio * 100).toFixed(1) + " %")}
    ${row("掉帧数 12/16.67/125ms", `${run.jank_s} / ${run.jank} / ${run.jank_l}`)}
    ${row("时长 / 帧数", `${fmtNum(run.dur_s, 1)} s / ${run.n_frames}`)}
    <div class="tt-sep"></div>
    <div class="tt-sub">参数</div>
    ${paramLines(run.params)}
    <div class="tt-sep"></div>
    <div class="tt-kv"><span>trace</span><b>${esc(run.trace.split("/").pop())}</b></div>
  </div>`;
}

function comboSpread(combo, xk, yk) {
  const pick = (k) => combo.runs
    .map((r) => metricValue(r, k))
    .filter((v) => v !== null);
  const range = (k) => {
    const vs = pick(k);
    if (!vs.length) return "—";
    const d = METRICS[k].digits;
    return vs.length === 1
      ? fmtNum(vs[0], d)
      : `${fmtNum(Math.min(...vs), d)} – ${fmtNum(Math.max(...vs), d)}`;
  };
  return `${range(xk)} / ${range(yk)}`;
}

function frontTooltip(combo, xk, yk) {
  const onServerFront = state.serverFront.has(combo.key);
  return `<div class="tt">
    <div class="tt-title">前沿组合 · ${combo.runs.length} 个 run</div>
    ${row("平均帧率", fmtNum(combo.mean.fps_avg, 2) + " fps")}
    ${row("帧率std", fmtNum(combo.mean.fps_std, 2))}
    ${row("掉帧率/s", fmtNum(combo.mean.jank_rate, 2) + " /s")}
    ${row("大掉帧率/s", fmtNum(combo.mean.jank_l_rate, 3) + " /s")}
    ${row("平均功耗", combo.mean.power_avg_mw === null ? "—" : fmtNum(combo.mean.power_avg_mw, 1) + " mW")}
    <div class="tt-sep"></div>
    <div class="tt-sub">${esc(METRICS[xk].short)} / ${esc(METRICS[yk].short)} 原始 run 分布</div>
    ${row("min – max", comboSpread(combo, xk, yk))}
    ${onServerFront ? row("服务端前沿 (fps vs 功耗)", "是") : ""}
    <div class="tt-sep"></div>
    <div class="tt-sub">参数</div>
    ${paramLines(combo.params)}
  </div>`;
}

// ── chart ───────────────────────────────────────────────────────────────

function baseOption() {
  return {
    animationDuration: 320,
    animationDurationUpdate: 220,
    // generous breathing room around the plot
    grid: { left: 64, right: 78, top: 66, bottom: 36, containLabel: true },
    legend: {
      top: 0,
      left: 0,
      itemGap: 20,
      icon: "circle",
      itemWidth: 12,
      itemHeight: 12,
      // only the two encodings that carry identity; the hover-only series
      // (ring / spread dots / selection) stay out of the legend
      data: ["全部 run", "前沿组合均值", "前沿包络线"],
      textStyle: { color: CSS.ink2, fontSize: 13 },
      inactiveColor: "#c3c2b7",
    },
    tooltip: {
      trigger: "item",
      confine: true,
      backgroundColor: CSS.surface,
      borderColor: "rgba(11,11,11,0.10)",
      borderWidth: 1,
      padding: [11, 14],
      textStyle: { color: CSS.ink, fontSize: 13.5 },
      extraCssText: "box-shadow:0 6px 20px rgba(11,11,11,0.12);border-radius:8px;",
    },
  };
}

function axisCommon(key) {
  const m = METRICS[key];
  return {
    type: "value",
    name: m.label,
    nameLocation: "middle",
    nameGap: 34,
    nameTextStyle: { color: CSS.ink2, fontSize: 14 },
    scale: true,
    inverse: m.dir === "min",  // up/right = better for every metric
    axisLine: { show: true, lineStyle: { color: CSS.axis, width: 1 } },
    axisTick: { show: false },
    splitLine: { show: false },  // no gridlines: minimal canvas
    axisLabel: { color: CSS.muted, fontSize: 12.5, formatter: axisFormatter(key) },
  };
}

function render() {
  const xk = state.x;
  const yk = state.y;
  const shown = visibleRuns();
  combos = buildCombos(shown);
  const front = paretoCombos(combos, xk, yk)
    .sort((a, b) => a.mean[xk] - b.mean[xk]);

  plotted = [];
  const runPts = [];
  let hidden = 0;
  state.runs.forEach((r, i) => {
    if (!shown.includes(r)) {
      plotted.push(-1);
      return;
    }
    const x = metricValue(r, xk);
    const y = metricValue(r, yk);
    if (x === null || y === null) {
      hidden += 1;
      plotted.push(-1);
      return;
    }
    plotted.push(runPts.length);
    runPts.push({ value: [x, y], run: r, runIndex: i });
  });

  // Front points sorted by "goodness" along x (all axes are inverted so
  // up/right = better, sort by raw value then let the axis flip visually)
  const sortedFront = [...front].sort((a, b) => a.mean[xk] - b.mean[xk]);
  // Smooth curve through the front points: monotone cubic interpolation
  // (Fritsch-Carlson) — passes exactly through every point, no overshoot
  // wiggles between sparse points, unlike a polynomial fit.
  const envelope = monotoneCurve(
    sortedFront.map((c) => c.mean[xk]),
    sortedFront.map((c) => c.mean[yk]),
  );
  const frontPts = sortedFront.map((c) => ({
    value: [c.mean[xk], c.mean[yk]],
    combo: c,
  }));

  const sel = state.selected >= 0 ? state.runs[state.selected] : null;
  const selX = sel ? metricValue(sel, xk) : null;
  const selY = sel ? metricValue(sel, yk) : null;
  const selPts = selX !== null && selY !== null
    ? [{ value: [selX, selY], run: sel }]
    : [];

  chart.setOption({
    ...baseOption(),
    xAxis: axisCommon(xk),
    yAxis: axisCommon(yk),
    series: [
      {
        id: "envelope",
        name: "前沿包络线",
        type: "line",
        step: "end",
        symbol: "none",
        silent: true,
        lineStyle: { color: CSS.front, width: 2, opacity: 0.75 },
        itemStyle: { color: CSS.front },
        data: envelope,
        z: 2,
      },
      {
        id: "runs",
        name: "全部 run",
        type: "scatter",
        symbolSize: 9,
        itemStyle: {
          color: CSS.runs,
          opacity: 0.8,
          borderColor: CSS.surface,
          borderWidth: 2,
        },
        emphasis: {
          scale: 1.7,
          itemStyle: { opacity: 1, borderColor: CSS.surface, borderWidth: 2 },
        },
        tooltip: { formatter: (p) => runTooltip(p.data.run) },
        data: runPts,
        z: 3,
      },
      {
        id: "front",
        name: "前沿组合均值",
        type: "scatter",
        symbolSize: 17,
        itemStyle: {
          color: CSS.front,
          borderColor: CSS.surface,
          borderWidth: 2,
        },
        emphasis: { scale: 1.25 },
        tooltip: { formatter: (p) => frontTooltip(p.data.combo, xk, yk) },
        data: frontPts,
        z: 5,
      },
      {
        // Enclosing circle for the hovered combo.  A scatter symbol is used
        // instead of a `graphic` element because graphic children are laid out
        // by their bounding box, not by absolute pixel coordinates — a symbol
        // sits exactly on the data point and sizes in pixels by construction.
        id: "ring",
        name: "组合分布包络圈",
        type: "scatter",
        symbol: "circle",
        silent: true,
        itemStyle: {
          color: "transparent",
          borderColor: CSS.front,
          borderWidth: 1.5,
          borderType: "dashed",
          opacity: 0.85,
        },
        data: [],
        z: 4,
      },
      {
        id: "spread",
        name: "组合内原始 run",
        type: "scatter",
        symbolSize: 7,
        silent: true,
        itemStyle: {
          color: CSS.front,
          opacity: 0.95,
          borderColor: CSS.surface,
          borderWidth: 1.5,
        },
        data: [],
        z: 6,
      },
      {
        id: "selected",
        name: "选中 run",
        type: "scatter",
        symbolSize: 15,
        itemStyle: {
          color: CSS.select,
          borderColor: CSS.surface,
          borderWidth: 2,
        },
        label: {
          show: true,
          position: "top",
          distance: 6,
          color: CSS.ink,
          fontSize: 11.5,
          fontWeight: 600,
          formatter: () => (sel ? sel.time : ""),
        },
        emphasis: { scale: 1.2 },
        data: selPts,
        z: 7,
      },
    ],
  }, { notMerge: true });

  hideSpread();

  $("chart-note").textContent =
    `run ${shown.length} 个 · 组合 ${combos.length} 个 · 前沿组合 ${front.length} 个` +
    (hidden ? ` · 当前坐标轴缺少数值隐藏 ${hidden} 个` : "");
  $("chart-foot").textContent =
    "每个点是一个 run；橙点为按相同参数聚合后的组合均值，连线为 Pareto 包络线。" +
    "悬停橙点可查看该组合内原始 run 的分布。";
}

// ── hover spread (enclosing circle + raw run dots) ──────────────────────

function toPixel(x, y) {
  try {
    return chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [x, y]);
  } catch (e) {
    return null;
  }
}

function showSpread(combo) {
  const xk = state.x;
  const yk = state.y;
  const pts = [];
  for (const r of combo.runs) {
    const x = metricValue(r, xk);
    const y = metricValue(r, yk);
    if (x === null || y === null) continue;
    pts.push({ value: [x, y], run: r });
  }
  if (!pts.length) return;

  // Radius = the combo's runs' max distance to the mean, measured in axis
  // units and converted to pixels so the circle truly encloses them.
  const center = toPixel(combo.mean[xk], combo.mean[yk]);
  let radius = 0;
  if (center) {
    for (const p of pts) {
      const px = toPixel(p.value[0], p.value[1]);
      if (!px) continue;
      radius = Math.max(radius, Math.hypot(px[0] - center[0], px[1] - center[1]));
    }
  }
  const diameter = 2 * Math.max(radius + 5, 11);

  chart.setOption({
    series: [
      {
        id: "ring",
        symbolSize: diameter,
        data: [{ value: [combo.mean[xk], combo.mean[yk]] }],
      },
      { id: "spread", data: pts },
    ],
  });
}

function hideSpread() {
  chart.setOption({ series: [{ id: "ring", data: [] }, { id: "spread", data: [] }] });
}

// ── selection ───────────────────────────────────────────────────────────

function selectRun(i, { scroll = false } = {}) {
  state.selected = i;
  const run = state.runs[i];
  const idx = plotted[i];

  document.querySelectorAll("#runs-body tr").forEach((tr) => {
    tr.classList.toggle("selected", Number(tr.dataset.index) === i);
  });

  render();

  if (idx >= 0) {
    chart.dispatchAction({ type: "highlight", seriesIndex: S.RUNS, dataIndex: idx });
    chart.dispatchAction({ type: "showTip", seriesIndex: S.RUNS, dataIndex: idx });
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => {
      chart.dispatchAction({ type: "downplay", seriesIndex: S.RUNS, dataIndex: idx });
    }, 1600);
  } else {
    $("side-foot").textContent =
      `${run.time} 在当前坐标轴上缺少数值，无法在图中定位。`;
  }

  const tr = document.querySelector(`#runs-body tr[data-index="${i}"]`);
  if (tr) {
    tr.classList.remove("flash");
    void tr.offsetWidth;   // restart the animation
    tr.classList.add("flash");
    if (scroll) tr.scrollIntoView({ block: "nearest" });
  }
}

// ── side panel ──────────────────────────────────────────────────────────

function paramSummary(params) {
  return Object.keys(params || {}).sort()
    .map((k) => `${abbrOf(k)}=${params[k]}`)
    .join(" ");
}

function renderTable() {
  const q = $("filter").value.trim().toLowerCase();
  const body = $("runs-body");
  const rows = [];
  let shown = 0;
  state.runs.forEach((run, i) => {
    if (!visibleRuns().includes(run)) return;  // group filter
    const params = paramSummary(run.params);
    const hay = (params + " " + run.time + " " + run.trace).toLowerCase();
    if (q && !hay.includes(q)) return;
    shown += 1;
    const hidden = plotted[i] === -1;
    const frontRow = state.serverFront.has(comboKey(run.params));
    rows.push(
      `<tr data-index="${i}" class="${hidden ? "dim" : ""}${frontRow ? " front-row" : ""}"` +
      `${hidden ? ' title="该 run 在所选坐标轴上缺少数值"' : ""}>` +
      `<td class="c-time">${esc(run.time)}</td>` +
      `<td class="c-params" title="${esc(params)}">${esc(params)}</td>` +
      `<td class="c-num">${fmtMetric(run, "fps_avg")}</td>` +
      `<td class="c-num">${run.power_avg_mw === null ? "—" : fmtNum(run.power_avg_mw, 0)}</td>` +
      `</tr>`
    );
  });
  body.innerHTML = rows.join("");
  $("run-count").textContent = q
    ? `${shown} / ${state.runs.length} 个`
    : `${state.runs.length} 个`;
  body.querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("click", () => selectRun(Number(tr.dataset.index), { scroll: false }));
  });
}

// ── data loading ────────────────────────────────────────────────────────

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).error || ""; } catch (e) { /* not json */ }
    throw new Error(`${url} -> HTTP ${res.status} ${detail}`);
  }
  return res.json();
}

function setBusy(busy) {
  $("rescan").disabled = busy;
  if (!state.loaded) {
    $("loading").classList.toggle("hidden", !busy);
  } else {
    // Refetch keeps the previous render at reduced opacity (no skeleton flash).
    $("main").classList.toggle("refreshing", busy);
  }
}

function showError(err) {
  $("loading").classList.remove("hidden");
  $("loading-title").textContent = "加载失败";
  $("loading-sub").textContent = String(err && err.message ? err.message : err);
}

async function load({ initial = false } = {}) {
  setBusy(true);
  try {
    const [ds, runs, pareto] = await Promise.all([
      getJSON("/api/datasets"),
      getJSON("/api/runs"),
      getJSON("/api/pareto").catch(() => ({ front: [], combos: [] })),
    ]);

    $("ds-root").textContent = ds.root || "—";
    $("ds-root").title = ds.root || "";
    $("ds-dirs").innerHTML = (ds.dirs || [])
      .map((d) => `<span class="chip">${esc(d)}</span>`).join("");

    state.runs = runs.runs || [];
    state.abbr = runs.abbr || {};
    state.serverFront = new Set(
      (pareto.front || []).map((c) => comboKey(c.params))
    );
    state.loaded = true;
    state.selected = -1;

    // Group selector: distinct replay groups present in the data
    const groups = [...new Set(state.runs.map(groupOf))].sort();
    const sel = $("group-sel");
    sel.innerHTML =
      `<option value="all">全部运行组</option>` +
      groups.map((g) => `<option value="${g}">运行组 ${g}</option>`).join("");
    // default to group 0 unless absent
    if (!groups.includes(state.group)) state.group = groups[0] || "all";
    sel.value = state.group;

    $("loading").classList.add("hidden");
    $("side-foot").textContent =
      "点击某行可高亮散点图中对应的 run。" +
      (runs.skipped ? ` 已跳过 ${runs.skipped} 条无数据/缺失的 trace。` : "");

    render();
    renderTable();
  } catch (err) {
    showError(err);
  } finally {
    setBusy(false);
  }
}

// ── boot ────────────────────────────────────────────────────────────────

function initControls() {
  for (const [id, def] of [["x-axis", "fps_avg"], ["y-axis", "power_avg_mw"]]) {
    const sel = $(id);
    sel.innerHTML = AXES
      .map((k) => `<option value="${k}">${esc(METRICS[k].label)}</option>`)
      .join("");
    sel.value = def;
  }
  state.x = $("x-axis").value;
  state.y = $("y-axis").value;
  $("x-axis").addEventListener("change", (e) => {
    state.x = e.target.value;
    render();
    renderTable();
  });
  $("y-axis").addEventListener("change", (e) => {
    state.y = e.target.value;
    render();
    renderTable();
  });
  $("group-sel").addEventListener("change", (e) => {
    state.group = e.target.value;
    state.selected = -1;
    render();
    renderTable();
  });
  $("rescan").addEventListener("click", () => load());
  $("filter").addEventListener("input", renderTable);
}

function initChart() {
  chart = echarts.init($("chart"), null, { renderer: "canvas" });

  chart.on("mouseover", { seriesIndex: S.FRONT }, (p) => {
    if (p.data && p.data.combo) showSpread(p.data.combo);
  });
  chart.on("mouseout", { seriesIndex: S.FRONT }, () => hideSpread());
  chart.on("click", { seriesIndex: S.RUNS }, (p) => {
    if (p.data && typeof p.data.runIndex === "number") {
      selectRun(p.data.runIndex, { scroll: true });
    }
  });
  chart.getZr().on("globalout", () => hideSpread());

  window.addEventListener("resize", () => {
    chart.resize();
    hideSpread();
  });
}

if (typeof echarts === "undefined") {
  showError("echarts.min.js 未加载：请确认 src/webui/static/echarts.min.js 存在。");
} else {
  initControls();
  initChart();
  load({ initial: true });
}
