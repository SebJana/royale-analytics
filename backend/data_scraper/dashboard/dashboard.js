"use strict";

// index.html is the scraper page, api.html the API page; both load this script.
const PAGE = document.body.dataset.page === "api" ? "api" : "scraper";

const RANGES = {
  "1h": 3600,
  "3h": 10800,
  "6h": 21600,
  "24h": 86400,
  "3d": 259200,
  "7d": 604800,
};
// NOTE Matches METRICS_INTERVAL in settings.py; three missed snapshots
// mean the scraper stopped publishing.
const SNAPSHOT_INTERVAL_S = 10;
const REFRESH_MS = 30000;
const CHART_HEIGHT = 150;
const THEME_KEY = "scraper-dashboard:theme";

function num(v) {
  if (v == null) return "–";
  if (Math.abs(v) >= 100) return Math.round(v).toLocaleString();
  return String(Number(v.toFixed(2)));
}
// Per-minute rates divide integer counts by the real sample span, so they are
// rarely whole. Small rates keep one decimal to stay visible on long ranges.
function rate(v) {
  if (v == null) return "–";
  if (Math.abs(v) >= 10) return Math.round(v).toLocaleString();
  return String(Number(v.toFixed(1)));
}
function dur(s) {
  if (s == null) return "–";
  if (s === 0) return "0";
  if (s < 1) return Math.round(s * 1000) + "ms";
  if (s < 10) return (Number.isInteger(s) ? s : s.toFixed(1)) + "s";
  if (s < 120) return Math.round(s) + "s";
  if (s < 7200) return Math.round(s / 60) + "m";
  if (s < 172800) return (s / 3600).toFixed(1) + "h";
  return (s / 86400).toFixed(1) + "d";
}
// API responses take milliseconds, which dur would round to "0ms".
function ms(s) {
  if (s == null) return "–";
  if (s === 0) return "0";
  const m = s * 1000;
  if (m < 10) return m.toFixed(1) + "ms";
  if (m < 1000) return Math.round(m) + "ms";
  return dur(s);
}
// An API percentile past the histogram's top bound reads only that bound, so
// it shows as a lower limit (p95Over and friends, see _api_summary).
const apiPct = (r, q) => (r[`p${q}Over`] ? "≥ " : "") + ms(r[`p${q}S`]);
// dur keeps a bare "0" for chart axes; elapsed time reads "0s" instead.
const age = (s) => (s < 1 ? "0s" : dur(s));
const mb = (b) => (b == null ? "–" : (b / 1048576).toFixed(1) + " MB");
const pct = (v) => (v == null ? "–" : `${Number(v.toFixed(1))}%`);

const perMin = (count, s) =>
  s.spanS > 0 ? ((count || 0) * 60) / s.spanS : null;
const outcome =
  (field, ...keys) =>
  (s) =>
    perMin(
      keys.reduce((sum, k) => sum + ((s[field] || {})[k] || 0), 0),
      s,
    );
// p50 recedes, p95 carries the accent, p99 is dashed for the tail.
const percentiles = (name) => [
  ["p50", "--c2", (s) => s[`battleP50${name}`]],
  ["p95", "--c1", (s) => s[`battleP95${name}`]],
  ["p99", "--c3", (s) => s[`battleP99${name}`], { dash: [4, 3] }],
];

// Sections of charts. Each chart: title, value format,
// [label, color variable, getter, extra uPlot series options] per series,
// and optionally { log: true } for a logarithmic value axis.
const SECTIONS = [
  [
    "Throughput",
    [
      [
        "Battle syncs / min",
        rate,
        [["Synced", "--c1", outcome("battleOutcomes", "synced")]],
      ],
      [
        "Battles inserted / min",
        rate,
        [["Inserted", "--c1", (s) => perMin(s.battlesInserted, s)]],
      ],
      [
        "Profile refreshes / min",
        rate,
        [
          ["Synced", "--c1", outcome("profileOutcomes", "synced")],
          [
            "Failed",
            "--c3",
            outcome("profileOutcomes", "failed", "busy", "maintenance"),
          ],
        ],
      ],
    ],
  ],
  [
    "Latency",
    [
      ["Battle sync delay", dur, percentiles("ClaimLatenessS")],
      ["Battle job duration", dur, percentiles("DurationS")],
      [
        "Oldest due player, waiting",
        dur,
        [
          ["Battles", "--c1", (s) => s.battlesOldestDueS],
          ["Profiles", "--c2", (s) => s.profilesOldestDueS],
        ],
      ],
    ],
  ],
  [
    "Health",
    [
      [
        "Battle sync problems / min",
        rate,
        [
          ["No key", "--c2", outcome("battleOutcomes", "busy")],
          ["Failed", "--c3", outcome("battleOutcomes", "failed")],
          ["Not found", "--c1", outcome("battleOutcomes", "not_found")],
          ["Maintenance", "--c4", outcome("battleOutcomes", "maintenance")],
        ],
      ],
      ["Possible battle gaps", num, [["Gaps", "--c4", (s) => s.possibleGaps]]],
      [
        "Due players",
        num,
        [
          ["Battles", "--c1", (s) => s.battlesDue],
          ["Profiles", "--c2", (s) => s.profilesDue],
        ],
      ],
    ],
  ],
  [
    "Database",
    [
      ["Battles on record", num, [["Battles", "--c1", (s) => s.battlesTotal]]],
      [
        "Mongo storage",
        mb,
        [
          ["On disk", "--c1", (s) => s.mongoTotalSize],
          ["Indexes", "--c2", (s) => s.mongoIndexSize],
          ["Uncompressed", "--c3", (s) => s.mongoDataSize, { dash: [4, 3] }],
        ],
      ],
      [
        "Mongo response time",
        dur,
        [
          [
            "Ping",
            "--c1",
            (s) => (s.mongoPingMs == null ? null : s.mongoPingMs / 1000),
          ],
        ],
      ],
      // Own chart: a 0/1 series next to the connection count would
      // stay a flat line at the bottom of the scale.
      [
        "Mongo unreachable",
        num,
        [["Unreachable", "--c4", (s) => s.mongoUnreachable]],
      ],
      ["Mongo connections", num, [["Open", "--c2", (s) => s.mongoConnections]]],
    ],
  ],
  [
    "Capacity",
    [
      [
        "Request budget (req/s)",
        num,
        [
          ["Planned", "--c2", (s) => s.requestRate],
          ["Battles", "--c1", (s) => s.battleRate],
          ["Needed", "--c3", (s) => s.battleDemand],
        ],
      ],
      [
        "Tracked players",
        num,
        [
          ["Tracked", "--c1", (s) => s.activePlayers],
          ["Max", "--c2", (s) => s.maxPlayers],
        ],
      ],
      [
        "Base sync interval",
        dur,
        [["Interval", "--c1", (s) => s.baseIntervalS]],
      ],
    ],
  ],
  [
    "System",
    [
      [
        "Keys and workers",
        num,
        [
          ["Usable keys", "--c1", (s) => s.keysUsable],
          ["Invalid keys", "--c3", (s) => s.keysInvalid],
          ["Workers", "--c2", (s) => s.workers],
        ],
      ],
      ["Key store memory", mb, [["Used", "--c1", (s) => s.redisMemory]]],
    ],
  ],
];

// The API's route timings (from /api-history), of the selected route, one
// route group, or all routes together. Titles name the scope (null for a
// single route), so a screenshot keeps it.
const scoped = (title, scope) => (scope ? `${title}, ${scope}` : title);
const apiCharts = (scope) => [
  [
    "Requests / min",
    rate,
    [
      ["Requests", "--c1", (s) => perMin(s.requests, s)],
      ["4xx", "--c3", (s) => perMin(s.e4, s)],
      ["5xx", "--c4", (s) => perMin(s.e5, s)],
    ],
  ],
  // Logarithmic: response times span from under a millisecond to seconds,
  // and one slow request would flatten every faster line at the bottom.
  [
    scoped("Response time", scope),
    ms,
    [
      ["p50", "--c2", (s) => s.p50S],
      ["p95", "--c1", (s) => s.p95S],
      ["p99", "--c3", (s) => s.p99S, { dash: [4, 3] }],
    ],
    { log: true },
  ],
  // Share of the requests, not their count, so a busy minute with few
  // errors does not look worse than a quiet one with many. A bucket without
  // requests is a gap. 4xx includes expected answers such as a wrong CAPTCHA
  // (401) or a player without battles in the range (404).
  [
    scoped("Error rate", scope),
    pct,
    [
      ["4xx", "--c3", (s) => (s.requests ? (s.e4 * 100) / s.requests : null)],
      ["5xx", "--c4", (s) => (s.requests ? (s.e5 * 100) / s.requests : null)],
    ],
  ],
  // Without lookups in a bucket the rate is a gap, not 0%: a quiet minute
  // or a route without a response cache did not miss anything.
  [
    scoped("Cache hit rate", scope),
    pct,
    [
      [
        "Hits",
        "--c2",
        (s) => {
          const lookups = (s.cacheHits || 0) + (s.cacheMisses || 0);
          return lookups ? (s.cacheHits * 100) / lookups : null;
        },
      ],
    ],
    { max: 100 },
  ],
];
const ROUTE_COLORS = ["--c1", "--c3", "--c4", "--c2"];
// "GET /api/players/{player_tag}/battles" reads "/players/{player_tag}/battles";
// other methods keep their name.
const shortRoute = (route) =>
  route.replace(/^GET /, "").replace(/^(\w+ )?\/api\//, "$1/");

// One p95 line per busiest route; the server sends their values per bucket.
function busiestRoutesChart(api) {
  const routes = groupRoutes(api, apiGroup)
    .slice(0, ROUTE_COLORS.length)
    .map((r) => r.route);
  return [
    "p95 of the busiest routes",
    ms,
    routes.map((route, i) => [
      shortRoute(route),
      ROUTE_COLORS[i],
      (s) => (s.routeP95S || {})[route] ?? null,
    ]),
    { log: true },
  ];
}
const API_COLUMNS = [
  ["Route", (r) => r.route],
  ["Requests", (r) => num(r.requests)],
  ["p50", (r) => apiPct(r, 50)],
  ["p95", (r) => apiPct(r, 95)],
  ["p99", (r) => apiPct(r, 99)],
  ["Max", (r) => ms(r.maxS)],
  ["4xx", (r) => num(r.e4)],
  ["5xx", (r) => num(r.e5)],
  ["Error rate", (r) => share(r.e4 + r.e5, r.requests)],
  // Routes without a response cache never look one up and show a dash.
  ["Cache hits", (r) => share(r.cacheHits, r.cacheHits + r.cacheMisses)],
];

const ICONS = {
  auto: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="2" y="3" width="12" height="8.5" rx="1.5"/><path d="M5.5 14h5M8 11.5V14"/></svg>',
  light:
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1"/></svg>',
  dark: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7z"/></svg>',
};

let range = RANGES[location.hash.slice(1)] || 21600;
let status = null;
let lastHistory = null;
let lastApiHistory = null;
// The API page's selected route, null for all; kept in the URL (?route=), so
// a reload or a shared link opens the same route.
let apiRoute = new URLSearchParams(location.search).get("route");
// Route groups the server charts on their own; the auth flow apart from
// regular traffic, so an attack on one shows separately.
// NOTE Matches API_ROUTE_GROUPS and route_group in src/metrics.py.
const API_GROUPS = [
  ["", "All", "all routes"],
  ["regular", "Regular", "regular traffic"],
  ["auth", "Auth flow", "auth flow"],
];
// The selected group, "" for all; kept in the URL (?group=) like the route.
let apiGroup = new URLSearchParams(location.search).get("group") || "";
if (!API_GROUPS.some(([value]) => value === apiGroup)) apiGroup = "";
const groupScope = (group) => API_GROUPS.find(([v]) => v === group)[2];
// Routes of one group, all for "". Rows from before groups count as regular.
const groupRoutes = (api, group) =>
  api.routes.filter((r) => !group || (r.group || "regular") === group);
let plots = [];
// A width read while the charts are rebuilt can be stale. Every newly
// observed card reports its final size once, which corrects that width,
// and later reports follow window resizes.
const resizer = new ResizeObserver((entries) => {
  for (const { target } of entries) {
    const plot = plots.find((p) => p.root.parentElement === target);
    if (plot)
      plot.setSize({
        width: target.clientWidth - 32,
        height: CHART_HEIGHT,
      });
  }
});
let loadedAt = null;
let refreshSeq = 0;
const $ = (id) => document.getElementById(id);
const css = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const node = (tag, props = {}, ...children) => {
  const el = Object.assign(document.createElement(tag), props);
  el.append(...children);
  return el;
};

function toColumns(history, getters) {
  // A null row breaks the lines where the scraper wrote no samples.
  const xs = [];
  const cols = getters.map(() => []);
  let prev = null;
  for (const s of history.samples) {
    if (prev !== null && s.t - prev > 2.5 * history.bucketS) {
      xs.push(prev + history.bucketS);
      cols.forEach((c) => c.push(null));
    }
    xs.push(s.t);
    getters.forEach((get, i) => cols[i].push(get(s)));
    prev = s.t;
  }
  return [xs, ...cols];
}

const lastValue = (col) => {
  for (let i = col.length - 1; i >= 0; i--) if (col[i] != null) return col[i];
  return null;
};
const timeLabel = (t) =>
  new Date(t * 1000).toLocaleString([], {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
// One line per tick: clock time within a day, weekday and date beyond.
const axisTime = (t, rangeS) => {
  const d = new Date(t * 1000);
  return rangeS <= 86400
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { weekday: "short", day: "numeric" });
};

// Shows the latest values, or the hovered point's values with its time.
function readout(el, timeEl, series, fmt, data, idx) {
  const items = series.map(([label, color, , extra], i) => {
    const value = idx == null ? lastValue(data[i + 1]) : data[i + 1][idx];
    const key = node("i", {
      className: extra && extra.dash ? "dashed" : "",
    });
    key.style.background = css(color);
    key.style.setProperty("--key", css(color));
    return node("span", {}, key, label, node("b", { textContent: fmt(value) }));
  });
  el.replaceChildren(...items);
  timeEl.textContent = idx == null ? "latest" : timeLabel(data[0][idx]);
}

// 1ms, 10ms, 100ms, ... within a log axis's range.
function decades(u, axisIdx, min, max) {
  const splits = [];
  for (
    let e = Math.floor(Math.log10(min));
    e <= Math.ceil(Math.log10(max));
    e++
  )
    if (10 ** e >= min && 10 ** e <= max) splits.push(10 ** e);
  return splits;
}

function chart(parent, [title, fmt, series, options = {}], history) {
  const values = node("div", { className: "readout" });
  const time = node("span", { className: "time" });
  const card = node(
    "article",
    { className: "card" },
    node("h2", { textContent: title }),
    time,
    values,
  );
  parent.append(card);
  const data = toColumns(
    history,
    series.map((s) => s[2]),
  );
  // A log axis has no zero; such a point becomes a gap instead.
  if (options.log)
    for (const col of data.slice(1))
      col.forEach((v, i) => {
        if (v != null && v <= 0) col[i] = null;
      });
  const axis = {
    stroke: css("--muted"),
    font: "11px Inter, system-ui, sans-serif",
    grid: { show: false },
    ticks: { show: false },
    gap: 6,
  };
  const opts = {
    width: card.clientWidth - 32,
    height: CHART_HEIGHT,
    legend: { show: false },
    cursor: { points: { size: 6, width: 0 }, y: false },
    scales: {
      // An array range pins the axis to the selected window. min/max
      // alone are overwritten by autoscaling to the first and last sample.
      x: { range: [history.now - history.rangeS, history.now] },
      y: options.log
        ? { distr: 3, log: 10 }
        : options.max != null
          ? { range: [0, options.max] }
          : { range: (u, min, max) => [0, max > 0 ? max * 1.15 : 1] },
    },
    axes: [
      {
        ...axis,
        space: 80,
        values: (u, splits) => splits.map((t) => axisTime(t, history.rangeS)),
      },
      {
        ...axis,
        size: 52,
        space: 36,
        grid: { stroke: css("--grid"), width: 1 },
        values: (u, vals) => vals.map(fmt),
        // Powers of ten only: uPlot's default also lines 2..9 of each decade,
        // a dense grid that hides the data. Every one keeps its label, which
        // uPlot's log filter would otherwise blank on alternate lines.
        ...(options.log && { splits: decades, filter: (u, splits) => splits }),
      },
    ],
    series: [
      {},
      ...series.map(([label, color, , extra], i) => ({
        label,
        stroke: css(color),
        width: 1.5,
        points: { show: false },
        // Only a lone series gets a faint wash, so overlaps stay readable.
        fill: series.length === 1 ? css(color) + "14" : undefined,
        ...extra,
      })),
    ],
    hooks: {
      setCursor: [
        (u) => readout(values, time, series, fmt, data, u.cursor.idx),
      ],
    },
  };
  plots.push(new uPlot(opts, data, card));
  resizer.observe(card);
  readout(values, time, series, fmt, data, null);
}

// Rebuilds the current page's charts from the last loaded data.
function renderCharts() {
  resizer.disconnect();
  plots.forEach((p) => p.destroy());
  plots = [];
  const root = $("sections");
  root.replaceChildren();
  if (PAGE === "api") {
    if (lastApiHistory) renderApi(root, lastApiHistory);
    return;
  }
  if (!lastHistory) return;
  for (const [heading, charts] of SECTIONS) {
    const grid = node("div", { className: "grid" });
    root.append(
      node("section", {}, node("h3", { textContent: heading }), grid),
    );
    charts.forEach((spec) => chart(grid, spec, lastHistory));
  }
}

// Charts of all routes together, then every route over the whole range,
// most requested first.
function renderApi(root, api) {
  const grid = node("div", { className: "grid" });
  root.append(
    node(
      "section",
      { id: "route-charts" },
      node("div", { className: "section-head" }, groupPicker(), routePicker(api)),
      grid,
    ),
  );
  apiCharts(apiRoute ? null : groupScope(apiGroup))
    // The auth flow has no response cache, so its hit rate chart stays empty.
    .filter(([title]) => !(apiGroup === "auth" && !apiRoute && title.startsWith("Cache")))
    .forEach((spec) => chart(grid, spec, api));
  // Comparing routes only makes sense while a whole group is shown.
  if (!apiRoute && groupRoutes(api, apiGroup).length)
    chart(grid, busiestRoutesChart(api), api);
  const routes = node("div", { className: "grid" });
  root.append(
    node("section", {}, node("h3", { textContent: "Per route" }), routes),
  );
  // One table per group, whatever is charted: regular traffic and the auth
  // flow side by side.
  for (const [group, label] of API_GROUPS.slice(1))
    routes.append(routeTable(`${label} routes`, groupRoutes(api, group)));
}

function routeTable(title, rows) {
  const card = node(
    "article",
    { className: "card routes" },
    node("h2", { textContent: title }),
    node("p", {
      className: "hint",
      textContent: "Select a route to chart it on its own.",
    }),
  );
  if (!rows.length) {
    card.append(
      node("p", {
        className: "empty",
        textContent: "No requests in this range.",
      }),
    );
    return card;
  }
  const head = node(
    "tr",
    {},
    ...API_COLUMNS.map(([label]) => node("th", { textContent: label })),
  );
  const cells = rows.map((r) => {
    const selected = r.route === apiRoute;
    // The route name is a button: the row reads as a row, the name as the
    // control, and the keyboard reaches it. A second click deselects.
    const pick = node("button", {
      type: "button",
      textContent: r.route,
      title: selected ? "Show the whole group" : "Chart this route",
      onclick: () => selectRoute(selected ? null : r.route),
    });
    pick.setAttribute("aria-pressed", String(selected));
    return node(
      "tr",
      { className: selected ? "selected" : "" },
      node("td", {}, pick),
      ...API_COLUMNS.slice(1).map(([, value]) =>
        node("td", { textContent: value(r) }),
      ),
    );
  });
  card.append(
    node(
      "div",
      { className: "table" },
      node("table", {}, node("thead", {}, head), node("tbody", {}, ...cells)),
    ),
  );
  return card;
}

// All routes, regular traffic or the auth flow; picking a group shows its
// totals instead of a single route's.
function groupPicker() {
  const picker = node("div", { className: "segmented" });
  picker.setAttribute("role", "group");
  picker.setAttribute("aria-label", "Route group");
  for (const [value, label] of API_GROUPS) {
    const button = node("button", { type: "button", textContent: label });
    button.setAttribute("aria-pressed", String(!apiRoute && value === apiGroup));
    button.onclick = () => selectGroup(value);
    picker.append(button);
  }
  return picker;
}

function selectGroup(group) {
  apiGroup = group;
  apiRoute = null;
  const url = new URL(location.href);
  url.searchParams.delete("route");
  if (group) url.searchParams.set("group", group);
  else url.searchParams.delete("group");
  history.replaceState(null, "", url);
  refresh();
}

// The whole group first, then the group's routes of the range by requests; a
// selected route without requests in this range still stays selectable.
function routePicker(api) {
  const names = groupRoutes(api, apiGroup).map((r) => r.route);
  if (apiRoute && !names.includes(apiRoute)) names.unshift(apiRoute);
  const label = API_GROUPS.find(([v]) => v === apiGroup)[1].toLowerCase();
  const select = node(
    "select",
    { id: "route", onchange: () => selectRoute(select.value || null) },
    node("option", {
      value: "",
      textContent: apiGroup ? `All ${label} routes` : "All routes",
    }),
    ...names.map((name) => node("option", { value: name, textContent: name })),
  );
  select.value = apiRoute || "";
  return node(
    "label",
    { className: "route-picker" },
    node("span", { textContent: "Route" }),
    select,
  );
}

function selectRoute(route) {
  apiRoute = route;
  const url = new URL(location.href);
  if (route) url.searchParams.set("route", route);
  else url.searchParams.delete("route");
  history.replaceState(null, "", url);
  // The table sits below the charts; a pick there would change charts out of
  // view.
  const charts = $("route-charts");
  if (charts && charts.getBoundingClientRect().top < 0)
    charts.scrollIntoView({ behavior: "smooth" });
  refresh();
}

// The API page has no snapshot: its samples show whether the API writes.
function apiState() {
  const samples = lastApiHistory ? lastApiHistory.samples : [];
  if (!lastApiHistory) return ["Loading", "--muted"];
  if (!samples.length) return ["No samples in range", "--warn"];
  const quiet = Date.now() / 1000 - samples[samples.length - 1].t;
  // Three missed samples, like the scraper's snapshots
  return quiet > 3 * lastApiHistory.intervalS
    ? [`Not reporting · last sample ${age(quiet)} ago`, "--bad"]
    : ["Running", "--ok"];
}

function renderStatus() {
  const el = $("status");
  if (PAGE === "api") {
    const [text, color] = apiState();
    el.style.setProperty("--dot", css(color));
    el.lastChild.textContent = text;
    showUpdated();
    return;
  }
  let [text, color] = ["Running", "--ok"];
  if (!status || !status.updatedAt)
    [text, color] = ["No snapshot yet", "--warn"];
  else if (Date.now() / 1000 - status.updatedAt > 3 * SNAPSHOT_INTERVAL_S)
    [text, color] = ["Not reporting", "--bad"];
  else if (status.maintenance) [text, color] = ["API maintenance", "--warn"];
  else if (status.keys.usable === 0)
    [text, color] = ["No usable keys", "--bad"];
  else if (status.mongo && !status.mongo.ok)
    [text, color] = ["Mongo unreachable", "--bad"];
  el.style.setProperty("--dot", css(color));
  el.lastChild.textContent =
    status && status.startedAt
      ? `${text} · up ${age(Date.now() / 1000 - status.startedAt)}`
      : text;
  showUpdated();
}

function showUpdated() {
  if (loadedAt)
    $("updated").textContent =
      `Updated ${age(Math.round((Date.now() - loadedAt) / 1000))} ago`;
}

// Snapshots from a scraper without the Mongo health check have no mongo.
function mongoKpi(m) {
  if (!m) return ["Mongo", "–", "no data"];
  if (!m.ok) return ["Mongo", "–", "unreachable"];
  const disk = m.fsTotalSize
    ? ` · disk ${Math.round((m.fsUsedSize / m.fsTotalSize) * 100)}% full`
    : "";
  return ["Mongo", mb(m.totalSize), `ping ${dur(m.pingMs / 1000)}${disk}`];
}

function renderKpis() {
  const s = status;
  const cap = s.capacity || {};
  const b = s.jobs.battles;
  const synced = b.jobs
    ? Math.round(((b.outcomes.synced || 0) / b.jobs) * 100)
    : 100;
  const perWindowMin = (count) => (count * 60) / s.windowS;
  const kpis = [
    ["Tracked players", num(cap.activePlayers), `of ${num(cap.maxPlayers)}`],
    ["Battle syncs / min", rate(perWindowMin(b.jobs)), `${synced}% successful`],
    ["Battles / min", rate(perWindowMin(b.inserted)), "inserted"],
    [
      "Sync delay p95",
      dur(b.p95ClaimLatenessS),
      `p50 ${dur(b.p50ClaimLatenessS)}`,
    ],
    ["Job duration p95", dur(b.p95DurationS), `p50 ${dur(b.p50DurationS)}`],
    [
      "Due now",
      num(s.schedules.battles.due),
      `oldest ${dur(s.schedules.battles.oldest_due_lateness_s)}`,
    ],
    ["Keys", `${s.keys.usable}/${s.keys.configured}`, `${s.workers} workers`],
    ["Battle gaps", num(s.possibleGaps), "since start"],
    mongoKpi(s.mongo),
    [
      "Key store",
      mb(s.redis.usedMemory),
      s.redis.maxMemory ? `of ${mb(s.redis.maxMemory)}` : "no limit",
    ],
  ];
  showKpis(kpis);
}

const share = (part, whole) =>
  whole ? `${Number(((part / whole) * 100).toFixed(1))}%` : "–";

// Over the selected range, unlike the scraper's tiles, which cover the last
// minute: a minute of a quiet site holds too few requests for a p95.
function renderApiKpis() {
  const api = lastApiHistory;
  const t = api.total;
  const upS = api.samples.reduce((sum, s) => sum + s.spanS, 0);
  // A route seen a handful of times has its slowest request as p95; with at
  // least 20 requests, one slow outlier is not the whole p95.
  // The routes the tiles cover: the selected group's, or all.
  const inScope = groupRoutes(api, apiGroup);
  const steady = inScope.filter((r) => r.requests >= 20);
  const slowest = (steady.length ? steady : inScope).reduce(
    (a, b) => (b.p95S > a.p95S ? b : a),
    { p95S: null },
  );
  const scope = apiRoute ? routeLabel(apiRoute) : groupScope(apiGroup);
  showKpis([
    [
      "Requests / min",
      rate(upS ? (t.requests * 60) / upS : null),
      `${num(t.requests)} in range`,
    ],
    ["p50", apiPct(t, 50), scope, apiRoute],
    ["p95", apiPct(t, 95), scope, apiRoute],
    ["p99", apiPct(t, 99), `slowest ${ms(t.maxS)}`],
    ["4xx", share(t.e4, t.requests), `${num(t.e4)} requests`],
    ["5xx", share(t.e5, t.requests), `${num(t.e5)} requests`],
    [
      "Cache hits",
      share(t.cacheHits, t.cacheHits + t.cacheMisses),
      `${num(t.cacheHits + t.cacheMisses)} lookups`,
    ],
    [
      "Slowest route p95",
      apiPct(slowest, 95),
      slowest.route ? routeLabel(slowest.route) : "no requests",
      slowest.route,
    ],
    ["Routes", num(inScope.length), "with requests"],
  ]);
}

// Tiles: [label, value, subtitle, tooltip]. A subtitle is text or the nodes
// routeLabel returns.
function showKpis(kpis) {
  $("kpis").replaceChildren(
    ...kpis.map(([label, value, sub, title = ""]) =>
      node(
        "div",
        { className: "kpi" },
        node("span", { textContent: label }),
        node("b", { textContent: value }),
        node("small", { title }, ...[sub].flat()),
      ),
    ),
  );
}

// A route path has no spaces, so it could not wrap and would run into the
// next tile. <wbr> after each "/" lets it break between segments.
const routeLabel = (route) =>
  shortRoute(route)
    .split(/(?<=\/)/)
    .flatMap((part, i) => (i ? [node("wbr"), part] : [part]));

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

async function refresh() {
  // A range switch can overlap a periodic refresh. Only the latest
  // request renders, so an older range never replaces a newer one.
  const request = ++refreshSeq;
  try {
    if (PAGE === "api") {
      const query = apiRoute
        ? `&route=${encodeURIComponent(apiRoute)}`
        : apiGroup
          ? `&group=${apiGroup}`
          : "";
      const apiHistory = await getJson(`api-history?range=${range}${query}`);
      if (request !== refreshSeq) return;
      lastApiHistory = apiHistory;
      renderApiKpis();
    } else {
      const [snapshot, history] = await Promise.all([
        getJson("status"),
        getJson(`history?range=${range}`),
      ]);
      if (request !== refreshSeq) return;
      status = snapshot;
      lastHistory = history;
      if (status.jobs) renderKpis();
    }
    loadedAt = Date.now();
    $("error").replaceChildren();
    renderCharts();
  } catch (error) {
    $("error").replaceChildren(
      node("div", {
        className: "error",
        textContent: `Could not load data (${error.message}). Showing the last loaded state.`,
      }),
    );
  }
  renderStatus();
}

function applyTheme(choice) {
  const dark =
    choice === "dark" ||
    (choice === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.themeChoice = choice;
  for (const b of $("theme").children)
    b.setAttribute("aria-pressed", String(b.dataset.value === choice));
  // Canvas colors are read when a chart is built, so the charts redraw.
  renderCharts();
  renderStatus();
}

for (const [label, seconds] of Object.entries(RANGES)) {
  const button = node("button", { textContent: label, type: "button" });
  button.setAttribute("aria-pressed", String(seconds === range));
  button.onclick = () => {
    range = seconds;
    history.replaceState(null, "", "#" + label);
    keepRangeInPageLinks();
    for (const b of $("ranges").children)
      b.setAttribute("aria-pressed", String(b === button));
    refresh();
  };
  $("ranges").append(button);
}
for (const value of ["auto", "light", "dark"]) {
  const button = node("button", {
    type: "button",
    title: `Theme: ${value}`,
    innerHTML: ICONS[value],
  });
  button.dataset.value = value;
  button.setAttribute("aria-label", `${value} theme`);
  button.onclick = () => {
    try {
      localStorage.setItem(THEME_KEY, value);
    } catch {}
    applyTheme(value);
  };
  $("theme").append(button);
}
// The other page opens with the same range selected.
function keepRangeInPageLinks() {
  for (const link of document.querySelectorAll("nav.pages a"))
    link.hash = location.hash;
}
keepRangeInPageLinks();
applyTheme(document.documentElement.dataset.themeChoice);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (document.documentElement.dataset.themeChoice === "auto")
    applyTheme("auto");
});
setInterval(refresh, REFRESH_MS);
setInterval(renderStatus, 1000);
refresh();
