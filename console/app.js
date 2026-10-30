// bosun console: hash routing over the views (Ledger list and item page, Sessions and health,
// Stats with its inline-SVG charts, Learning, Knowledge (the Briefing with its review card stack,
// the Constellation on a canvas, the Raw data overview, entity pages, process flows, a
// neighbourhood graph and the review batch), and the Library with a safe Markdown renderer), the
// "Needs you" badge, and polling: /api/version every 15 s refreshes the open view when the
// ledger changes, and the Sessions view reloads every 15 s while it is visible. Design:
// docs/design/console.md. Everything from the API is rendered with textContent (through el()
// below), never innerHTML: ledger text includes untrusted Slack excerpts and PR titles. Only
// http(s) URLs become links. No inline handlers or style attributes (the CSP forbids them). The
// one write is a knowledge review decision (post(), with the review model's CSRF token).
"use strict";

const POLL_MS = 15000;
const SEARCH_DELAY_MS = 250;
const COPIED_MS = 1500;
const METER_STEPS = 10;
const DEFAULT_VIEW = "ledger";
const VIEWS = {
  ledger: "Ledger",
  sessions: "Sessions",
  stats: "Stats",
  impact: "Impact",
  learning: "Improvements",
  knowledge: "Knowledge Graph",
  library: "Config",
};
const ITEM_ROUTE = "item";
const FILTER_KEYS = ["kind", "group", "window", "person", "since", "q", "sort"];
const DEFAULT_GROUP = "active";
const GROUP_CHOICES = [
  ["active", "All open"],
  ["needs-me", "Needs me"],
  ["waiting", "Waiting on someone"],
  ["open", "Open"],
  ["closed", "Closed"],
  ["all", "Everything"],
];
const GROUP_LABELS = { "needs-me": "needs me", waiting: "waiting", open: "open", closed: "closed" };
const SINCE_CHOICES = [
  ["", "Any time"],
  ["24h", "Last 24 hours"],
  ["7d", "Last 7 days"],
  ["30d", "Last 30 days"],
];
const SORT_CHOICES = [
  ["updated", "Updated"],
  ["created", "Created"],
  ["kind", "Kind"],
];
const CLAUDE_STATUSES = ["idle", "busy", "shell", "waiting"];
const SECONDS = { d: 86400, h: 3600, m: 60 };
const STATS_PERIODS = [7, 30, 90]; // /api/stats takes these and nothing else
const SVG_NS = "http://www.w3.org/2000/svg";
const SERIES_COLOURS = 8; // .series-0 ... .series-7 in app.css
// Stacked day charts are drawn at their box's measured width, so text stays at its CSS size.
const CHART = { width: 640, minWidth: 280, height: 210, axis: 24, left: 34, gap: 0.3, headroom: 22, dayLabelMin: 40 };
const BARS = { width: 560, row: 28, label: 200, value: 44, inset: 4, baseline: 0.68 };
const SPARK = { width: 120, height: 28 };
const REVIEW_KEY = "review"; // #/knowledge/review
const REVIEW_API = "/api/knowledge/review";
const CSRF_HEADER = "X-Bosun-CSRF";
const KG_CARDS = ["people", "process", "product", "systems"]; // .axis-<card> in app.css
const KG_STATUS_BADGES = { confirmed: "ok", proposed: "warn", rejected: "error", expired: "" };
const KG_AUTHORITIES = ["user", "stakeholder", "observed", "inferred"]; // .authority-<label>
const GRAPH = { center: 13, node: 8, label: 22, centerLabel: 30 };
const BRIEFING_API = "/api/knowledge/briefing";
const CONSTELLATION_API = "/api/knowledge/constellation";
const KG_TABS = [["briefing", "Briefing"], ["constellation", "Constellation"], ["raw", "Raw data"]]; // #/knowledge?view=<name>
const KG_DEFAULT_TAB = "briefing";
const KG_METRO_NOTE = "A metro-map view of the processes: a prototype only for now (docs/design/prototypes/knowledge-treatments/); on the roadmap.";
const KG_AXIS_TOKENS = { people: "--series-0", process: "--series-2", product: "--series-1", systems: "--series-5" }; // as .axis-<card> in app.css
// The Briefing: new facts listed per timeline day before "Show more", a coverage ring, a mini ego graph's node radii (in its 800x520 picture).
const BRIEF = { dayFacts: 8, ring: { size: 52, stroke: 6 }, mini: { center: 40, node: 26 } };
// The review stack: cards drawn, the drag distances that decide, and how long a decision waits (for Z) before it is sent.
const STACK = { depth: 3, swipe: 110, drop: 90, undoMs: 4000 };
const STACK_KEYS = { y: "confirm", ArrowRight: "confirm", n: "reject", ArrowLeft: "reject", s: "skip", ArrowDown: "skip", z: "undo" };
const STACK_ACTIONS = { confirm: "confirm", reject: "reject", skip: "defer" }; // the card's verb -> the POST's action
// The Constellation's layout and drawing (world units are pixels at zoom 1).
const STAR = {
  seed: 7, settleTicks: 320, minAlpha: 0.003, restAlpha: 0.25, cooling: 0.02, reheat: 0.5, dragAlpha: 0.25,
  anchors: { people: [-440, 30], process: [-60, -210], product: [420, -20], systems: [40, 260] }, spreadNodes: 120, startRadius: 140,
  anchorPull: 0.06, charge: 260, chargeRange: 90000, gap: 9, overlapPush: 0.25, damping: 0.58,
  sameLength: 52, crossLength: 170, sameSpring: 0.22, crossSpring: 0.025,
  baseRadius: 4.5, degreeRadius: 3.1, maxRadius: 18, drift: 1.6, driftSpeed: 0.0005, pulseMs: 1800,
  edge: 0.5, proposedEdge: 0.3, focusEdge: 0.95, dimEdge: 0.05, dimNode: 0.15, proposedNode: 0.4, dash: [3, 4],
  minScale: 0.6, maxScale: 1.8, minZoom: 0.3, maxZoom: 3.5, minFit: 0.35, maxFit: 1.9, fitMargin: 160, focusZoom: 1.25, zoomStep: 1.3, wheel: 0.0016, flyMs: 750,
  hubDegree: 6, labelZoom: 1.35, hitMin: 8, clickSlop: 4, results: 8, panelFacts: 8, panelShift: 180, minWidth: 320, minHeight: 360,
};

let lastStamp = null;
let lastLedgerHash = "#/ledger";
let searchTimer = null;
let chartCount = 0; // ids for the charts' title and desc
// The review page: its CSRF token, its last decision, its "leave out" checkboxes, and whether a
// decision is being sent (a refresh on poll waits while either says a review is in progress).
const review = { csrf: "", result: null, ticks: [], busy: false };
let lastKnowledgeStamp = null;

// ---- DOM helpers ------------------------------------------------------------------------------

function isWebUrl(value) {
  if (typeof value !== "string" || /\s/.test(value)) return false;
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && Boolean(url.hostname);
  } catch (error) {
    return false;
  }
}

function safeHref(value) {
  const href = String(value);
  if (href.startsWith("#/") || isWebUrl(href)) return href;
  throw new Error("refusing a link that is neither a console route nor an http(s) URL");
}

// el("a", {className: "x", href: "#/ledger"}, "text", child): children are text or nodes.
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "className") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "href") node.setAttribute("href", safeHref(value));
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// What replaceChildren() may take: nodes and text, never null (the DOM would show "null").
function present(...nodes) {
  return nodes.flat().filter((node) => node !== null && node !== undefined && node !== false);
}

function itemHref(id) {
  return `#/${ITEM_ROUTE}/${encodeURIComponent(id)}`;
}

function itemLink(id, text) {
  return el("a", { href: itemHref(id), className: "item-id" }, text || id);
}

function externalLink(url, text) {
  if (!isWebUrl(url)) return el("span", { className: "not-a-link" }, text || String(url));
  return el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text || url);
}

function badge(text, kind) {
  return el("span", { className: `badge ${kind || ""}`.trim() }, text);
}

function groupBadge(state, group) {
  return el("span", { className: `badge ${group || ""}`.trim(), title: GROUP_LABELS[group] || "" }, state);
}

function statusClass(status) {
  return CLAUDE_STATUSES.includes(status) ? `status-${status}` : "status-none";
}

function statusBadge(status) {
  return el("span", { className: `badge ${statusClass(status)}` }, el("span", { className: "dot", "aria-hidden": "true" }), status);
}

function card(className, ...content) {
  return el("section", { className: `card ${className || ""}`.trim() }, ...content);
}

function section(title, ...content) {
  return card("", el("h2", {}, title), ...content);
}

function pairs(entries) {
  return el(
    "dl",
    { className: "pairs" },
    entries.filter(([, value]) => value !== "" && value !== null && value !== undefined)
      .map(([name, value]) => [el("dt", {}, name), el("dd", {}, value)]),
  );
}

function when(iso) {
  if (!iso) return "";
  const moment = new Date(iso);
  return Number.isNaN(moment.getTime()) ? String(iso) : moment.toLocaleString();
}

function ago(iso) {
  const moment = new Date(iso);
  if (!iso || Number.isNaN(moment.getTime())) return "";
  return `${duration(Math.max(0, (Date.now() - moment.getTime()) / 1000))} ago`;
}

// A coarse duration, as `ledger list` shows ages: 3d, 5h, 12m, <1m.
function duration(seconds) {
  for (const [unit, size] of Object.entries(SECONDS)) {
    if (seconds >= size) return `${Math.floor(seconds / size)}${unit}`;
  }
  return "<1m";
}

function timeCell(iso) {
  return el("time", { datetime: iso || "", title: when(iso) }, ago(iso));
}

// A command to paste into a terminal, with a copy button (navigator.clipboard; select it if that
// is unavailable, e.g. outside a secure context).
function copyable(command) {
  const code = el("code", {}, command);
  const button = el("button", { type: "button", className: "button", "aria-label": `Copy: ${command}` }, "Copy");
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(command);
      button.textContent = "Copied";
      button.classList.add("done");
      setTimeout(() => {
        button.textContent = "Copy";
        button.classList.remove("done");
      }, COPIED_MS);
    } catch (error) {
      window.getSelection().selectAllChildren(code);
    }
  });
  return el("div", { className: "command" }, code, button);
}

function meter(used, limit) {
  const ratio = limit > 0 ? Math.min(1, used / limit) : 0;
  const step = Math.round(ratio * METER_STEPS);
  return el("div", { className: "meter", role: "presentation" }, el("span", { className: `w${step}` }));
}

// ---- API --------------------------------------------------------------------------------------

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  } catch (error) {
    throw new ApiError(0, "The console is not running (start it with bosun console).");
  }
  if (response.status === 401) throw new ApiError(401, "Not signed in: open the URL that bosun console printed.");
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, body.error || `The console answered ${response.status}.`);
  return body;
}

// The console's one write: a knowledge review decision, as JSON, with the CSRF token the review
// model carried (the server also checks the cookie and the Origin).
function post(path, body, csrf) {
  return api(path, {
    method: "POST",
    keepalive: true, // a decision sent as the page closes still arrives
    headers: { "Content-Type": "application/json", [CSRF_HEADER]: csrf },
    body: JSON.stringify(body),
  });
}

// ---- routing ----------------------------------------------------------------------------------

function route() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [path, query] = [hash.split("?")[0], hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : ""];
  const [name, ...rest] = path.split("/");
  if (name === ITEM_ROUTE && rest.length) {
    return { view: ITEM_ROUTE, id: safeDecode(rest.join("/")) };
  }
  const view = Object.prototype.hasOwnProperty.call(VIEWS, name) ? name : DEFAULT_VIEW;
  // #/learning/report/<key> and #/library/<key>: one report or file, by its opaque key
  // #/knowledge/<key> and #/knowledge/review: an entity's page by its opaque key, the review batch
  // #/impact/internals: bosun's own diagnostics, off the Impact page
  const key = view === "learning" ? (rest[0] === "report" ? rest[1] : "") : ["library", "knowledge", "impact"].includes(view) ? rest[0] : "";
  return { view, params: new URLSearchParams(query), key: key ? safeDecode(key) : "" };
}

function safeDecode(text) {
  try {
    return decodeURIComponent(text);
  } catch (error) {
    return text;
  }
}

function main() {
  return document.getElementById("main");
}

function setNav(view) {
  for (const link of document.querySelectorAll("nav a")) {
    const active = link.dataset.view === view;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

function render() {
  stopConstellation();
  commitPending(); // a review decision still waiting for Z is sent when you leave
  const current = route();
  setNav(current.view === ITEM_ROUTE ? DEFAULT_VIEW : current.view);
  document.title = `${current.view === ITEM_ROUTE ? "Item" : VIEWS[current.view]} - bosun console`;
  if (current.view === ITEM_ROUTE) {
    renderItem(current.id);
  } else if (current.view === "ledger") {
    lastLedgerHash = location.hash || "#/ledger";
    renderLedger(current.params);
  } else if (current.view === "sessions") {
    renderSessions();
  } else if (current.view === "stats") {
    renderStats(current.params);
  } else if (current.view === "impact") {
    renderImpact(current.key);
  } else if (current.view === "learning") {
    renderLearning(current);
  } else if (current.view === "knowledge") {
    renderKnowledge(current);
  } else {
    renderLibrary(current);
  }
}

function refresh() {
  const current = route();
  if (current.view === ITEM_ROUTE) renderItem(current.id, true);
  else if (current.view === "ledger") loadLedger();
  else if (current.view === "stats") loadStats(statsDays(current.params));
  else if (current.view === "impact") loadImpact(current.key);
  else if (current.view === "learning" && !current.key) loadLearning();
  else if (current.view === "knowledge") refreshKnowledge(); // related ledger items
}

// The open Knowledge view again, when the knowledge base (or the ledger) changed; the review
// only while nothing is ticked and no decision is on its way, so a review in progress is kept.
function refreshKnowledge() {
  const current = route();
  if (current.view !== "knowledge") return;
  if (current.key === REVIEW_KEY) {
    if (!reviewInProgress()) loadReview();
  } else if (current.key) {
    renderEntity(current.key, true);
  } else if (kgTab(current.params) === "raw") {
    loadKnowledge(current.params);
  } else if (kgTab(current.params) === "briefing" && !stackInProgress()) {
    loadBriefing();
  } else if (kgTab(current.params) === "constellation") {
    showStarReload(); // the layout is kept until you ask for the new graph
  }
}

function reviewInProgress() {
  return review.busy || review.ticks.some((tick) => tick.checked);
}

function pageHead(title, ...extra) {
  return el("div", { className: "page-head" }, el("h1", {}, title), ...extra);
}

function showError(target, error) {
  target.replaceChildren(el("p", { className: "problem" }, error.message || String(error)));
}

// ---- Ledger: the list -------------------------------------------------------------------------

function options(select, choices, value) {
  select.replaceChildren(...choices.map(([key, label]) => el("option", { value: key }, label)));
  select.value = value;
  if (select.value !== value) select.value = choices.length ? choices[0][0] : "";
}

function control(label, input) {
  return el("label", { className: "control" }, el("span", {}, label), input);
}

function filterBar(params) {
  const form = el("form", { className: "filters", id: "filters", role: "search", onsubmit: (event) => event.preventDefault() });
  const q = el("input", { type: "search", name: "q", placeholder: "Search id, title, fields, notes", value: params.get("q") || "" });
  const person = el("input", { type: "text", name: "person", placeholder: "Requester or recipient", value: params.get("person") || "" });
  const selects = Object.fromEntries(["group", "kind", "window", "since", "sort"].map((name) => [name, el("select", { name })]));
  options(selects.group, GROUP_CHOICES, params.get("group") || DEFAULT_GROUP);
  options(selects.kind, [["", "All kinds"]], params.get("kind") || "");
  options(selects.window, [["", "All windows"]], params.get("window") || "");
  options(selects.since, SINCE_CHOICES, params.get("since") || "");
  options(selects.sort, SORT_CHOICES, params.get("sort") || "updated");
  for (const select of Object.values(selects)) select.addEventListener("change", filtersChanged);
  for (const input of [q, person]) input.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(filtersChanged, SEARCH_DELAY_MS);
  });
  form.append(
    control("Search", q),
    control("Group", selects.group),
    control("Kind", selects.kind),
    control("Window", selects.window),
    control("Person", person),
    control("Updated", selects.since),
    control("Sort", selects.sort),
  );
  return form;
}

function currentFilters() {
  const form = document.getElementById("filters");
  const params = new URLSearchParams();
  if (!form) return params;
  for (const key of FILTER_KEYS) {
    const value = (form.elements[key].value || "").trim();
    const isDefault = (key === "group" && value === DEFAULT_GROUP) || (key === "sort" && value === "updated");
    if (value && !isDefault) params.set(key, value);
  }
  return params;
}

function filtersChanged() {
  const query = currentFilters().toString();
  lastLedgerHash = `#/ledger${query ? `?${query}` : ""}`;
  history.replaceState(null, "", lastLedgerHash);
  loadList();
}

function apiQuery(params) {
  const query = new URLSearchParams(params);
  if (!query.has("group")) query.set("group", DEFAULT_GROUP);
  return query.toString();
}

function renderLedger(params) {
  main().replaceChildren(
    el("section", { id: "needs-you", className: "card needs-you", "aria-live": "polite" }, el("p", { className: "muted" }, "Loading...")),
    pageHead("Ledger", el("span", { id: "list-status", className: "muted small" })),
    card("", filterBar(params)),
    el("div", { id: "rows", className: "card flush" }),
  );
  loadLedger();
}

function loadLedger() {
  loadNeedsYou();
  loadList();
}

async function loadList() {
  const rows = document.getElementById("rows");
  if (!rows) return;
  try {
    const model = await api(`/api/items?${apiQuery(currentFilters())}`);
    fillFilterChoices(model);
    document.getElementById("list-status").textContent = `${model.count} of ${model.total} items`;
    rows.replaceChildren(model.rows.length ? rowsTable(model.rows) : el("p", { className: "placeholder padded" }, "No items match."));
  } catch (error) {
    showError(rows, error);
  }
}

function fillFilterChoices(model) {
  const form = document.getElementById("filters");
  if (!form) return;
  const counted = GROUP_CHOICES.map(([key, label]) => [key, key in model.groups ? `${label} (${model.groups[key]})` : label]);
  options(form.elements.group, counted, form.elements.group.value);
  options(form.elements.kind, [["", "All kinds"], ...model.kinds.map((k) => [k, k])], form.elements.kind.value);
  const windows = new Set(model.windows);
  if (form.elements.window.value) windows.add(form.elements.window.value);
  options(form.elements.window, [["", "All windows"], ...[...windows].sort().map((w) => [w, `@${w}`])], form.elements.window.value);
}

function waitingCell(waiting) {
  return waiting.map((w) => {
    const text = w.who ? `${w.who} (${w.state})` : `${w.id} (${w.state})`;
    return el("div", {}, w.id ? itemLink(w.id, text) : text);
  });
}

function windowCell(row) {
  if (!row.window) return "";
  return [`@${row.window}`, row.handover ? el("div", { className: "muted small" }, row.handover) : null];
}

function rowsTable(rows) {
  const columns = [["Item"], ["Kind", "optional"], ["State"], ["Title"], ["Window", "optional"], ["Waiting on", "optional"], ["Updated"]];
  const head = el("tr", {}, columns.map(([name, cls]) => el("th", { scope: "col", className: cls }, name)));
  const body = rows.map((row) =>
    el(
      "tr",
      { className: `group-${row.group}` },
      el("td", {}, itemLink(row.id)),
      el("td", { className: "optional" }, badge(row.kind, "kind")),
      el("td", {}, groupBadge(row.state, row.group), row.pending ? [" ", badge("proposal", "needs-me")] : row.proposal_status ? [" ", badge(row.proposal_status)] : null),
      el("td", { className: "title" }, row.title, row.url ? [" ", externalLink(row.url, "↗")] : null),
      el("td", { className: "optional" }, windowCell(row)),
      el("td", { className: "optional" }, waitingCell(row.waiting_on)),
      el("td", {}, timeCell(row.updated)),
    ),
  );
  return el("div", { className: "table-wrap" }, el("table", { className: "rows" }, el("thead", {}, head), el("tbody", {}, body)));
}

// ---- "Needs you": the strip and the top bar's badge -------------------------------------------

async function loadNeedsYou() {
  const strip = document.getElementById("needs-you");
  if (!strip) return;
  try {
    const model = await api("/api/needs-you");
    setNeedsBadge(model.count);
    strip.classList.toggle("calm", !model.count);
    strip.replaceChildren(...needsYou(model));
  } catch (error) {
    showError(strip, error);
  }
}

function setNeedsBadge(count) {
  const badgeLink = document.getElementById("needs-badge");
  document.getElementById("needs-count").textContent = String(count);
  badgeLink.classList.toggle("is-empty", !count);
  badgeLink.setAttribute("aria-label", `${count} things need you`);
}

async function updateNeedsBadge() {
  try {
    setNeedsBadge((await api("/api/needs-you")).count);
  } catch (error) {
    // the footer's status line already says what is wrong
  }
}

function needsEntry(...content) {
  return el("li", {}, el("div", { className: "line" }, ...content.slice(0, -1)), content[content.length - 1]);
}

function needsYou(model) {
  const head = el(
    "div",
    { className: "needs-head" },
    el("h2", {}, model.count ? `Needs you (${model.count})` : "Nothing needs you"),
    model.windows === null ? el("span", { className: "muted small" }, "(windows unknown: the session readers failed)") : null,
  );
  const entries = [
    ...(model.windows || []).map((w) =>
      needsEntry(statusBadge("waiting"), el("strong", {}, `@${w.name}`), el("span", { className: "muted" }, `waits at a prompt for ${duration(w.seconds || 0)}`), el("a", { href: "#/sessions", className: "small" }, "Sessions")),
    ),
    ...model.proposals.map((p) =>
      needsEntry(badge("proposal", "needs-me"), itemLink(p.id), el("span", { className: "muted" }, p.title), p.status === "awaiting your approval" ? null : el("span", { className: "problem small" }, p.status), el("pre", { className: "action" }, p.action)),
    ),
    ...model.outreach.map((o) =>
      needsEntry(
        badge("outreach", "needs-me"),
        itemLink(o.id),
        el("span", {}, `${o.purpose} by ${o.channel} to ${o.to}: ${o.subject}`),
        el("pre", { className: "action" }, o.action || "(no message proposed yet)"),
      ),
    ),
    ...model.mismatches.map((m) =>
      needsEntry(
        badge("ticket", "needs-me"),
        itemLink(m.id),
        groupBadge(m.state, "needs-me"),
        el("span", { className: "muted" }, m.title),
        el("span", { className: "muted small" }, "tracking ", m.tickets.map((t, i) => [i ? ", " : "", itemLink(t)])),
        el("ul", {}, m.hints.map((h) => el("li", {}, h))),
      ),
    ),
  ];
  // Approved proposals: bosun makes the call, or it was made and the proposal is closing. Not counted.
  const approved = (model.in_flight || []).map((p) =>
    el("li", { className: "muted small" }, badge(p.status), " ", itemLink(p.id), " ", p.title || ""),
  );
  return [
    head,
    entries.length ? el("ul", { className: "needs-list" }, entries) : el("p", { className: "muted small" }, "No proposals, outreach, ticket mismatches or waiting windows."),
    approved.length ? el("ul", { className: "needs-list approved" }, approved) : null,
  ].filter(Boolean);
}

// ---- Ledger: the item page --------------------------------------------------------------------

async function renderItem(id, keep) {
  const historyOpen = keep && Boolean(document.querySelector("details.history[open]"));
  if (!keep) main().replaceChildren(el("p", { className: "placeholder" }, "Loading..."));
  try {
    const item = await api(`/api/items/${encodeURIComponent(id)}`);
    main().replaceChildren(...itemPage(item, historyOpen));
    document.title = `${item.id} - bosun console`;
  } catch (error) {
    const message = error.status === 404 ? `No item ${id} in the ledger.` : error.message;
    main().replaceChildren(backLink(), card("", el("p", { className: "problem" }, message)));
  }
}

function backLink() {
  return el("a", { className: "back", href: lastLedgerHash.startsWith("#/") ? lastLedgerHash : "#/ledger" }, "← Ledger");
}

function header(item) {
  const url = item.url
    ? externalLink(item.url)
    : item.url_text ? el("span", { className: "not-a-link" }, `${item.url_text} (not a web link)`) : null;
  return el(
    "div",
    { className: "item-head" },
    el("h1", {}, item.title || item.id),
    el(
      "div",
      { className: "meta" },
      el("code", {}, item.id),
      badge(item.kind, "kind"),
      groupBadge(item.state, item.group),
      el("span", { className: "muted" }, `created ${when(item.created)} · updated ${when(item.updated)}`),
    ),
    url ? el("div", { className: "small" }, url) : null,
    item.description ? el("p", { className: "muted small" }, item.description) : null,
  );
}

function proposalBlock(item) {
  if (!item.proposal) return null;
  const status = item.proposal.status ? ` · ${item.proposal.status}` : "";
  return card("callout", el("h2", {}, "Pending proposal"), el("p", { className: "muted small" }, `proposed ${when(item.proposal.at)}${status}`), el("pre", { className: "action" }, item.proposal.action));
}

function hintsBlock(item) {
  if (!item.hints.length) return null;
  return card("callout", el("h2", {}, "Hints"), el("ul", {}, item.hints.map((hint) => el("li", {}, hint))));
}

function waitingBlock(item) {
  if (!item.waiting_on.length) return null;
  return section("Waiting on", el("ul", {}, item.waiting_on.map((w) => el("li", {}, waitingCell([w])))));
}

function fieldsBlock(item) {
  if (!item.fields.length) return null;
  return section("Fields", pairs(item.fields.map((f) => [f.name, f.value])));
}

function delegationBlock(item) {
  const d = item.delegation;
  if (!d) return null;
  const status = d.active ? (d.awaiting_report ? "awaiting report" : d.handover || "handed over") : "finished";
  return section(
    "Delegation",
    pairs([
      ["window", `@${d.window}`],
      ["task", d.task],
      ["since", when(d.at)],
      ["status", status],
      ["ask", d.ask],
      ["requester", d.requester],
      ["link", d.link ? externalLink(d.link) : ""],
      ["updates", d.updates],
    ]),
  );
}

function reportBlock(item) {
  const r = item.report;
  if (!r) return null;
  return section(
    "Latest report",
    pairs([
      ["at", `${when(r.at)}${r.window ? ` from @${r.window}` : ""}`],
      ["summary", r.summary],
      ["results", r.results.length ? r.results.map((x) => el("div", {}, x.url ? externalLink(x.url) : x.text)) : ""],
      ["open", r.open],
    ]),
  );
}

function outreachBlock(item) {
  const o = item.outreach;
  if (!o) return null;
  const messages = o.messages.map((m, i) =>
    el("div", {}, `${i ? `nudge ${i}` : "sent"} ${when(m.at)} by ${m.channel}: `, m.url ? externalLink(m.url) : m.ref),
  );
  return section(
    "Outreach",
    pairs([
      ["to", o.to],
      ["channel", o.channel],
      ["purpose", o.purpose],
      ["subject", o.subject],
      ["options", o.options.join(" | ")],
      ["details", o.details],
      ["for", o.parent ? itemLink(o.parent) : ""],
      ["expect by", o.expect_by],
      ["messages", messages.length ? messages : ""],
      ["nudges", o.messages.length ? String(o.nudges) : ""],
      ["waiting", o.waiting_seconds === null ? "" : duration(o.waiting_seconds)],
      ["reply", o.answer],
      ["reply ref", o.reply_url ? externalLink(o.reply_url) : o.reply_ref],
      ["replied", when(o.replied_at)],
    ]),
  );
}

function saidBlock(item) {
  if (!item.said.length) return null;
  return section(
    "What I said",
    item.said.map((s) =>
      el(
        "div",
        { className: "said" },
        el("p", { className: "muted small" }, `${when(s.at)} in @${s.window}${s.held_only ? " (the window also held this item)" : ""}`),
        el("pre", {}, s.text),
      ),
    ),
  );
}

function treeNode(node) {
  const name = node.focus ? el("strong", { className: "item-id" }, node.id) : itemLink(node.id);
  const label = el(
    "span",
    {},
    node.how ? el("span", { className: "muted" }, `${node.how}: `) : null,
    name,
    " ",
    node.missing ? badge("missing", "error") : badge(node.state),
    node.title ? ` ${node.title}` : "",
    node.repeat ? el("span", { className: "muted" }, " (shown above)") : null,
  );
  return el("li", {}, label, node.children.length ? el("ul", {}, node.children.map(treeNode)) : null);
}

function lineageBlock(item) {
  if (!item.tree.length && !item.links.length) return null;
  const chips = item.links.map((link) =>
    el(
      "div",
      { className: "chip" },
      el("span", { className: "muted" }, link.label),
      itemLink(link.id),
      link.missing ? badge("missing", "error") : badge(link.state),
      link.via ? el("span", { className: "muted" }, "via ", link.via_url ? externalLink(link.via_url) : link.via) : null,
    ),
  );
  return section(
    "Lineage",
    item.tree.length ? el("ul", { className: "tree" }, item.tree.map(treeNode)) : null,
    chips.length ? el("div", { className: "chips" }, chips) : null,
  );
}

function anchorsBlock(item) {
  if (!item.anchors.length) return null;
  return section(
    "Links out",
    el(
      "ul",
      { className: "anchors" },
      item.anchors.map((a) => el("li", {}, badge(a.label, "kind"), " ", externalLink(a.url), el("div", { className: "muted small" }, a.where.join(", ")))),
    ),
  );
}

function historyBlock(item, open) {
  const entries = [...item.history].reverse().map((h) =>
    el(
      "li",
      {},
      el("span", { className: "muted small" }, when(h.at)),
      " ",
      el("strong", {}, h.event),
      h.note ? ` ${h.note}` : "",
      h.details.length ? el("div", { className: "muted small" }, h.details.map((d) => `${d.name}: ${d.value}`).join("; ")) : null,
    ),
  );
  return el(
    "details",
    { className: "history card", open },
    el("summary", {}, `History (${item.history.length} entries, newest first)`),
    el("ol", { className: "timeline" }, entries),
  );
}

function itemPage(item, historyOpen) {
  const mainColumn = [
    proposalBlock(item),
    hintsBlock(item),
    outreachBlock(item),
    reportBlock(item),
    saidBlock(item),
    fieldsBlock(item),
    historyBlock(item, historyOpen),
  ].filter(Boolean);
  const side = [waitingBlock(item), delegationBlock(item), lineageBlock(item), anchorsBlock(item)].filter(Boolean);
  return [
    backLink(),
    header(item),
    el(
      "div",
      { className: "item-grid" },
      el("div", { className: "stack" }, mainColumn),
      side.length ? el("aside", { className: "stack", "aria-label": "Lineage and links" }, side) : null,
    ),
  ];
}

// ---- Sessions and health ----------------------------------------------------------------------

function renderSessions() {
  main().replaceChildren(
    pageHead("Sessions", el("span", { id: "sessions-status", className: "muted small" })),
    el("div", { id: "attach" }),
    el("div", { id: "windows", className: "cards" }, el("p", { className: "placeholder" }, "Loading...")),
    el("h2", { className: "section-title" }, "Health"),
    el("div", { id: "health" }),
  );
  loadSessions();
}

async function loadSessions() {
  const windows = document.getElementById("windows");
  const health = document.getElementById("health");
  if (!windows || !health) return;
  const [sessions, healthModel] = await Promise.allSettled([api("/api/sessions"), api("/api/health")]);
  if (sessions.status === "fulfilled") showSessions(sessions.value);
  else showError(windows, sessions.reason);
  if (healthModel.status === "fulfilled") health.replaceChildren(...healthPanel(healthModel.value));
  else showError(health, healthModel.reason);
}

function showSessions(model) {
  const counts = Object.entries(model.counts).map(([state, n]) => badge(`${n} ${state}`, statusClass(state)));
  document.getElementById("sessions-status").replaceChildren(
    el("span", { className: "summary-chips" }, counts, el("span", {}, `read ${ago(model.collected_at)}`)),
  );
  document.getElementById("attach").replaceChildren(
    card("", el("h2", {}, `Attach to ${model.session || "the session"}`), copyable(model.attach)),
  );
  const windows = document.getElementById("windows");
  if (model.error) windows.replaceChildren(card("", el("p", { className: "problem" }, `Could not read the windows: ${model.error}`)));
  else if (!model.windows.length) windows.replaceChildren(card("", el("p", { className: "placeholder" }, "No windows.")));
  else windows.replaceChildren(...model.windows.map(windowCard));
}

function heldList(w) {
  if (!w.held.length) return el("p", { className: "muted small" }, "Holds no items.");
  return el(
    "ul",
    { className: "held" },
    w.held.map((i) =>
      el(
        "li",
        { className: i.id === w.active ? "active" : "" },
        itemLink(i.id),
        badge(i.state),
        i.id === w.active ? badge(i.working ? "working" : "active", i.working ? "status-busy" : "") : null,
        el("span", { className: "muted" }, i.title),
      ),
    ),
  );
}

function windowCard(w) {
  const claude = w.claude;
  const since = claude && claude.seconds !== null ? `for ${duration(claude.seconds)}` : "";
  const model = [w.model, w.effort].filter(Boolean).join(" / ");
  const stateClass = w.state === "waiting" ? "state-waiting" : claude ? "" : "state-none";
  return card(
    `window-card ${stateClass}`,
    el("div", { className: "card-head" }, el("h3", {}, `@${w.name}`), statusBadge(w.state)),
    pairs([
      ["kind", w.kind],
      ["status", claude ? `${claude.status} ${since}`.trim() : w.state],
      ["model", model],
      ["claude", claude ? [claude.version ? `v${claude.version}` : "", claude.name ? ` ${claude.name}` : ""].join("") : ""],
      ["folder", w.dir ? el("span", { className: "dir mono" }, w.dir) : ""],
      ["login", w.login ? el("span", { className: w.login.state === "needs-login" ? "problem" : "" }, `${w.login.state}: ${w.login.detail}`) : ""],
    ]),
    heldList(w),
    w.last_report
      ? el(
          "div",
          { className: "report" },
          el("div", { className: "muted small" }, "Last report ", timeCell(w.last_report.at), " on ", itemLink(w.last_report.id)),
          w.last_report.summary,
        )
      : null,
    w.window ? copyable(w.attach) : null,
  );
}

function checkRow(check) {
  const level = check.ok ? "ok" : check.severity === "error" ? "error" : "warn";
  const label = check.ok ? "ok" : check.severity === "error" ? "fail" : "warn";
  const note = check.ok
    ? el("span", { className: "note muted" }, check.note)
    : el("span", { className: "note" }, check.findings.map((f) => el("div", {}, f.message, el("span", { className: "muted small" }, ` [${f.decision}]`))));
  return el("li", { title: check.summary }, badge(label, level), el("span", { className: "name" }, check.name), note);
}

function watchdogCard(w) {
  if (w.error) return card("", el("h2", {}, "Watchdog"), el("p", { className: "problem" }, w.error));
  return card(
    "",
    el("div", { className: "card-head" }, el("h2", {}, "Watchdog checks"), badge(w.problems ? `${w.problems} problem${w.problems > 1 ? "s" : ""}` : "all ok", w.problems ? "warn" : "ok")),
    el("p", { className: "muted small" }, `${w.awake_window}; last full run ${w.last_run || "never"}; last alert ${w.last_alert || "never"}`),
    el("ul", { className: "checks" }, w.checks.map(checkRow)),
  );
}

function runsCard(h) {
  const m = h.morning;
  return section(
    "Morning and chores",
    pairs([
      ["morning run", m ? `${m.started} (${m.trigger}): ${m.outcome}` : "none today"],
      ["problems", m && m.problems.length ? m.problems.map((p) => el("div", { className: "small" }, p)) : ""],
      ...h.chores.map((c) => [`last ${c.name}`, el("span", {}, when(c.at), " ", el("span", { className: "muted" }, `(${ago(c.at)})`))]),
    ]),
  );
}

function enabledBadge(on) {
  return badge(on ? "on" : "off", on ? "ok" : "");
}

function eventsCard(e) {
  return section(
    "Events dispatcher",
    pairs([
      ["enabled", enabledBadge(e.enabled)],
      ["today", el("div", {}, `${e.deliveries_today} of ${e.max_per_day} deliveries`, meter(e.deliveries_today, e.max_per_day))],
      ["pending", String(e.pending)],
      ["last run", e.last_tick ? `${when(e.last_tick)} (${ago(e.last_tick)})` : "never"],
      ["last error", e.last_error ? el("span", { className: "problem" }, `${when(e.last_error.at)}: ${e.last_error.error}`) : ""],
      ["problem", e.error ? el("span", { className: "problem" }, e.error) : ""],
    ]),
  );
}

function slackCard(s) {
  const run = s.last_run;
  return section(
    "Slack watcher",
    pairs([
      ["enabled", [enabledBadge(s.enabled), s.enabled && !s.active ? el("span", { className: "muted small" }, " (needs events on)") : null]],
      ["spend today", el("div", {}, `$${s.spend_today.toFixed(2)} of $${s.cap.toFixed(2)}${s.budget_spent_today ? " (budget spent)" : ""}`, meter(s.spend_today, s.cap))],
      ["last run", run ? `${when(run.started)}: ${run.outcome}${run.error ? ` (${run.error})` : ` (${run.found} found, ${run.queued} queued)`}` : "never"],
      ["failures", s.failures ? el("span", { className: "problem" }, `${s.failures} in a row since ${when(s.failing_since)}`) : ""],
      ["problem", s.error ? el("span", { className: "problem" }, s.error) : ""],
    ]),
  );
}

function loginCard(l) {
  const level = l.state === "ok" ? "ok" : l.state === "needs-login" ? "error" : "";
  return section(
    "Login",
    pairs([
      ["state", [badge(l.state, level), " ", l.detail]],
      ["last check", l.last_check ? `${when(l.last_check.at)}: ${l.last_check.outcome}` : "none yet"],
      ["paused", l.gated.length ? l.gated.map((g) => el("div", {}, `@${g.window} since ${when(g.since)}`)) : ""],
      ["windows", l.windows.length ? l.windows.map((w) => el("div", { className: "small" }, `@${w.name}: ${w.state}`)) : ""],
    ]),
  );
}

function flagsCard(h) {
  const names = { events: "events", slack_watch: "Slack watcher", learn: "learning" };
  return section(
    "Feature flags",
    el("div", { className: "flags" }, Object.entries(h.flags).map(([key, on]) => badge(`${names[key] || key}: ${on ? "on" : "off"}`, on ? "ok" : ""))),
    el("h2", { className: "spaced" }, "Not shown here"),
    el("ul", { className: "muted small" }, h.left_out.map((text) => el("li", {}, text))),
  );
}

function healthPanel(h) {
  return [
    watchdogCard(h.watchdog),
    el("div", { className: "health-grid" }, runsCard(h), eventsCard(h.events), slackCard(h.slack_watch), loginCard(h.login), flagsCard(h)),
  ];
}

// ---- charts: small inline-SVG helpers ---------------------------------------------------------
// Built with createElementNS; geometry goes in SVG attributes (x, y, width, height), colours in
// classes (series-0 ... series-7 in app.css), never style attributes. Each chart is an <svg
// role="img"> with a <title> and <desc>, a text summary under it, and its numbers in a table
// behind a "Data table" toggle.

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null && value !== false) node.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function seriesClass(index) {
  return `series-${index % SERIES_COLOURS}`;
}

function chartSvg(title, summary, width, height, marks) {
  chartCount += 1;
  const id = `chart-${chartCount}`;
  return svg(
    "svg",
    { class: "chart", viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-labelledby": `${id}-title ${id}-desc` },
    svg("title", { id: `${id}-title` }, title),
    svg("desc", { id: `${id}-desc` }, summary),
    marks,
  );
}

function dataTable(columns, rows) {
  const cells = (row) => row.map((cell, i) => (i ? el("td", {}, cell) : el("th", { scope: "row" }, cell)));
  return el(
    "details",
    { className: "data-table" },
    el("summary", {}, "Data table"),
    el(
      "div",
      { className: "table-wrap" },
      el(
        "table",
        { className: "rows compact" },
        el("thead", {}, el("tr", {}, columns.map((name) => el("th", { scope: "col" }, name)))),
        el("tbody", {}, rows.map((row) => el("tr", {}, cells(row)))),
      ),
    ),
  );
}

function legend(names, extra) {
  return el("ul", { className: `legend ${extra || ""}`.trim(), "aria-hidden": "true" }, names.map((name, i) => el("li", {}, el("span", { className: `swatch ${seriesClass(i)}` }), name)));
}

function chartFigure(chart, summary, table, names) {
  return el(
    "figure",
    { className: "chart-figure" },
    chart,
    names && names.length > 1 ? legend(names) : null,
    el("figcaption", { className: "muted small" }, summary),
    table,
  );
}

function sum(values) {
  return values.reduce((a, b) => a + b, 0);
}

function rounded(value) {
  return Math.round(value * 100) / 100;
}

// "Thu 15" for a date like 2026-10-15; anything else as given.
function dayLabel(label) {
  const day = new Date(`${label}T00:00:00`);
  if (Number.isNaN(day.getTime())) return label;
  return `${day.toLocaleDateString(undefined, { weekday: "short" })} ${day.getDate()}`;
}

// A round number at or above <max> for the top gridline: 1, 2, 5 times a power of ten.
function niceTop(max) {
  if (max <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(max));
  const step = [1, 2, 5, 10].find((n) => n * power >= max);
  return step * power;
}

// The marks of a stacked day chart <width> wide: gridlines with their values, one bar per day
// with its total on top, and day labels where they fit.
function stackedMarks(width, labels, series, totals) {
  const { height, axis, left, gap, headroom, dayLabelMin } = CHART;
  const top = niceTop(Math.max(...totals, 0));
  const plot = height - axis;
  const y = (value) => plot - (value / top) * (plot - headroom);
  const slot = (width - left) / Math.max(1, labels.length);
  const bar = Math.max(2, slot * (1 - gap));
  const every = Math.max(1, Math.ceil(dayLabelMin / slot));
  const marks = [top / 2, top].map((v) => [
    svg("line", { class: "grid", x1: left, x2: width, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }),
    svg("text", { class: "tick end", x: left - 6, y: (y(v) + 4).toFixed(1) }, String(rounded(v))),
  ]);
  marks.push(svg("line", { class: "axis", x1: left, y1: plot, x2: width, y2: plot }));
  labels.forEach((label, i) => {
    const x = left + i * slot + (slot - bar) / 2;
    let base = 0;
    series.forEach((s, k) => {
      const value = s.values[i] || 0;
      if (!value) return;
      const lower = y(base);
      base += value;
      const box = { x: x.toFixed(1), y: y(base).toFixed(1), width: bar.toFixed(1), height: Math.max(0.5, lower - y(base)).toFixed(1) };
      marks.push(svg("rect", { class: `seg ${seriesClass(k)}`, ...box }, svg("title", {}, `${label}: ${s.name} ${value}`)));
    });
    const middle = (x + bar / 2).toFixed(1);
    if (totals[i]) marks.push(svg("text", { class: "bar-total", x: middle, y: (y(totals[i]) - 6).toFixed(1) }, String(totals[i])));
    if (i % every === 0) marks.push(svg("text", { class: "tick mid", x: middle, y: height - 6 }, dayLabel(label)));
  });
  return marks;
}

// An <svg> that redraws itself at its box's width (a ResizeObserver; without one, CHART.width).
function fluidChart(title, summary, draw) {
  chartCount += 1;
  const id = `chart-${chartCount}`;
  const node = svg("svg", { class: "chart", role: "img", "aria-labelledby": `${id}-title ${id}-desc` });
  const render = (width) => {
    const w = Math.max(CHART.minWidth, Math.round(width));
    for (const [key, value] of Object.entries({ viewBox: `0 0 ${w} ${CHART.height}`, width: w, height: CHART.height })) node.setAttribute(key, String(value));
    node.replaceChildren(svg("title", { id: `${id}-title` }, title), svg("desc", { id: `${id}-desc` }, summary), ...draw(w).flat(Infinity));
  };
  render(CHART.width);
  const box = el("div", { className: "chart-box" }, node);
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver((entries) => {
      const width = entries[0].contentRect.width;
      if (width && Math.abs(width - Number(node.getAttribute("width"))) > 2) render(width);
    }).observe(box);
  }
  return box;
}

// Stacked vertical bars: one column per label (a day), one segment per series (a kind).
// options.legend false: the caller shows one legend for several charts.
function stackedBars(title, labels, series, options = {}) {
  const totals = labels.map((_, i) => rounded(sum(series.map((s) => s.values[i] || 0))));
  const total = rounded(sum(totals));
  const busiest = totals.indexOf(Math.max(...totals, 0));
  const unit = options.unit || "day";
  const summary = total ? `${total} in ${plural(labels.length, unit)}; most on ${labels[busiest]} (${totals[busiest]}).` : `None in ${plural(labels.length, unit)}.`;
  const table = dataTable([unit === "day" ? "Day" : "Week", ...series.map((s) => s.name), "Total"], labels.map((label, i) => [label, ...series.map((s) => String(s.values[i] || 0)), String(totals[i])]));
  const chart = fluidChart(title, summary, (width) => stackedMarks(width, labels, series, totals));
  return chartFigure(chart, summary, table, options.legend === false ? null : series.map((s) => s.name));
}

// Horizontal bars: one row per entry ({name, count}), in the order given.
function bars(title, entries) {
  const { width, row, label, value } = BARS;
  const top = Math.max(1, ...entries.map((e) => e.count));
  const height = Math.max(row, entries.length * row);
  const marks = entries.map((e, i) => {
    const w = (e.count / top) * (width - label - value);
    const baseline = i * row + row * BARS.baseline;
    return svg(
      "g",
      {},
      svg("text", { class: "bar-label", x: 0, y: baseline }, e.name),
      svg("rect", { class: seriesClass(0), x: label, y: i * row + BARS.inset, width: Math.max(1, w).toFixed(1), height: row - 2 * BARS.inset }, svg("title", {}, `${e.name}: ${e.count}`)),
      svg("text", { class: "bar-value", x: (label + w + BARS.inset).toFixed(1), y: baseline }, String(e.count)),
    );
  });
  const summary = entries.length ? entries.map((e) => `${e.name} ${e.count}`).join(", ") : "None.";
  return chartFigure(chartSvg(title, summary, width, height, marks), summary, dataTable(["Name", "Count"], entries.map((e) => [e.name, String(e.count)])));
}

// A sparkline: a small line over equally spaced values; its title says the numbers.
function sparkline(title, values) {
  const { width, height } = SPARK;
  const top = Math.max(...values, 0) || 1;
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  const points = values.map((v, i) => `${(i * step).toFixed(1)},${(height - 1 - (v / top) * (height - 2)).toFixed(1)}`).join(" ");
  const summary = `${title}: ${values.join(", ")} (total ${rounded(sum(values))})`;
  return chartSvg(title, summary, width, height, [svg("polyline", { class: "spark", points })]);
}

// ---- Stats ------------------------------------------------------------------------------------

// "3h 20m", "2d 4h", "45m"; "n/a" when there was nothing to measure.
function span(seconds) {
  if (seconds === null || seconds === undefined) return "n/a";
  const units = Object.entries(SECONDS);
  for (let i = 0; i < units.length; i += 1) {
    const [unit, size] = units[i];
    if (seconds < size) continue;
    const next = units[i + 1];
    const rest = next ? Math.floor((seconds % size) / next[1]) : 0;
    return `${Math.floor(seconds / size)}${unit}${rest ? ` ${rest}${next[0]}` : ""}`;
  }
  return "<1m";
}

function usd(value) {
  return `$${(value || 0).toFixed(2)}`;
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function statsDays(params) {
  const days = Number(params.get("days"));
  return STATS_PERIODS.includes(days) ? days : STATS_PERIODS[0];
}

function periodPicker(days) {
  return el(
    "nav",
    { className: "segmented", "aria-label": "Period" },
    STATS_PERIODS.map((d) => el("a", { href: `#/stats?days=${d}`, className: d === days ? "active" : "", "aria-current": d === days ? "page" : null }, `${d} days`)),
  );
}

function renderStats(params) {
  const days = statsDays(params);
  main().replaceChildren(pageHead("Stats", periodPicker(days)), el("div", { id: "stats" }, el("p", { className: "placeholder" }, "Loading...")));
  loadStats(days);
}

async function loadStats(days) {
  const target = document.getElementById("stats");
  if (!target) return;
  try {
    target.replaceChildren(...statsPage(await api(`/api/stats?days=${days}`)));
  } catch (error) {
    showError(target, error);
  }
}

function tile(label, value, extra) {
  return el("div", { className: "tile card" }, el("div", { className: "tile-value" }, value), el("div", { className: "muted small" }, label), extra);
}

function kindSeries(t, what, kinds) {
  return kinds.map((kind) => ({ name: kind, values: t.days.map((d) => d[what][kind] || 0) }));
}

// Kinds by volume over the period, largest first: the bottom of each stack and the legend's order.
function kindsByVolume(t) {
  const volume = (kind) => sum(t.days.map((d) => (d.opened[kind] || 0) + (d.closed[kind] || 0)));
  return [...t.kinds].sort((a, b) => volume(b) - volume(a));
}

function perDay(t, what) {
  return t.days.map((d) => sum(Object.values(d[what])));
}

function statsTiles(s) {
  const t = s.throughput;
  return el(
    "div",
    { className: "tiles" },
    tile("items opened", String(t.opened), sparkline("Items opened per day", perDay(t, "opened"))),
    tile("items closed", String(t.closed), sparkline("Items closed per day", perDay(t, "closed"))),
    tile("my PRs merged", String(s.prs.merged)),
    tile("review rounds done", String(s.reviews.done)),
    tile("tasks done", String(s.tasks.done)),
    tile("outreach sent", String(s.outreach.sent)),
  );
}

function throughputCard(s) {
  const t = s.throughput;
  const labels = t.days.map((d) => d.date);
  const kinds = kindsByVolume(t);
  return section(
    "Throughput",
    legend(kinds, "legend-top"),
    el(
      "div",
      { className: "split" },
      el("div", {}, el("h3", {}, "Opened per day"), stackedBars("Items opened per day, by kind", labels, kindSeries(t, "opened", kinds), { legend: false })),
      el("div", {}, el("h3", {}, "Closed per day"), stackedBars("Items closed per day, by kind", labels, kindSeries(t, "closed", kinds), { legend: false })),
    ),
  );
}

function prsCard(s) {
  return section(
    "PRs and reviews",
    pairs([
      ["my PRs merged", String(s.prs.merged)],
      ["median open to merge", span(s.prs.median_open_to_merge_seconds)],
      ["review rounds done", `${s.reviews.done} (on ${plural(s.reviews.items, "item")})`],
      ["median turnaround", span(s.reviews.median_turnaround_seconds)],
    ]),
  );
}

function tasksCard(s) {
  const t = s.tasks;
  return section(
    "Tasks and hand-overs",
    pairs([
      ["spawned", String(t.spawned)],
      ["accepted", String(t.accepted)],
      ["done", String(t.done)],
      ["declined / dropped", `${t.declined} / ${t.dropped}`],
      ["hand-overs", String(t.handovers)],
      ["median hand-over to report", span(t.median_handover_to_report_seconds)],
    ]),
    t.per_window.length ? [el("h3", {}, "Hand-overs per window"), bars("Hand-overs per window", t.per_window.map((w) => ({ name: `@${w.window}`, count: w.count })))] : null,
  );
}

function outreachCard(s) {
  const o = s.outreach;
  const outcomes = [
    { name: "replied", count: o.replied },
    { name: "no reply", count: o.no_reply },
    { name: "waiting", count: o.waiting },
    { name: "dropped", count: o.dropped },
  ];
  return section(
    "Outreach",
    pairs([
      ["sent", `${o.sent}${o.one_way ? ` (${o.one_way} one-way)` : ""}`],
      ["median reply time", span(o.median_reply_seconds)],
      ["nudges", String(o.nudges)],
      ["nudges per reply", o.nudges_per_reply === null ? "n/a" : String(o.nudges_per_reply)],
    ]),
    o.sent > o.one_way ? [el("h3", {}, "How two-way outreach ended"), bars("Two-way outreach by outcome", outcomes)] : null,
  );
}

function peopleList(title, tally) {
  return [
    el("h3", {}, `${title} (${tally.distinct} distinct)`),
    tally.top.length ? bars(title, tally.top) : el("p", { className: "muted small" }, "Nobody in this period."),
  ];
}

function peopleCard(s) {
  return wideSection(
    "People",
    el("p", { className: "muted small" }, "Names come from the local ledger and stay on this machine."),
    el(
      "div",
      { className: "split" },
      el("div", {}, peopleList("Reached out to", s.people.recipients)),
      el("div", {}, peopleList("Worked for", s.people.requesters)),
    ),
  );
}

function deliveriesText(d) {
  const ways = Object.entries(d.by_via).map(([via, n]) => `${n} by ${via}`);
  return ways.length ? `${ways.join(", ")} (${plural(d.events, "event")})` : "none";
}

function bosunCard(s) {
  const a = s.bosun;
  const d = a.deliveries;
  const problems = [d.error, a.slack_watch.error, a.learn.error].filter(Boolean);
  return section(
    "Bosun itself",
    pairs([
      ...a.chores.map((c) => [`last ${c.name}`, when(c.at)]),
      ["event deliveries", deliveriesText(d)],
      ["Slack watcher", `${plural(a.slack_watch.runs, "run")} (${a.slack_watch.failed} failed), ${usd(a.slack_watch.usd)}`],
      ["learning", `${plural(a.learn.runs, "run")}, ${plural(a.learn.dry_runs, "dry run")} (${a.learn.failed} failed), ${usd(a.learn.usd)}`],
      ["problems", problems.length ? problems.map((p) => el("div", { className: "problem small" }, p)) : ""],
    ]),
    el("p", { className: "muted small" }, `Deliveries, Slack-watch runs and spend are kept for ${d.kept_days} days, so a longer period shows only their last ${d.kept_days}. The ledger keeps only the latest chores run of each name.`),
    el("h3", {}, "Spend per day (USD)"),
    stackedBars("Spend per day in USD: Slack watcher and learning", a.spend.map((x) => x.date), [
      { name: "Slack watcher", values: a.spend.map((x) => x.slack_watch) },
      { name: "learning", values: a.spend.map((x) => x.learn) },
    ]),
  );
}

function wideSection(title, ...content) {
  return card("span-all", el("h2", {}, title), ...content);
}

function statsPage(s) {
  const odd = s.odd_times ? el("span", { className: "problem" }, ` ${plural(s.odd_times, "time")} in the ledger could not be read and are left out.`) : null;
  return [
    el("p", { className: "muted small" }, `From ${when(s.from)} to ${when(s.to)}.`, odd),
    statsTiles(s),
    throughputCard(s),
    el("div", { className: "stats-grid" }, prsCard(s), tasksCard(s), outreachCard(s), bosunCard(s), peopleCard(s)),
  ];
}

// ---- Markdown: a small, safe renderer ---------------------------------------------------------
// Everything becomes text nodes through el(); no HTML in the source is ever parsed, so a <script>
// in a file shows as text. Blocks: headings, paragraphs, lists (nested by indent), fenced code,
// block quotes, rules and simple pipe tables. Inline: `code`, **strong**, *emphasis*, _emphasis_
// and [links](url), which become links only through safeHref (console routes and http(s));
// anything else stays text with its target shown.

const INLINE = /(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)|\*\*([^*]+)\*\*|__([^_]+)__|(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])|(?<![\w_])_([^_\s][^_]*?)_(?![\w_])|\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/g;
const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const FENCE = /^\s*(```|~~~)/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const TABLE_DIVIDER = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
const HEADING_OFFSET = 1; // a file's "#" is the page's h2: the page title is its h1
const MAX_HEADING = 6;

function markdownLink(text, target) {
  let href = null;
  try {
    href = safeHref(target);
  } catch (error) {
    return el("span", { className: "not-a-link" }, `${text} (${target})`);
  }
  return href.startsWith("#/") ? el("a", { href }, text) : el("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);
}

function inline(text) {
  const nodes = [];
  let last = 0;
  for (const match of String(text).matchAll(INLINE)) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const [, ticks, code, strong, strong2, em, em2, label, target] = match;
    if (ticks) nodes.push(el("code", {}, code));
    else if (strong || strong2) nodes.push(el("strong", {}, strong || strong2));
    else if (em || em2) nodes.push(el("em", {}, em || em2));
    else nodes.push(markdownLink(label, target));
    last = match.index + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function tableCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function isTableStart(lines, i) {
  return lines[i].includes("|") && i + 1 < lines.length && lines[i + 1].includes("-") && TABLE_DIVIDER.test(lines[i + 1]);
}

// Each block reader takes the lines and a position, returns [node, next position].
function readFence(lines, i) {
  const marker = lines[i].trim().slice(0, 3);
  const body = [];
  let j = i + 1;
  while (j < lines.length && !lines[j].trim().startsWith(marker)) body.push(lines[j++]);
  return [el("pre", { className: "code" }, el("code", {}, body.join("\n"))), j + 1];
}

function readTable(lines, i) {
  const head = tableCells(lines[i]);
  const rows = [];
  let j = i + 2;
  while (j < lines.length && lines[j].includes("|") && lines[j].trim()) rows.push(tableCells(lines[j++]));
  const table = el(
    "table",
    { className: "rows compact" },
    el("thead", {}, el("tr", {}, head.map((cell) => el("th", { scope: "col" }, inline(cell))))),
    el("tbody", {}, rows.map((row) => el("tr", {}, head.map((_, k) => el("td", {}, inline(row[k] || "")))))),
  );
  return [el("div", { className: "table-wrap" }, table), j];
}

function readList(lines, i) {
  const root = { indent: -1, node: null, children: [] };
  const stack = [root];
  let j = i;
  while (j < lines.length) {
    const match = LIST_ITEM.exec(lines[j]);
    if (!match) {
      if (!lines[j].trim() || !/^\s/.test(lines[j]) || stack.length < 2) break;
      stack[stack.length - 1].text.push(" ", lines[j].trim()); // a continuation line
      j += 1;
      continue;
    }
    const indent = match[1].length;
    while (stack.length > 1 && indent <= stack[stack.length - 1].indent) stack.pop();
    const entry = { indent, ordered: /\d/.test(match[2]), text: [match[3]], children: [] };
    stack[stack.length - 1].children.push(entry);
    stack.push(entry);
    j += 1;
  }
  return [listNode(root.children), j];
}

function listNode(entries) {
  return el(
    entries[0] && entries[0].ordered ? "ol" : "ul",
    {},
    entries.map((e) => el("li", {}, inline(e.text.join("")), e.children.length ? listNode(e.children) : null)),
  );
}

function readQuote(lines, i) {
  const body = [];
  let j = i;
  while (j < lines.length && /^\s*>/.test(lines[j])) body.push(lines[j++].replace(/^\s*>\s?/, ""));
  return [el("blockquote", {}, markdownBlocks(body)), j];
}

function readParagraph(lines, i) {
  const body = [];
  let j = i;
  while (j < lines.length && lines[j].trim() && !startsBlock(lines, j)) body.push(lines[j++].trim());
  return [el("p", {}, inline(body.join(" "))), j];
}

function startsBlock(lines, i) {
  const line = lines[i];
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || LIST_ITEM.test(line) || /^\s*>/.test(line) || isTableStart(lines, i);
}

function readBlock(lines, i) {
  const line = lines[i];
  if (FENCE.test(line)) return readFence(lines, i);
  const heading = HEADING.exec(line);
  if (heading) return [el(`h${Math.min(MAX_HEADING, heading[1].length + HEADING_OFFSET)}`, {}, inline(heading[2])), i + 1];
  if (RULE.test(line)) return [el("hr"), i + 1];
  if (isTableStart(lines, i)) return readTable(lines, i);
  if (LIST_ITEM.test(line)) return readList(lines, i);
  if (/^\s*>/.test(line)) return readQuote(lines, i);
  return readParagraph(lines, i);
}

function markdownBlocks(lines) {
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    if (!lines[i].trim()) {
      i += 1;
      continue;
    }
    const [node, next] = readBlock(lines, i);
    blocks.push(node);
    i = Math.max(next, i + 1);
  }
  return blocks;
}

function markdown(text) {
  return el("div", { className: "markdown" }, markdownBlocks(String(text || "").replace(/\r\n?/g, "\n").split("\n")));
}

function frontMatterTable(fields) {
  if (!fields || !fields.length) return null;
  return el("div", { className: "table-wrap front-matter" }, el("table", { className: "rows compact" }, el("tbody", {}, fields.map(([key, value]) => el("tr", {}, el("th", { scope: "row" }, key), el("td", {}, value))))));
}

// ---- Impact -----------------------------------------------------------------------------------
// What bosun changed (docs/design/impact.md), from the snapshot `bosun impact collect`
// writes: delivery and reviews against my own baseline, leverage and cost. Estimates carry an
// "estimate" badge and a range; every number says how many weeks or items it rests on.

const IMPACT_CHART = { width: 640, height: 200, axis: 22, left: 34, top: 18, gap: 0.25 };
const INTERNALS_KEY = "internals"; // #/impact/internals

function renderImpact(key) {
  const title = key === INTERNALS_KEY ? "Bosun internals" : "Impact";
  main().replaceChildren(pageHead(title), el("div", { id: "impact" }, el("p", { className: "placeholder" }, "Loading...")));
  loadImpact(key);
}

async function loadImpact(key) {
  const target = document.getElementById("impact");
  if (!target) return;
  try {
    const m = await api("/api/impact");
    target.replaceChildren(...(key === INTERNALS_KEY ? internalsPage(m) : impactPage(m)));
  } catch (error) {
    showError(target, error);
  }
}

function weekLabel(week) {
  const moment = new Date(`${week}T00:00:00`);
  return Number.isNaN(moment.getTime()) ? week : moment.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function estimateBadge(title) {
  return el("span", { className: "badge estimate", title }, "estimate");
}

function signedPercent(change) {
  if (change === null || change === undefined) return "";
  const percent = Math.round(change * 100);
  return `${percent > 0 ? "+" : ""}${percent}%`;
}

function sinceVsBaseline(c, unit) {
  const b = c.baseline;
  const band = b.p25 === null ? "" : ` (middle half ${b.p25}-${b.p75})`;
  const since = `${plural(c.since.weeks, "week")} since${c.since.partial_only ? ", this week so far" : ""}`;
  return el(
    "div",
    { className: "muted small" },
    `baseline ${b.mean ?? "n/a"} ${unit}${band} over ${plural(b.weeks, "week")}; ${since}`,
    c.change === null ? null : el("span", { className: `change ${c.change >= 0 ? "up" : "down"}` }, ` ${signedPercent(c.change)}`),
  );
}

function medianPair(pair, what) {
  const fmt = (m) => (m.median === null ? "n/a" : `${m.median} h`);
  return `${what}: ${fmt(pair.since)} since (n=${pair.since.n}), ${fmt(pair.baseline)} before (n=${pair.baseline.n})`;
}

function impactTiles(m) {
  const lev = m.since.leverage;
  const touch = lev.touch;
  const levValue = lev.estimate === null ? "n/a" : `${lev.estimate}x`;
  const assumption = `Agent busy hours over my touch time: each prompt I typed counted as ${m.prompt_minutes} minutes (range: half to double).`;
  return el(
    "div",
    { className: "tiles impact-tiles" },
    tile("PRs merged per week", String(m.prs.since.mean ?? "n/a"), sinceVsBaseline(m.prs, "a week")),
    tile("reviews given per week", String(m.reviews.since.mean ?? "n/a"), sinceVsBaseline(m.reviews, "a week")),
    tile(
      "leverage: agent hours per hour of mine",
      levValue,
      el("div", { className: "muted small" }, estimateBadge(assumption), ` ${lev.low ?? "?"}-${lev.high ?? "?"}x; ${lev.agent_hours} agent h, ${touch.low}-${touch.high} h of mine`),
    ),
    tile(
      "cost per week",
      m.since.cost_per_week === null ? "n/a" : usd(m.since.cost_per_week),
      el("div", { className: "muted small" }, estimateBadge("Tokens from the transcripts at list prices; not a bill."), ` ${usd(m.since.cost)} since the start; ${m.since.cost_per_pr === null ? "n/a" : usd(m.since.cost_per_pr)} per merged PR`),
    ),
  );
}

function impactNotes(m) {
  const notes = [];
  if (m.early) notes.push("Early data: fewer than 4 full weeks since the start, so the comparison can still swing a lot.");
  if (m.stale) notes.push(`The snapshot is from ${when(m.collected_at)}; run bosun impact collect for fresh numbers.`);
  if (m.unpriced_models.length) notes.push(`Priced at the highest list price (not in the price table): ${m.unpriced_models.join(", ")}.`);
  notes.push("A heavy project week, holidays or a change in team load move these numbers as much as bosun does: read single weeks with that in mind.");
  return card("impact-notes", el("ul", { className: "plain small" }, notes.map((text) => el("li", {}, text))));
}

// One week's bar of one series. A single series is coloured by phase (before or since the
// start); several series sit side by side in the week's slot, each in its own series colour.
function impactBar(w, i, x, y, step, series, j, count) {
  const value = series.values[i];
  const width = (step * (1 - IMPACT_CHART.gap)) / count;
  const height = Math.max(0, IMPACT_CHART.height - IMPACT_CHART.axis - y(value));
  const classes = ["impact-bar", count > 1 ? `series-${j}` : w.phase, w.partial ? "partial" : ""].join(" ").trim();
  const label = count > 1 ? `${series.name}, week of ${weekLabel(w.week)}` : weekLabel(w.week);
  return svg("rect", { class: classes, x: (x(i) + j * width).toFixed(1), y: y(value).toFixed(1), width: width.toFixed(1), height: height.toFixed(1) }, svg("title", {}, `${label}: ${value}${w.partial ? " (so far)" : ""}`));
}

// A chart's values as series: a plain list of numbers is one series named after the chart.
function asSeries(title, values) {
  return values.length && typeof values[0] === "object" ? values : [{ name: title, values }];
}

// Weekly bars with the baseline's middle half as a band and its mean as a line across the
// baseline weeks, and a dashed line where bosun started.
function weeklyMarks(width, weeks, series, compare) {
  const { height, axis, left, top } = IMPACT_CHART;
  const peak = Math.max(1, ...series.flatMap((one) => one.values), compare ? compare.baseline.p75 || 0 : 0);
  const step = (width - left) / Math.max(1, weeks.length);
  const x = (i) => left + i * step;
  const y = (v) => top + (height - axis - top) * (1 - v / peak);
  const firstSince = weeks.findIndex((w) => w.phase === "since");
  const baseEnd = firstSince < 0 ? weeks.length : firstSince;
  const marks = [svg("line", { class: "axis", x1: left, x2: width, y1: height - axis, y2: height - axis })];
  if (compare && compare.baseline.p25 !== null && baseEnd > 0) {
    marks.push(svg("rect", { class: "baseline-band", x: left, y: y(compare.baseline.p75).toFixed(1), width: (baseEnd * step).toFixed(1), height: Math.max(1, y(compare.baseline.p25) - y(compare.baseline.p75)).toFixed(1) }));
    marks.push(svg("line", { class: "baseline-mean", x1: left, x2: (left + baseEnd * step).toFixed(1), y1: y(compare.baseline.mean).toFixed(1), y2: y(compare.baseline.mean).toFixed(1) }));
  }
  weeks.forEach((w, i) => series.forEach((one, j) => marks.push(impactBar(w, i, x, y, step, one, j, series.length))));
  if (firstSince > 0) { // a start line only between baseline and since weeks
    const at = (x(firstSince) - (step * IMPACT_CHART.gap) / 2).toFixed(1);
    marks.push(svg("line", { class: "start-line", x1: at, x2: at, y1: top - 8, y2: height - axis }), svg("text", { class: "start-label", x: (Number(at) + 4).toFixed(1), y: top - 6 }, "bosun"));
  }
  const every = Math.ceil(weeks.length / Math.max(1, Math.floor(width / 70)));
  weeks.forEach((w, i) => {
    if (i % every === 0) marks.push(svg("text", { class: "tick mid", x: (x(i) + (step * (1 - IMPACT_CHART.gap)) / 2).toFixed(1), y: height - 6 }, weekLabel(w.week)));
  });
  marks.push(svg("text", { class: "tick", x: 0, y: top + 4 }, String(rounded(peak))));
  return marks;
}

// <values>: the weekly numbers, or several series ({name, values}) drawn side by side with a
// legend; <summary> replaces the default caption.
function weeklyChart(title, weeks, values, compare, summaryText) {
  const series = asSeries(title, values);
  const summary = summaryText || (compare
    ? `${title}: ${compare.since.mean ?? "n/a"} a week since the start against ${compare.baseline.mean ?? "n/a"} before.`
    : `${title}: ${rounded(sum(series[0].values))} in ${plural(weeks.length, "week")}.`);
  const table = dataTable(["Week", "Phase", ...series.map((one) => one.name)], weeks.map((w, i) => [weekLabel(w.week), `${w.phase}${w.partial ? " (so far)" : ""}`, ...series.map((one) => String(one.values[i]))]));
  const chart = fluidChart(title, summary, (width) => weeklyMarks(width, weeks, series, compare));
  return chartFigure(chart, summary, table, series.map((one) => one.name));
}

// ---- Impact: your time and agent time ----------------------------------------------------------

function timeExplained(m) {
  return `Agent busy hours: the time agent sessions were working. Your touch time: an estimate, each prompt you typed counted as ${m.prompt_minutes} minutes (range: half to double). Each pair of bars is one week, labelled by its Monday.`;
}

function leverageRange(l) {
  return `${l.estimate}x (range ${l.low}-${l.high}x)`;
}

function leverageLines(m) {
  const { baseline: b, since: s } = m.leverage;
  const sinceLine = s.estimate === null
    ? "No prompts recorded in a full week since the start yet."
    : `Since bosun started: for every hour you spent prompting, agents worked about ${leverageRange(s)}; ${s.agent_per_week} agent hours a week against ${s.touch_per_week} of yours (${plural(s.weeks, "full week")}).`;
  const beforeLine = b.estimate === null
    ? "Before: no Claude Code transcripts from before the start, so there is no before figure."
    : `Before, in your own Claude Code sessions: ${leverageRange(b)}; ${b.agent_per_week} agent hours a week against ${b.touch_per_week} of yours (${plural(b.weeks, "week")} with transcripts out of ${b.of_weeks}; Claude Code removes old transcripts, so earlier weeks are missing).`;
  const lines = [sinceLine, beforeLine];
  if (s.agent_per_week && b.agent_per_week && b.touch_per_week) {
    const times = (now, then) => `${Math.round((now / then) * 10) / 10}x`;
    lines.push(`Since against before, a week: ${times(s.agent_per_week, b.agent_per_week)} the agent hours, for ${times(s.touch_per_week, b.touch_per_week)} the time you spend prompting.`);
  }
  return lines;
}

function timeSection(m) {
  const first = m.leverage.baseline.first_week || m.leverage.since.first_week;
  const shown = first ? m.weeks.filter((w) => w.week >= first) : m.weeks.filter((w) => w.phase === "since");
  const series = [
    { name: "Agent busy hours", values: shown.map((w) => w.agent_hours) },
    { name: "Your touch time (h)", values: shown.map((w) => w.touch_hours) },
  ];
  const perWeek = shown.filter((w) => w.leverage.estimate !== null).map((w) => `${weekLabel(w.week)} ${w.leverage.estimate}x${w.partial ? " (so far)" : ""}`);
  const summary = `Agent busy hours and your touch time per week, from the week of ${shown.length ? weekLabel(shown[0].week) : "n/a"}.`;
  return section(
    "Your time and agent time",
    el("ul", { className: "plain impact-headline" }, leverageLines(m).map((line) => el("li", {}, estimateBadge(timeExplained(m)), ` ${line}`))),
    weeklyChart("Hours per week", shown, series, null, summary),
    perWeek.length ? el("p", { className: "muted small" }, `Agent hours per hour of yours, week by week: ${perWeek.join(" · ")}`) : null,
    el("p", { className: "muted small" }, timeExplained(m)),
  );
}

// ---- Impact: what learning has produced --------------------------------------------------------

const NOT_MEASURED = "Not measured yet: when a session uses a knowledge-base fact or a memory note, so no time saved is estimated here.";

function stateCounts(counts) {
  const entries = Object.entries(counts || {});
  return entries.length ? entries.map(([state, n]) => `${n} ${state}`).join(", ") : "none yet";
}

function learningPairs(l) {
  const r = l.runs;
  const k = l.knowledge;
  return pairs([
    ["Learning runs", `${plural(r.runs, "run")}, ${plural(r.dry_runs, "dry run")}${r.last ? `; last ${when(r.last)}` : ""}; ${usd(r.cost)} in all`],
    ["Findings reported", String(r.findings)],
    ["Changes proposed", stateCounts(l.proposals)],
    ["Knowledge base", k.exists ? `${plural(k.facts, "fact")} about ${plural(k.entities, "thing")}: ${stateCounts(k.by_status)}` : k.error || "none yet"],
  ]);
}

function kbGrowth(k) {
  if (!k.exists || !k.weeks.length) return null;
  return plainTable(["Week of", "Facts added", "Total"], [...k.weeks].reverse().map((w) => [weekLabel(w.week), String(w.added), String(w.total)]));
}

function recurringList(l) {
  if (!l.recurring.length) return el("p", { className: "muted small" }, "No finding has come back in a later run yet.");
  return [
    el("h3", {}, "Findings that came back"),
    el("ul", { className: "plain small" }, l.recurring.map((f) => el("li", {}, `${f.title} `, el("span", { className: "muted" }, `(in ${f.runs} runs, ${when(f.first)} to ${when(f.last)})`)))),
  ];
}

function learningSection(l) {
  if (!l) return section("What learning has produced", el("p", { className: "placeholder" }, "No learning data in this view yet."));
  return section("What learning has produced", learningPairs(l), recurringList(l), kbGrowth(l.knowledge), el("p", { className: "muted small" }, NOT_MEASURED));
}

function weekDetails(w) {
  const prs = w.pr_links.map((p) => el("li", {}, externalLink(p.url, p.title), el("span", { className: "muted small" }, ` ${p.repo}`)));
  const reviews = w.review_links.map((p) => el("li", {}, externalLink(p.url, p.title)));
  return el(
    "details",
    { className: "impact-week" },
    el("summary", {}, `${weekLabel(w.week)}${w.partial ? " (so far)" : ""}: ${plural(w.prs, "PR")} merged, ${plural(w.reviews, "review")}, ${w.agent_hours} agent h`),
    prs.length ? [el("h4", {}, "Merged"), el("ul", { className: "plain small" }, prs)] : null,
    reviews.length ? [el("h4", {}, "Reviewed"), el("ul", { className: "plain small" }, reviews)] : null,
  );
}

function impactEmpty() {
  return [
    card(
      "",
      el("p", { className: "placeholder" }, "No snapshot yet. Collect one (GitHub reads only, plus your transcripts), then reload:"),
      copyable("bosun impact collect"),
    ),
  ];
}

function impactPage(m) {
  if (!m.exists) return impactEmpty();
  const weeks = m.weeks;
  const since = weeks.filter((w) => w.phase === "since");
  const windows = m.windows.map((w) => ({ name: w.window, count: w.agent_hours }));
  return [
    el("p", { className: "muted small" }, `Since ${m.start}, against ${m.baseline_from} to then; ${plural(m.repos.length, "repo")}; collected ${when(m.collected_at)}.`),
    impactTiles(m),
    impactNotes(m),
    section("PRs merged per week", weeklyChart("PRs merged", weeks, weeks.map((w) => w.prs), m.prs), el("p", { className: "muted small" }, medianPair(m.prs.merge_hours, "Median time from open to merge"))),
    section("Reviews given per week", weeklyChart("Reviews given", weeks, weeks.map((w) => w.reviews), m.reviews), el("p", { className: "muted small" }, medianPair(m.reviews.first_review_hours, "Median time from a PR's opening to my first review"))),
    el(
      "div",
      { className: "stats-grid" },
      section("Where agent time went", windows.length ? bars("Agent busy hours per window since the start", windows) : el("p", { className: "placeholder" }, "No session activity since the start.")),
      section("Cost per week", weeklyChart("Cost in USD", since, since.map((w) => w.cost)), el("p", { className: "muted small" }, estimateBadge("List prices; not a bill."))),
    ),
    timeSection(m),
    learningSection(m.learning),
    overheadSummary(m.overhead),
    ...taskCostSections(m.tasks),
    section("Week by week", el("div", { className: "impact-weeks" }, [...weeks].reverse().map(weekDetails))),
    el("p", { className: "muted small" }, el("a", { href: `#/impact/${INTERNALS_KEY}` }, "Bosun internals"), ": the overhead fixes before and after, context per window, cost per turn and the guardrails."),
  ];
}

// #/impact/internals: what we watch while developing bosun itself, kept off the Impact page.
function internalsPage(m) {
  if (!m.exists) return impactEmpty();
  return [el("p", { className: "muted small" }, el("a", { href: "#/impact" }, "Back to Impact")), ...overheadSections(m.overhead)];
}

// ---- Impact: overhead and guardrails ----------------------------------------------------------
// What bosun itself costs (lib/impact_overhead.py): every turn's tokens against what started
// it, the context each turn re-read, and the guardrails that show the work did not get worse.

const OVERHEAD_EXPLAINED =
  "Overhead is the turns bosun starts on its own: check-ins, events summaries, background-task notifications, resume prompts, messages between sessions and a site's scheduled prompts. Each re-reads the session's whole conversation, so a long conversation makes even a one-line turn expensive. My requests, tasks handed to a window and subagents count as work.";

function plainTable(columns, rows) {
  const cells = (row) => row.map((cell, i) => (i ? el("td", {}, cell) : el("th", { scope: "row" }, cell)));
  return el(
    "div",
    { className: "table-wrap" },
    el("table", { className: "rows compact" }, el("thead", {}, el("tr", {}, columns.map((name) => el("th", { scope: "col" }, name)))), el("tbody", {}, rows.map((row) => el("tr", {}, cells(row))))),
  );
}

function kTokens(value) {
  return value === null || value === undefined ? "n/a" : `${value}k`;
}

function shareText(share) {
  return share === null || share === undefined ? "n/a" : `${Math.round(share * 100)}%`;
}

function overheadTiles(o) {
  const last = o.last;
  const prev = o.previous;
  const turnCost = (side, cls) => (side.per_turn[cls] === undefined ? "n/a" : usd(side.per_turn[cls]));
  const vs = (text) => el("div", { className: "muted small" }, estimateBadge("Tokens at list prices; not a bill."), ` ${text}`);
  return el(
    "div",
    { className: "tiles impact-tiles" },
    tile("overhead share, last 7 days", shareText(last.share), vs(`${shareText(prev.share)} the 7 days before; ${usd(last.overhead_per_day)} a day`)),
    tile("cost per check-in", turnCost(last, "check-in"), vs(`${turnCost(prev, "check-in")} before; ${last.turns["check-in"] || 0} turns`)),
    tile("cost per notification", turnCost(last, "notification"), vs(`${turnCost(prev, "notification")} before; ${last.turns.notification || 0} turns`)),
    tile("context re-read per turn", kTokens(last.contexts.median_k), el("div", { className: "muted small" }, `median, p90 ${kTokens(last.contexts.p90_k)} tokens (n=${last.contexts.n}); before ${kTokens(prev.contexts.median_k)}`)),
  );
}

function fixComparison(o) {
  if (!o.fix) return null;
  const { before, after } = o.fix;
  const perTurn = (side, cls) => (side.per_turn[cls] === undefined ? "n/a" : usd(side.per_turn[cls]));
  const g = o.guardrails.exists ? o.guardrails.fix : null;
  const rows = [
    ["Cost a day", usd(before.per_day), usd(after.per_day)],
    ["Overhead a day", usd(before.overhead_per_day), usd(after.overhead_per_day)],
    ["Overhead share", shareText(before.share), shareText(after.share)],
    ...["check-in", "notification", "resume", "events"].map((cls) => [`Cost per ${cls} turn`, perTurn(before, cls), perTurn(after, cls)]),
    ["Context re-read, median", kTokens(before.contexts.median_k), kTokens(after.contexts.median_k)],
  ];
  if (g) {
    rows.push(
      ["Tasks reported", String(g.before.tasks_reported), String(g.after.tasks_reported)],
      ["Tasks done", String(g.before.tasks_done), String(g.after.tasks_done)],
      ["Hand-over to report, median h", String(g.before.handover_to_report_hours_median ?? "n/a"), String(g.after.handover_to_report_hours_median ?? "n/a")],
      ["Event delay, median min", String(g.before.event_delay_minutes_median ?? "n/a"), String(g.after.event_delay_minutes_median ?? "n/a")],
      ["Approval failures", String(g.before.approval_failures), String(g.after.approval_failures)],
    );
  }
  const note = `Daily cost over the ${before.days} days before against the ${plural(after.days, "day")} since; the work rows are totals over the same days. Cost should fall; the work rows should hold.${after.days < before.days ? " Early: the after side is still short." : ""}`;
  return section(`Before and after the overhead fixes (${o.fix.pivot})`, el("p", { className: "muted small" }, note), plainTable(["", `Before (${before.days} days)`, `After (${plural(after.days, "day")})`], rows));
}

function overheadChart(o) {
  const present = o.classes.filter((c) => o.weeks.some((w) => w.cost[c.key]));
  const series = present.map((c) => ({ name: `${c.overhead ? "" : "work: "}${c.label}`, values: o.weeks.map((w) => w.cost[c.key] || 0) }));
  return stackedBars("Cost in USD by what started each turn", o.weeks.map((w) => `${weekLabel(w.week)}${w.partial ? " (so far)" : ""}`), series, { unit: "week" });
}

function contextTable(o) {
  const rows = o.windows.map((w) => {
    const latest = w.weeks[w.weeks.length - 1];
    const earlier = w.weeks.length > 1 ? w.weeks[w.weeks.length - 2] : null;
    return [w.window, kTokens(latest.median_k), kTokens(latest.p90_k), String(latest.n), earlier ? kTokens(earlier.p90_k) : "n/a"];
  });
  return rows.length ? plainTable(["Window", "Median", "p90", "Turns", "p90 the week before"], rows) : el("p", { className: "placeholder" }, "No turns recorded yet.");
}

function guardrailTable(g) {
  if (!g.exists) return el("p", { className: "placeholder" }, "No guardrail data in this snapshot yet.");
  const rows = [...g.weeks].reverse().map((w) => [
    `${weekLabel(w.week)}${w.partial ? " (so far)" : ""}`,
    String(w.tasks_reported),
    String(w.tasks_done),
    String(w.handover_to_report_hours_median ?? "n/a"),
    String(w.approval_failures),
    `${w.outreach_replied} / ${w.outreach_dropped}`,
    `${w.events_delivered} / ${w.events_queued}`,
    String(w.event_delay_minutes_median ?? "n/a"),
    String(w.events_pending_end_max),
    `${w.checkins_delivered} / ${w.checkins_skipped_nothing_new}`,
  ]);
  return plainTable(["Week", "Tasks reported", "Tasks done", "Hand-over to report (h)", "Approval failures", "Outreach replied / dropped", "Events delivered / queued", "Event delay (min)", "Pending at day end (max)", "Check-ins sent / skipped as nothing new"], rows);
}

// The Impact page's overhead card: what bosun itself costs, against the work.
function overheadSummary(o) {
  if (!o || !o.exists) {
    return section("Overhead", el("p", { className: "placeholder" }, "This snapshot has no turn data yet; collect again to see what bosun itself costs:"), copyable("bosun impact collect"));
  }
  return section("Overhead: what bosun itself costs", el("p", { className: "muted small" }, OVERHEAD_EXPLAINED), overheadTiles(o), overheadChart(o));
}

// The internals page: the overhead fixes before and after, contexts, cost per turn, guardrails.
function overheadSections(o) {
  if (!o || !o.exists) return [overheadSummary(o)];
  return [
    fixComparison(o),
    el(
      "div",
      { className: "stats-grid" },
      section("Context re-read per turn, by window", el("p", { className: "muted small" }, "Thousands of tokens each turn re-reads, in the latest week. A long conversation makes every turn in that window expensive."), contextTable(o)),
      section("Cost per turn, by week", plainTable(["Week", "Check-in", "Notification", "Resume", "Events"], [...o.weeks].reverse().map((w) => [weekLabel(w.week), ...["check-in", "notification", "resume", "events"].map((c) => (w.per_turn[c] === undefined ? "n/a" : usd(w.per_turn[c])))]))),
    ),
    section("Guardrails: the work, week by week", el("p", { className: "muted small" }, "A lower cost only counts if these hold: tasks keep getting reported and done, hand-overs and events are not slower, and nothing piles up."), guardrailTable(o.guardrails)),
  ].filter(Boolean);
}

// ---- Impact: cost per task ---------------------------------------------------------------------
// What each ledger item cost, its subagents included (lib/impact_tasks.py): by kind, task type and
// outcome, the most expensive items next to what they produced, and what no item claimed.

const TASKS_EXPLAINED =
  "Each turn counts for the ledger item it worked on: the item a hand-over names, else the item the window reported on next, else the window's active item, as long as its last ledger event is recent enough. A subagent counts for its parent's item. Turns no item claims (the orchestrator's coordination, work outside the ledger) stay unattributed, per window.";

function taskTiles(t) {
  const note = (text) => el("div", { className: "muted small" }, estimateBadge("Tokens at list prices; not a bill."), ` ${text}`);
  return el(
    "div",
    { className: "tiles impact-tiles" },
    tile("cost since the start", usd(t.total), note(`${plural(t.items, "item")}`)),
    tile("attributed to an item", shareText(t.attributed_share), note(`${usd(t.attributed)}; by hand-over ${usd(t.rules.handover)}, report ${usd(t.rules.report)}, active item ${usd(t.rules.active)}`)),
    tile("in subagents", shareText(t.subagent_share), note("of the attributed cost")),
  );
}

function taskGroupTable(groups, label) {
  const rows = groups.map((g) => [g.key || "none", String(g.items), usd(g.cost), usd(g.median), shareText(g.subagent_share)]);
  return rows.length ? plainTable([label, "Items", "Cost", "Median per item", "In subagents"], rows) : el("p", { className: "placeholder" }, "No items yet.");
}

function topTaskTable(top) {
  const rows = top.map((i) => [
    itemLink(i.id, i.title || i.id),
    `${i.kind}${i.task ? ` / ${i.task}` : ""}`,
    i.outcome,
    i.pr ? externalLink(i.pr, "PR") : "none",
    usd(i.cost),
    usd(i.subagent_cost),
    String(i.turns),
  ]);
  return plainTable(["Item", "Kind / task", "Outcome", "Produced", "Cost", "In subagents", "Turns"], rows);
}

function unpricedNote(t) {
  if (!t.unpriced.length) return null;
  return el("p", { className: "muted small" }, `Not in the price table, priced as ${t.fallback_model}: ${t.unpriced.join(", ")}.`);
}

function taskCostSections(t) {
  if (!t || !t.exists) {
    return [section("Cost per task", el("p", { className: "placeholder" }, "No turns attributed yet: hand work to a window or report on a ledger item, then collect again:"), copyable("bosun impact collect"))];
  }
  const rest = t.unattributed.map((r) => [r.window, String(r.turns), usd(r.cost)]);
  return [
    section("Cost per task", el("p", { className: "muted small" }, `${TASKS_EXPLAINED} Recent enough: within ${t.horizon_hours} hours.`), taskTiles(t), unpricedNote(t)),
    el(
      "div",
      { className: "stats-grid" },
      section("By outcome", taskGroupTable(t.by_outcome, "Outcome")),
      section("By task type", taskGroupTable(t.by_task, "Task type")),
      section("By kind", taskGroupTable(t.by_kind, "Kind")),
      section("Unattributed, by window", rest.length ? plainTable(["Window", "Turns", "Cost"], rest) : el("p", { className: "placeholder" }, "Every turn went to an item.")),
    ),
    section(`The ${plural(t.top.length, "most expensive item")}`, topTaskTable(t.top)),
  ].filter(Boolean);
}

// ---- Learning ---------------------------------------------------------------------------------

function renderLearning(current) {
  if (current.key) {
    renderReport(current.key);
    return;
  }
  main().replaceChildren(pageHead("Improvements"), el("div", { id: "learning" }, el("p", { className: "placeholder" }, "Loading...")));
  loadLearning();
}

async function loadLearning() {
  const target = document.getElementById("learning");
  if (!target) return;
  try {
    target.replaceChildren(...learningPage(await api("/api/learn")));
  } catch (error) {
    showError(target, error);
  }
}

function reportCell(run) {
  return run.report ? el("a", { href: `#/learning/report/${encodeURIComponent(run.report)}` }, "report") : el("span", { className: "muted" }, run.report_note || "");
}

function learnRunsCard(model) {
  const head = ["Started", "Kind", "Outcome", "Findings", "Proposals", "Cost", "Report"];
  const rows = model.runs.map((run) =>
    el(
      "tr",
      {},
      el("td", {}, when(run.started)),
      el("td", {}, run.dry_run ? badge("dry run") : badge("live", "ok")),
      el("td", {}, run.outcome === "failed" ? el("span", { className: "problem" }, `failed${run.error ? `: ${run.error}` : ""}`) : run.outcome || ""),
      el("td", {}, run.findings === null || run.findings === undefined ? "" : String(run.findings)),
      el("td", {}, run.proposals.map((id, i) => [i ? ", " : "", itemLink(id)])),
      el("td", {}, typeof run.cost === "number" ? usd(run.cost) : ""),
      el("td", {}, reportCell(run)),
    ),
  );
  return section(
    "Runs",
    model.running ? el("p", { className: "muted small" }, `Running since ${when(model.running.started)}.`) : null,
    rows.length
      ? el("div", { className: "table-wrap" }, el("table", { className: "rows" }, el("thead", {}, el("tr", {}, head.map((h) => el("th", { scope: "col" }, h)))), el("tbody", {}, rows)))
      : el("p", { className: "placeholder" }, "No runs yet."),
  );
}

function settledText(s) {
  return s ? `${s.state} ${when(s.at)}${s.note ? `: ${s.note}` : ""}` : "";
}

function proposalCard(item) {
  const change = item.change;
  return card(
    `learn-item ${item.pending ? "state-waiting" : ""}`.trim(),
    el("div", { className: "card-head" }, el("h3", {}, item.title || item.id), item.pending ? badge("waiting for you", "needs-me") : badge(item.state)),
    pairs([
      ["item", itemLink(item.id)],
      ["finding", item.key ? el("code", {}, item.key) : ""],
      ["category", item.category],
      ["evidence", item.evidence],
      ["why", item.why],
      ["change", change ? [badge(change.target, "kind"), change.window ? ` @${change.window}` : "", el("pre", {}, change.text || "")] : item.change_text],
      ["status", item.settled ? settledText(item.settled) : item.state],
      ["proposed", when(item.created)],
    ]),
    item.action ? el("details", {}, el("summary", {}, "The proposal as sent"), el("pre", { className: "action" }, item.action)) : null,
  );
}

function findingsCard(model) {
  const counts = Object.entries(model.statuses).map(([status, n]) => badge(`${n} ${status}`));
  const rows = model.findings.map((f) =>
    el("tr", {}, el("td", {}, el("code", {}, f.key)), el("td", {}, f.title || ""), el("td", {}, f.category || ""), el("td", {}, f.status || ""), el("td", {}, String(f.times_seen)), el("td", {}, when(f.first_seen)), el("td", {}, when(f.last_seen))),
  );
  const head = ["Key", "Title", "Category", "Status", "Seen", "First", "Last"];
  return section(
    "Findings remembered",
    el("div", { className: "summary-chips" }, counts),
    rows.length
      ? el("div", { className: "table-wrap" }, el("table", { className: "rows compact" }, el("thead", {}, el("tr", {}, head.map((h) => el("th", { scope: "col" }, h)))), el("tbody", {}, rows)))
      : el("p", { className: "placeholder" }, "None yet."),
  );
}

function learningPage(model) {
  return [
    model.error ? card("", el("p", { className: "problem" }, model.error)) : null,
    learnRunsCard(model),
    el("h2", { className: "section-title" }, `Proposals (${model.items.length})`),
    model.items.length ? el("div", { className: "stack" }, model.items.map(proposalCard)) : card("", el("p", { className: "placeholder" }, "No learn: items in the ledger.")),
    findingsCard(model),
  ].filter(Boolean);
}

async function renderReport(key) {
  const back = el("a", { className: "back", href: "#/learning" }, "← Learning");
  main().replaceChildren(back, el("p", { className: "placeholder" }, "Loading..."));
  try {
    const report = await api(`/api/learn/report/${encodeURIComponent(key)}`);
    main().replaceChildren(
      back,
      pageHead(`Learning report${report.dry_run ? " (dry run)" : ""}`, el("span", { className: "muted small" }, when(report.started))),
      card("", report.error ? el("p", { className: "problem" }, report.error) : markdown(report.body)),
    );
  } catch (error) {
    main().replaceChildren(back, card("", el("p", { className: "problem" }, error.status === 404 ? "No such report (it may have been pruned)." : error.message)));
  }
}

// ---- Knowledge --------------------------------------------------------------------------------
// The knowledge base by axis, entity pages (facts, history, backlinks, related ledger items, a
// process's flow, the neighbourhood graph) and the review batch. Entities are addressed by opaque
// keys; sources link only when they are http(s) URLs or ledger items.

function entityCount(count) {
  return count === 1 ? "1 entity" : `${count} entities`;
}

function entityHref(key) {
  return `#/knowledge/${encodeURIComponent(key)}`;
}

function entityLink(ref) {
  if (!ref) return "";
  return ref.key ? el("a", { href: entityHref(ref.key) }, ref.name) : el("span", { className: "mono" }, ref.name);
}

function cardClass(card) {
  return `axis-${KG_CARDS.includes(card) ? card : "product"}`;
}

function kgStatus(status) {
  return badge(status, KG_STATUS_BADGES[status] || "");
}

function authorityBadge(fact) {
  const label = KG_AUTHORITIES.includes(fact.authority_label) ? fact.authority_label : "observed";
  return el("span", { className: `badge authority-${label}`, title: `authority: ${fact.authority || label}` }, fact.authority_label);
}

// A long fact text in a list: a few lines, then "Show more" to read all of it (the server sends
// the whole text; a reviewer must be able to see the whole fact).
const CLAMP_CHARS = 180;

function expandableText(text) {
  if (!text || text.length <= CLAMP_CHARS) return el("span", {}, text || "");
  const body = el("span", { className: "kg-clamp" }, text);
  const toggle = el("button", { type: "button", className: "kg-show-more", "aria-expanded": "false" }, "Show more");
  toggle.addEventListener("click", () => {
    const open = !body.classList.contains("open");
    body.classList.toggle("open", open);
    toggle.textContent = open ? "Show less" : "Show more";
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
  return el("span", { className: "kg-expandable" }, body, toggle);
}

function factTarget(fact) {
  return [
    fact.object ? entityLink(fact.object) : el("span", { className: "kg-value" }, expandableText(fact.value || "")),
    fact.order ? el("span", { className: "muted" }, ` (step ${fact.order})`) : null,
  ];
}

function capitalized(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// What a fact says, in plain words: the server's sentence parts (its predicate's sentence, from
// the knowledge schema), entities as links, a long value clamped with Show more. <subject>: start
// with the entity it is about, for lists that mix entities; without it (a block names the entity)
// the words start the line. One renderer for the review, the card stack and the other lists.
function sentenceParts(fact) {
  return fact.sentence.map((p) => (p.entity ? entityLink(p.entity) : p.value !== undefined ? el("span", { className: "kg-value" }, expandableText(p.value)) : p.text));
}

function factSentence(fact, subject) {
  if (!fact.sentence) return [subject ? [entityLink(fact.subject), " "] : null, el("span", { className: "muted" }, fact.predicate), " ", factTarget(fact)];
  const parts = sentenceParts(fact);
  if (subject) return [entityLink(fact.subject), " ", parts];
  if (typeof parts[0] === "string") parts[0] = capitalized(parts[0]);
  return parts;
}

const REWORK_HINT = "It records something that had to be rediscovered or redone before: confirming it first saves that work next time.";

// What to know about a fact beyond its words: who stands behind it unless it was plainly observed,
// its status unless the list is all one status (the review: all proposed), and "prevents rework".
function factBadges(fact, sameStatus) {
  return [
    fact.authority_label && fact.authority_label !== "observed" ? [" ", authorityBadge(fact)] : null,
    sameStatus ? null : [" ", kgStatus(fact.status)],
    fact.prevents_rework ? [" ", el("span", { className: "badge warn", title: REWORK_HINT }, "prevents rework")] : null,
  ];
}

function sourceTarget(s) {
  const label = s.label || s.ref;
  if (s.url) return externalLink(s.url, label);
  if (s.item) return itemLink(s.item, label);
  if (s.library) return el("a", { href: `#/library/${encodeURIComponent(s.library)}` }, label);
  return el("span", {}, label);
}

// Where a fact came from, in words: what kind of source, its name (a memory note's title, a
// session's name and time, a link when there is one), and the note's summary when known: once per
// list of facts that share <described> (a review block), not under every fact citing the note.
function sourceItem(s, described) {
  const detail = s.detail && !(described && described.has(s.library || s.ref)) ? s.detail : "";
  if (detail && described) described.add(s.library || s.ref);
  const verified = s.verified === true ? badge("quote verified", "ok") : s.verified === false ? badge("quote not verified", "warn") : null;
  return el(
    "li",
    { className: s.trusted ? "" : "untrusted", title: s.trusted ? s.ref : `untrusted source (${s.ref}): never confirms a fact on its own` },
    el("span", { className: "muted" }, `${s.kind_label || s.kind}: `),
    sourceTarget(s),
    s.quote ? [" ", el("q", {}, s.quote)] : null,
    verified ? [" ", verified] : null,
    detail ? el("div", { className: "muted small kg-source-detail" }, detail) : null,
  );
}

function sourcesList(sources, described) {
  return sources.length ? el("ul", { className: "kg-sources" }, sources.map((s) => sourceItem(s, described))) : null;
}

function factDates(fact) {
  const parts = [fact.id, `observed ${when(fact.observed_at)}`, `valid from ${when(fact.valid_from)}`];
  if (fact.valid_to) parts.push(`until ${when(fact.valid_to)}`);
  if (fact.observations > 1) parts.push(`seen ${fact.observations} times`);
  if (fact.reviewed_by) parts.push(`reviewed in the ${fact.reviewed_by.ref} ${when(fact.reviewed_by.at)}`);
  return el("div", { className: "muted small" }, parts.join(" · "));
}

// One fact: what it says, who stands behind it, its status, dates and sources. <subject>: also
// name the entity it is about (lists that mix entities).
function factItem(fact, subject) {
  return el(
    "li",
    { className: `kg-fact status-${fact.status}` },
    el(
      "div",
      { className: "kg-fact-line" },
      subject ? factSentence(fact, true) : factTarget(fact),
      factBadges(fact, false),
    ),
    fact.replaced_by ? el("div", { className: "small" }, `replaced by ${fact.replaced_by.id}: ${fact.replaced_by.text} (${fact.replaced_by.status})`) : null,
    factDates(fact),
    sourcesList(fact.sources),
  );
}

function renderKnowledge(current) {
  if (current.key === REVIEW_KEY) {
    renderReview();
    return;
  }
  if (current.key) {
    renderEntity(current.key, false);
    return;
  }
  const tab = kgTab(current.params);
  main().replaceChildren(
    ...present(
      pageHead("Knowledge Graph", el("span", { className: "muted small" }, "People, process and product, with sources")),
      kgTabs(tab),
      tab === "raw" ? card("", kgSearchForm(current.params)) : null,
      el("div", { id: "knowledge", className: `kg-view kg-view-${tab}` }, el("p", { className: "placeholder" }, "Loading...")),
    ),
  );
  if (tab === "raw") loadKnowledge(current.params);
  else if (tab === "constellation") loadConstellation();
  else loadBriefing();
}

function kgParams(params) {
  const found = new URLSearchParams();
  for (const name of ["q", "type"]) if (params.get(name)) found.set(name, params.get(name));
  return found;
}

function kgSearchForm(params) {
  const q = el("input", { type: "search", name: "q", id: "kg-q", placeholder: "Names, aliases and fact text", value: params.get("q") || "" });
  const type = el("select", { name: "type", id: "kg-type" });
  options(type, [["", "All types"]], params.get("type") || "");
  const changed = () => {
    const chosen = new URLSearchParams({ view: "raw" });
    if (q.value.trim()) chosen.set("q", q.value.trim());
    if (type.value) chosen.set("type", type.value);
    history.replaceState(null, "", `#/knowledge?${chosen}`);
    loadKnowledge(chosen);
  };
  q.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(changed, SEARCH_DELAY_MS);
  });
  type.addEventListener("change", changed);
  return el("form", { className: "filters kg-search", role: "search", onsubmit: (event) => event.preventDefault() }, control("Search", q), control("Type", type));
}

async function loadKnowledge(params) {
  const target = document.getElementById("knowledge");
  if (!target) return;
  const query = kgParams(params).toString();
  try {
    const model = await api(`/api/knowledge${query ? `?${query}` : ""}`);
    const type = document.getElementById("kg-type");
    if (type) options(type, [["", "All types"], ...model.types.map((t) => [t, t])], params.get("type") || "");
    target.replaceChildren(...knowledgePage(model));
  } catch (error) {
    showError(target, error);
  }
}

function emptyKnowledge(model) {
  if (model.error) return card("", el("p", { className: "problem" }, model.error));
  return card(
    "",
    el("p", { className: "placeholder" }, model.hint),
    copyable("bosun kg import <file> --apply"),
    el("div", { className: "spaced" }, copyable("bosun kg propose <subject> <predicate> [<object>] --value ... --source kind:ref")),
  );
}

function knowledgePage(model) {
  if (!model.exists) return [emptyKnowledge(model)];
  const totals = model.totals;
  return [
    el("p", { className: "muted small" }, `${entityCount(totals.entities)}, ${plural(totals.facts, "fact")}: ${totals.confirmed || 0} confirmed, ${totals.proposed || 0} proposed.`),
    reviewSummaryCard(model.review),
    searchResults(model.search),
    el("div", { className: "kg-cards" }, model.cards.map(axisCard)),
  ].filter(Boolean);
}

function reviewSummaryCard(s) {
  const later = s.later_groups ? ` ${plural(s.later_groups, "more group")} wait for a later batch.` : "";
  const text = s.groups ? `Today's batch: ${plural(s.groups, "group")}, ${plural(s.facts, "fact")} (${s.waiting} waiting for you).${later}` : "Nothing to review: no proposed facts wait.";
  return card(
    `kg-review-card${s.waiting ? " state-waiting" : ""}`,
    el("div", { className: "card-head" }, el("h2", {}, "Review"), s.waiting ? badge(`${s.waiting} waiting`, "needs-me") : badge("nothing waiting", "ok")),
    el("p", {}, text),
    s.groups ? el("a", { className: "button", href: `#/knowledge/${REVIEW_KEY}` }, "Open the review") : null,
  );
}

function entityEntry(e) {
  const counts = [e.confirmed ? `${e.confirmed} confirmed` : "", e.proposed ? `${e.proposed} proposed` : ""].filter(Boolean).join(" · ");
  return el("li", {}, entityLink(e), " ", badge(e.type, "kind"), counts ? el("span", { className: "muted small" }, ` ${counts}`) : null, e.why ? el("span", { className: "muted small" }, ` (${e.why})`) : null);
}

function searchResults(s) {
  if (!s) return null;
  const nothing = !s.entities.length && !s.facts.length;
  return section(
    "Search results",
    nothing ? el("p", { className: "placeholder" }, "No matches.") : null,
    s.entities.length ? el("ul", { className: "kg-list" }, s.entities.map(entityEntry)) : null,
    s.facts.length ? [el("h3", { className: "spaced" }, "Facts"), el("ul", { className: "kg-facts" }, s.facts.map((f) => factItem(f, true)))] : null,
    s.more ? el("p", { className: "muted small" }, `+${s.more} more: narrow the search.`) : null,
  );
}

function axisCard(c) {
  const recent = c.recent.map((f) => el("li", {}, factSentence(f, true), " ", kgStatus(f.status), el("span", { className: "muted small" }, ` ${ago(f.changed_at)}`)));
  return card(
    `kg-axis ${cardClass(c.name)}`,
    el("div", { className: "card-head" }, el("h3", {}, el("span", { className: "kg-swatch", "aria-hidden": "true" }), c.title), badge(entityCount(c.entities))),
    el("div", { className: "summary-chips" }, badge(`${c.confirmed} confirmed`, "ok"), badge(`${c.proposed} proposed`, c.proposed ? "warn" : "")),
    c.list.length ? el("ul", { className: "kg-list" }, c.list.map(entityEntry)) : el("p", { className: "placeholder" }, "None yet."),
    c.more ? el("p", { className: "muted small" }, `+${c.more} more: search to find them.`) : null,
    recent.length ? [el("h3", { className: "spaced small" }, "Recently changed"), el("ul", { className: "kg-recent" }, recent)] : null,
  );
}

// ---- Knowledge Graph: the tabs ----
// #/knowledge is the Briefing; ?view=constellation and ?view=raw (the overview and entity browser)
// are the other tabs. The Metro map is a prototype only (docs/design/prototypes/), shown disabled.

function kgTab(params) {
  const view = params.get("view") || "";
  return KG_TABS.some(([name]) => name === view) ? view : KG_DEFAULT_TAB;
}

function kgTabHref(name) {
  return name === KG_DEFAULT_TAB ? "#/knowledge" : `#/knowledge?view=${name}`;
}

function kgTabs(active) {
  const tabs = KG_TABS.map(([name, label]) =>
    el("a", { href: kgTabHref(name), className: `kg-tab${name === active ? " active" : ""}`, role: "tab", "aria-selected": name === active ? "true" : "false" }, label),
  );
  tabs.push(el("span", { className: "kg-tab disabled", role: "tab", "aria-disabled": "true", "aria-selected": "false", title: KG_METRO_NOTE }, "Metro (prototype)"));
  return el("div", { className: "kg-tabs", role: "tablist", "aria-label": "Knowledge Graph views" }, tabs);
}

function reducedMotion() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

function percent(share) {
  return `${Math.round((Number(share) || 0) * 100)}%`;
}

// ---- Knowledge Graph: the Briefing ----
// The headline (facts captured per day, the confirmed share, coverage per card, the rediscovery
// placeholder), this week's timeline, today's review as a card stack, and the most connected.

async function loadBriefing() {
  const target = document.getElementById("knowledge");
  if (!target) return;
  try {
    const [model, batch] = await Promise.all([api(BRIEFING_API), api(REVIEW_API)]);
    review.csrf = batch.csrf || "";
    target.replaceChildren(...briefingPage(model, batch));
  } catch (error) {
    showError(target, error);
  }
}

// The call to action over the Briefing: how many facts wait for review, with a button that takes
// you to the card stack (focused, so Y/S/N work at once) and a link to the full review screen.
function reviewCallout(model, batch) {
  const s = batch.summary;
  if (!batch.exists || !s || !s.waiting) return null;
  const proposed = model.totals ? model.totals.proposed : 0;
  const later = proposed > s.waiting ? ` ${proposed - s.waiting} more are queued for later batches.` : "";
  const start = el("button", { type: "button", className: "button primary" }, "Review now");
  start.addEventListener("click", () => {
    const target = document.getElementById("kg-stack");
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
  });
  return el(
    "div",
    { className: "callout kg-review-cta", role: "status" },
    el("p", {}, el("strong", {}, `${plural(s.waiting, "fact")} ${s.waiting === 1 ? "is" : "are"} waiting for your review.`), ` Today's batch has ${plural(s.groups, "group")}.${later}`),
    el("div", { className: "kg-review-cta-actions" }, start, el("a", { href: `#/knowledge/${REVIEW_KEY}` }, "Open the full review")),
  );
}

function briefingPage(model, batch) {
  if (!model.exists) return [emptyKnowledge(model)];
  return [
    reviewCallout(model, batch),
    el("div", { className: "kg-hero" }, capturedTile(model), confirmedTile(model.totals), coverageTile(model.coverage), rediscoveryTile(model.rediscovery)),
    el("div", { className: "kg-brief-grid" }, weekCard(model.week), stackCard(batch)),
    connectedCard(model.connected),
  ].filter(Boolean);
}

function tileHead(title) {
  return el("h2", {}, title);
}

function capturedTile(model) {
  const c = model.captured;
  const total = c.total.length ? c.total[c.total.length - 1] : 0;
  return card(
    "kg-tile",
    tileHead("Facts captured"),
    el("div", { className: "kg-big" }, String(total), el("small", {}, `${c.this_week} this week`)),
    el("div", { className: "kg-spark" }, sparkline(`Facts captured per day, ${c.days[0]} to ${c.days[c.days.length - 1]}`, c.new)),
    el("p", { className: "muted small" }, `Per day over the last ${c.days.length} days${c.first_day ? `; the first on ${c.first_day}` : ""}. ${entityCount(model.totals.entities)}.`),
  );
}

function confirmedTile(t) {
  return card(
    "kg-tile",
    tileHead("Confirmed"),
    el("div", { className: "kg-big" }, percent(t.confirmed_share), el("small", {}, `of ${plural(t.facts, "current fact")}`)),
    meter(t.confirmed, t.facts),
    el("p", { className: "muted small" }, `${t.confirmed} confirmed, ${t.proposed} proposed (not used until you confirm them).`),
  );
}

function ring(row) {
  const { size, stroke } = BRIEF.ring;
  const middle = size / 2;
  const radius = middle - stroke;
  const length = 2 * Math.PI * radius;
  const arc = (length * Math.max(0, Math.min(1, Number(row.share) || 0))).toFixed(1);
  return svg(
    "svg",
    { class: `kg-ring ${cardClass(row.card)}`, viewBox: `0 0 ${size} ${size}`, width: size, height: size, role: "img", "aria-label": `${row.title}: ${row.covered} of ${row.entities} covered` },
    svg("circle", { class: "kg-ring-track", cx: middle, cy: middle, r: radius.toFixed(1) }),
    Number(arc) > 0 ? svg("circle", { class: "kg-ring-arc", cx: middle, cy: middle, r: radius.toFixed(1), "stroke-dasharray": `${arc} ${length.toFixed(1)}`, transform: `rotate(-90 ${middle} ${middle})` }) : null,
    svg("text", { class: "kg-ring-text", x: middle, y: middle + 4 }, percent(row.share)),
  );
}

function coverageTile(rows) {
  return card(
    "kg-tile",
    tileHead("Coverage"),
    el("ul", { className: "kg-rings" }, rows.map((row) => el("li", {}, ring(row), el("span", {}, row.title), el("span", { className: "muted small" }, `${row.covered}/${row.entities}`)))),
    el("p", { className: "muted small" }, "Entities in at least one confirmed fact."),
  );
}

// Not a number: rediscovery needs learning to count rework episodes, which it does not yet.
function rediscoveryTile(r) {
  return card("kg-tile kg-placeholder", tileHead("Rediscovery saved"), el("p", { className: "kg-big muted" }, "Not measured yet"), el("p", { className: "muted small" }, r ? r.note : ""));
}

function briefFact(f) {
  return el(
    "li",
    { className: `kg-brief-fact ${cardClass(f.subject ? f.subject.card : "")}` },
    el("span", { className: "kg-swatch", "aria-hidden": "true" }),
    el("span", {}, factSentence(f, true), factBadges(f, false)),
  );
}

function changeSide(label, s) {
  const named = s.entity && s.text === s.entity.name; // nothing beyond its name: the name is the text
  return el(
    "div",
    { className: `kg-side ${label === "Before" ? "before" : "after"}` },
    el("span", { className: "kg-side-label" }, label),
    named ? null : entityLink(s.entity),
    el("div", { className: "kg-side-text" }, named ? entityLink(s.entity) : s.text),
  );
}

function changeRow(c) {
  return el("li", { className: "kg-change" }, changeSide("Before", c.before), el("span", { className: "kg-arrow", "aria-label": "replaced by" }, "→"), changeSide("Now", c.after));
}

function weekDay(d) {
  const shown = d.facts.slice(0, BRIEF.dayFacts);
  const rest = d.facts.slice(BRIEF.dayFacts);
  return el(
    "li",
    { className: "kg-day" },
    el("h3", {}, dayLabel(d.day), el("span", { className: "muted small" }, ` ${plural(d.count, "fact")} · ${d.new_entities} new ${d.new_entities === 1 ? "entity" : "entities"}`)),
    d.changes.length ? el("ul", { className: "kg-changes" }, d.changes.map(changeRow)) : null,
    shown.length ? el("ul", { className: "kg-brief-facts" }, shown.map(briefFact)) : null,
    rest.length ? el("details", { className: "kg-more" }, el("summary", {}, `Show ${rest.length} more`), el("ul", { className: "kg-brief-facts" }, rest.map(briefFact))) : null,
    d.more ? el("p", { className: "muted small" }, `+${d.more} more not listed: see Raw data.`) : null,
  );
}

function weekCard(w) {
  const days = w.days.map(weekDay);
  return card(
    "kg-week",
    el("div", { className: "card-head" }, el("h2", {}, "This week we learned"), el("span", { className: "muted small" }, `${w.start} to ${w.end}: ${plural(w.facts, "new fact")}, ${plural(w.changes, "change")}`)),
    days.length ? el("ol", { className: "kg-timeline" }, days) : el("p", { className: "placeholder" }, "Nothing new this week."),
  );
}

function miniGraph(g, name) {
  const marks = g.edges.map((e) => {
    const [a, b] = [g.nodes[e.from], g.nodes[e.to]];
    return svg("line", { class: `kg-edge${e.confirmed ? "" : " proposed"}`, x1: a.x, y1: a.y, x2: b.x, y2: b.y });
  });
  for (const n of g.nodes) marks.push(svg("circle", { class: `kg-node ${cardClass(n.card)}${n.depth === 0 ? " center" : ""}`, cx: n.x, cy: n.y, r: n.depth === 0 ? BRIEF.mini.center : BRIEF.mini.node }));
  return svg("svg", { class: "kg-mini", viewBox: `0 0 ${g.width} ${g.height}`, role: "img", "aria-label": `${name} and ${entityCount(g.nodes.length - 1)} it links to` }, marks);
}

function connectedCard(list) {
  if (!list.length) return null;
  const entries = list.map((e) =>
    el(
      "li",
      {},
      el(
        e.key ? "a" : "div",
        { className: `kg-ego ${cardClass(e.card)}`, href: e.key ? entityHref(e.key) : null },
        miniGraph(e.graph, e.name),
        el("span", { className: "kg-ego-name" }, e.name),
        el("span", { className: "muted small" }, `${e.type} · ${plural(e.degree, "link")} · ${plural(e.facts, "fact")}`),
      ),
    ),
  );
  return section("Most connected", el("ul", { className: "kg-strip" }, entries));
}

// ---- the review as a card stack ----
// Today's batch (the same as #/knowledge/review), one fact a card. Y or right arrow confirms, N
// or left arrow rejects, S or down arrow skips (defer: it stays proposed), Z takes back the last
// decision; the keys work only while the stack has focus. Dragging the top card right, left or
// down does the same. A decision is sent STACK.undoMs after it was made, or when the next one is,
// so Z can take it back before anything is written.

const stack = { cards: [], index: 0, results: [], pending: null, sending: 0, saved: 0, answered: false, error: "", note: "", leaving: null, drag: null, parts: null };

function stackCards(batch) {
  return (batch.groups || []).flatMap((g) => g.facts.filter((f) => f.decidable).map((f) => ({ fact: f, group: g.title })));
}

function stackInProgress() {
  return Boolean(stack.pending || stack.sending || stack.drag || (stack.index > 0 && stack.index < stack.cards.length));
}

function stackCard(batch) {
  Object.assign(stack, { cards: batch.exists ? stackCards(batch) : [], index: 0, results: [], pending: null, saved: 0, answered: false, error: "", note: "", leaving: null, drag: null });
  const s = batch.summary;
  const head = el(
    "div",
    { className: "card-head" },
    el("h2", {}, "Today's review"),
    s && s.waiting ? badge(`${s.waiting} waiting`, "needs-me") : badge("nothing waiting", "ok"),
  );
  const box = el("div", { className: "kg-stack", id: "kg-stack", tabindex: "0", role: "group", "aria-label": "Review cards. Keys: Y confirm, S skip, N reject, Z undo" });
  box.addEventListener("keydown", stackKey);
  const area = el("div", { className: "kg-stack-cards", id: "kg-stack-cards" });
  const progress = el("ol", { className: "kg-progress", id: "kg-progress", "aria-hidden": "true" });
  const live = el("p", { className: "muted small", id: "kg-stack-live", "aria-live": "polite" });
  const actions = el(
    "div",
    { className: "kg-stack-actions" },
    stackButton("Reject", "reject", "N"),
    stackButton("Skip", "skip", "S"),
    stackButton("Confirm", "confirm", "Y"),
    stackButton("Undo", "undo", "Z"),
  );
  box.append(progress, area, actions, live);
  stack.parts = { box, progress, area, live };
  drawStack();
  const later = s && s.later_groups ? ` ${plural(s.later_groups, "more group")} wait for a later batch.` : "";
  return card(
    "kg-review-panel",
    head,
    el("p", { className: "muted small" }, s ? `${plural(s.facts, "fact")} in ${plural(s.groups, "group")}.${later} Decisions are recorded as yours, from the console.` : ""),
    box,
    el("p", { className: "small" }, el("a", { href: `#/knowledge/${REVIEW_KEY}` }, "Open the full review"), el("span", { className: "muted" }, " (groups, \"confirm all except\")")),
  );
}

function stackButton(label, action, key) {
  const button = el("button", { type: "button", className: `button kg-act ${action}`, "aria-label": `${label} (${key})` }, label, " ", el("kbd", {}, key));
  button.addEventListener("click", () => {
    if (action === "undo") stackUndo();
    else stackDecide(action);
    if (stack.parts && stack.parts.box.focus) stack.parts.box.focus(); // the keys keep working
  });
  return button;
}

function stackKey(event) {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const action = STACK_KEYS[event.key] || STACK_KEYS[String(event.key).toLowerCase()];
  if (!action) return;
  event.preventDefault();
  if (action === "undo") stackUndo();
  else stackDecide(action);
}

function stackFactCard(entry, depth) {
  const f = entry.fact;
  const context = f.context || [];
  const node = el(
    "div",
    { className: `kg-card depth-${depth} ${cardClass(f.subject ? f.subject.card : "")}` },
    el("span", { className: "kg-stamp confirm", "aria-hidden": "true" }, "Confirm"),
    el("span", { className: "kg-stamp reject", "aria-hidden": "true" }, "Reject"),
    el("span", { className: "kg-stamp skip", "aria-hidden": "true" }, "Skip"),
    el("div", { className: "kg-card-group" }, `${f.ref} · ${entry.group}`),
    el("div", { className: "kg-card-sentence", title: f.id }, factSentence(f, true)),
    knownList(f.subject ? f.subject.name : "it", context),
    el("div", { className: "kg-card-meta" }, factBadges(f, true)),
    sourcesList(f.sources),
  );
  if (depth === 0) bindDrag(node);
  return node;
}

function stackCounts() {
  const counted = { confirm: 0, reject: 0, skip: 0 };
  for (const result of stack.results) if (result in counted) counted[result] += 1;
  return counted;
}

function stackDone() {
  if (!stack.cards.length) return el("div", { className: "kg-stack-done" }, el("p", {}, "Nothing to review: no proposed facts wait."));
  const n = stackCounts();
  const saving = stack.pending || stack.sending;
  const answered = stack.answered ? " The batch kg batch printed is marked answered, as kg batch apply does." : "";
  return el(
    "div",
    { className: "kg-stack-done" },
    el("p", { className: "kg-done-title" }, saving ? "Batch done: saving..." : "Today's batch is answered."),
    el("p", {}, `${n.confirm} confirmed, ${n.reject} rejected, ${n.skip} skipped (they stay proposed for a later batch).${saving ? " Z takes back the last one." : answered}`),
  );
}

function drawStack() {
  if (!stack.parts) return;
  const { progress, area: target, live } = stack.parts;
  progress.replaceChildren(...stack.cards.map((_, i) => el("li", { className: stack.results[i] ? `done-${stack.results[i]}` : i === stack.index ? "current" : "" })));
  const left = stack.cards.length - stack.index;
  const cards = [];
  for (let depth = Math.min(STACK.depth, left) - 1; depth >= 0; depth -= 1) cards.push(stackFactCard(stack.cards[stack.index + depth], depth));
  target.replaceChildren(...(left > 0 ? cards : [stackDone()]));
  if (stack.leaving && !reducedMotion()) {
    const ghost = stackFactCard(stack.leaving.card, 0);
    ghost.classList.add("leaving", `out-${stack.leaving.action}`);
    ghost.addEventListener("animationend", () => ghost.remove());
    target.append(ghost);
  }
  stack.leaving = null;
  const position = left > 0 ? `Card ${stack.index + 1} of ${stack.cards.length}.` : "";
  live.textContent = [stack.error, stack.note, position].filter(Boolean).join(" ");
  stack.note = "";
}

function stackDecide(action) {
  if (stack.index >= stack.cards.length) return;
  commitPending();
  const index = stack.index;
  stack.results[index] = action;
  stack.pending = { index, action, timer: setTimeout(commitPending, STACK.undoMs) };
  stack.leaving = { card: stack.cards[index], action };
  stack.index += 1;
  drawStack();
}

function stackUndo() {
  const p = stack.pending;
  if (!p) {
    stack.note = stack.index ? "The last decision is already saved: nothing to take back." : "Nothing to take back.";
  } else {
    clearTimeout(p.timer);
    stack.pending = null;
    stack.index = p.index;
    stack.results[p.index] = null;
  }
  drawStack();
}

// Send the decision that waits (if any); the stack redraws when the answer comes.
async function commitPending() {
  const p = stack.pending;
  if (!p) return;
  stack.pending = null;
  clearTimeout(p.timer);
  const fact = stack.cards[p.index].fact;
  stack.sending += 1;
  try {
    const result = await post(REVIEW_API, { action: STACK_ACTIONS[p.action], facts: [fact.id] }, review.csrf);
    stack.saved += 1;
    if (result.batch_answered) stack.answered = true;
  } catch (error) {
    stack.error = `${fact.ref} was not saved: ${error.message}`;
    stack.results[p.index] = "error";
  }
  stack.sending -= 1;
  drawStack();
}

// Drag the top card: right confirms, left rejects, down skips; the card follows the pointer
// through CSS custom properties (CSSOM, which the CSP allows; never a style attribute).
function bindDrag(node) {
  const props = (dx, dy) => {
    node.style.setProperty("--dx", `${dx}px`);
    node.style.setProperty("--dy", `${Math.max(dy, -20)}px`);
    node.style.setProperty("--confirm", String(Math.max(0, Math.min(1, dx / STACK.swipe))));
    node.style.setProperty("--reject", String(Math.max(0, Math.min(1, -dx / STACK.swipe))));
    node.style.setProperty("--skip", String(Math.abs(dx) < STACK.swipe / 2 ? Math.max(0, Math.min(1, dy / STACK.drop)) : 0));
  };
  node.addEventListener("pointerdown", (event) => {
    if (event.target && event.target.closest && event.target.closest("a, button")) return;
    stack.drag = { x: event.clientX, y: event.clientY, dx: 0, dy: 0 };
    if (node.setPointerCapture) node.setPointerCapture(event.pointerId);
    node.classList.add("dragging");
  });
  node.addEventListener("pointermove", (event) => {
    if (!stack.drag) return;
    stack.drag.dx = event.clientX - stack.drag.x;
    stack.drag.dy = event.clientY - stack.drag.y;
    props(stack.drag.dx, stack.drag.dy);
  });
  const end = () => {
    if (!stack.drag) return;
    const { dx, dy } = stack.drag;
    stack.drag = null;
    node.classList.remove("dragging");
    const action = dx > STACK.swipe ? "confirm" : dx < -STACK.swipe ? "reject" : dy > STACK.drop ? "skip" : "";
    if (action) stackDecide(action);
    else for (const name of ["--dx", "--dy", "--confirm", "--reject", "--skip"]) node.style.removeProperty(name);
  };
  node.addEventListener("pointerup", end);
  node.addEventListener("pointercancel", end);
}

// ---- Knowledge Graph: the Constellation ----
// Every entity (at most the model's cap) on a canvas, clustered by card with a small hand-written
// force layout (charge, springs, a pull to each card's anchor; O(n^2) per frame, fine for a few
// hundred nodes). Hover highlights a node's neighbours, a click opens its facts in a panel,
// search flies to an entity, the chips hide cards and the slider shows the graph as of a day.
// Proposed-only entities are translucent and proposed links dashed. With reduced motion nothing
// drifts or flies and the layout settles before the first frame.

const star = { model: null, nodes: [], edges: [], near: [], running: false, frame: 0, canvas: null, ctx: null, width: 0, height: 0, ratio: 1, cam: { x: 0, y: 0, k: 1 }, fly: null, alpha: 1, hover: null, selected: null, drag: null, pointer: null, cards: new Set(KG_CARDS), asOf: "", colours: {}, font: "sans-serif", results: [], pick: 0 };

function mulberry32(seed) {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function starColours() {
  const style = window.getComputedStyle ? window.getComputedStyle(document.documentElement) : null;
  const read = (name, fallback) => (style && style.getPropertyValue(name).trim()) || fallback;
  const colours = {};
  for (const [cardName, token] of Object.entries(KG_AXIS_TOKENS)) colours[cardName] = read(token, "#6b7380");
  for (const [name, token] of Object.entries({ text: "--text", muted: "--muted", line: "--border-strong", surface: "--surface", accent: "--accent" })) colours[name] = read(token, "#888888");
  return colours;
}

function starAnchor(cardName) {
  const [x, y] = STAR.anchors[cardName] || STAR.anchors.product;
  const spread = Math.max(1, Math.sqrt(star.nodes.length / STAR.spreadNodes));
  return [x * spread, y * spread];
}

function buildStar(model) {
  const random = mulberry32(STAR.seed);
  star.model = model;
  star.nodes = model.nodes.map((n) => ({ ...n, x: 0, y: 0, vx: 0, vy: 0, r: Math.min(STAR.maxRadius, STAR.baseRadius + Math.sqrt(n.degree) * STAR.degreeRadius), fixed: false, phase: random() * Math.PI * 2, shown: true, sx: 0, sy: 0 }));
  for (const n of star.nodes) {
    const [ax, ay] = starAnchor(n.card);
    const angle = random() * Math.PI * 2;
    const radius = STAR.startRadius * (0.3 + random());
    n.x = ax + Math.cos(angle) * radius;
    n.y = ay + Math.sin(angle) * radius;
  }
  star.edges = model.edges.map((e) => ({ ...e, s: star.nodes[e.from], t: star.nodes[e.to], same: star.nodes[e.from].card === star.nodes[e.to].card }));
  star.near = star.nodes.map(() => new Set());
  for (const e of model.edges) {
    star.near[e.from].add(e.to);
    star.near[e.to].add(e.from);
  }
  star.asOf = model.days.length ? model.days[model.days.length - 1] : "";
  star.hover = star.selected = null;
  starVisibility();
  star.alpha = 1;
  for (let i = 0; i < STAR.settleTicks; i += 1) {
    star.alpha = Math.max(STAR.minAlpha, 1 - i / STAR.settleTicks);
    starTick();
  }
  star.alpha = reducedMotion() ? 0 : STAR.restAlpha;
}

function starVisibility() {
  for (const n of star.nodes) n.shown = star.cards.has(n.card) && (!star.asOf || n.day <= star.asOf);
}

function starTick() {
  if (star.alpha < STAR.minAlpha) return;
  const alpha = star.alpha;
  const shown = star.nodes.filter((n) => n.shown);
  for (const n of shown) {
    const [ax, ay] = starAnchor(n.card);
    n.vx += (ax - n.x) * STAR.anchorPull * alpha;
    n.vy += (ay - n.y) * STAR.anchorPull * alpha;
  }
  starCharge(shown, alpha);
  for (const e of star.edges) {
    if (!e.s.shown || !e.t.shown) continue;
    const dx = e.t.x - e.s.x;
    const dy = e.t.y - e.s.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 1;
    const length = e.same ? STAR.sameLength : STAR.crossLength;
    const f = ((d - length) / d) * alpha * (e.same ? STAR.sameSpring : STAR.crossSpring);
    e.s.vx += dx * f;
    e.s.vy += dy * f;
    e.t.vx -= dx * f;
    e.t.vy -= dy * f;
  }
  for (const n of shown) {
    if (n.fixed) {
      n.vx = n.vy = 0;
      continue;
    }
    n.vx *= STAR.damping;
    n.vy *= STAR.damping;
    n.x += n.vx;
    n.y += n.vy;
  }
  star.alpha += (0 - star.alpha) * STAR.cooling;
}

// Every pair of shown nodes repels (within a range) and never overlaps.
function starCharge(shown, alpha) {
  for (let i = 0; i < shown.length; i += 1) {
    const a = shown[i];
    for (let j = i + 1; j < shown.length; j += 1) {
      const b = shown[j];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 1) {
        dx = Math.cos(i + j);
        dy = Math.sin(i + j);
        d2 = 1;
      }
      if (d2 < STAR.chargeRange) {
        const f = (STAR.charge * alpha) / d2;
        a.vx -= dx * f;
        a.vy -= dy * f;
        b.vx += dx * f;
        b.vy += dy * f;
      }
      const min = a.r + b.r + STAR.gap;
      if (d2 < min * min) {
        const d = Math.sqrt(d2);
        const push = ((min - d) / d) * STAR.overlapPush;
        a.x -= dx * push;
        a.y -= dy * push;
        b.x += dx * push;
        b.y += dy * push;
      }
    }
  }
}

function toScreen(x, y) {
  return [(x - star.cam.x) * star.cam.k + star.width / 2, (y - star.cam.y) * star.cam.k + star.height / 2];
}

function toWorld(sx, sy) {
  return [(sx - star.width / 2) / star.cam.k + star.cam.x, (sy - star.height / 2) / star.cam.k + star.cam.y];
}

function fitTarget() {
  const shown = star.nodes.filter((n) => n.shown);
  if (!shown.length) return { x: 0, y: 0, k: 1 };
  const xs = shown.map((n) => n.x);
  const ys = shown.map((n) => n.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const k = Math.min(STAR.maxFit, Math.max(STAR.minFit, Math.min(star.width / (x1 - x0 + STAR.fitMargin), star.height / (y1 - y0 + STAR.fitMargin))));
  return { x: (x0 + x1) / 2, y: (y0 + y1) / 2, k };
}

function flyTo(target) {
  if (reducedMotion()) {
    Object.assign(star.cam, target);
    star.fly = null;
    return;
  }
  star.fly = { from: { ...star.cam }, to: target, start: performance.now() };
}

function easeInOut(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
}

function starResize() {
  if (!star.canvas) return;
  const box = star.canvas.parentNode.getBoundingClientRect();
  star.ratio = Math.min(2, window.devicePixelRatio || 1);
  star.width = Math.max(STAR.minWidth, box.width);
  star.height = Math.max(STAR.minHeight, box.height);
  star.canvas.width = Math.round(star.width * star.ratio);
  star.canvas.height = Math.round(star.height * star.ratio);
}

function starFocus() {
  const focus = star.hover || star.selected;
  if (!focus) return null;
  const index = star.nodes.indexOf(focus);
  return new Set([index, ...star.near[index]]);
}

function starShape(ctx, cardName, x, y, r) {
  ctx.beginPath();
  if (cardName === "process") {
    ctx.moveTo(x, y - r * 1.2);
    ctx.lineTo(x + r * 1.2, y);
    ctx.lineTo(x, y + r * 1.2);
    ctx.lineTo(x - r * 1.2, y);
    ctx.closePath();
  } else if (cardName === "product") {
    ctx.rect(x - r, y - r, r * 2, r * 2);
  } else if (cardName === "systems") {
    ctx.moveTo(x, y - r * 1.2);
    ctx.lineTo(x + r * 1.1, y + r * 0.8);
    ctx.lineTo(x - r * 1.1, y + r * 0.8);
    ctx.closePath();
  } else {
    ctx.arc(x, y, r, 0, Math.PI * 2);
  }
}

function starDraw(now) {
  const { ctx, colours } = star;
  const still = reducedMotion();
  ctx.setTransform(star.ratio, 0, 0, star.ratio, 0, 0);
  ctx.clearRect(0, 0, star.width, star.height);
  const focus = starFocus();
  star.nodes.forEach((n) => {
    const drift = still ? 0 : STAR.drift;
    [n.sx, n.sy] = toScreen(n.x + Math.sin(now * STAR.driftSpeed + n.phase) * drift, n.y + Math.cos(now * STAR.driftSpeed + n.phase * 1.3) * drift);
  });
  starDrawEdges(ctx, colours, focus);
  starDrawNodes(ctx, colours, focus, now, still);
  starDrawLabels(ctx, colours, focus);
}

function starDrawEdges(ctx, colours, focus) {
  const focusIndex = focus ? star.nodes.indexOf(star.hover || star.selected) : -1;
  for (const e of star.edges) {
    if (!e.s.shown || !e.t.shown || (star.asOf && e.day > star.asOf)) continue;
    const touches = focus && (e.from === focusIndex || e.to === focusIndex);
    ctx.globalAlpha = focus ? (touches ? STAR.focusEdge : STAR.dimEdge) : e.confirmed ? STAR.edge : STAR.proposedEdge;
    ctx.strokeStyle = touches ? colours[(e.from === focusIndex ? e.t : e.s).card] : colours.line;
    ctx.lineWidth = touches ? 1.6 : 1;
    ctx.setLineDash(e.confirmed ? [] : STAR.dash);
    ctx.beginPath();
    ctx.moveTo(e.s.sx, e.s.sy);
    ctx.lineTo(e.t.sx, e.t.sy);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
}

function starDrawNodes(ctx, colours, focus, now, still) {
  const k = Math.max(STAR.minScale, Math.min(STAR.maxScale, star.cam.k));
  star.nodes.forEach((n, i) => {
    if (!n.shown) return;
    const r = n.r * k;
    const dim = focus && !focus.has(i) ? STAR.dimNode : 1;
    if (n.day === star.asOf && star.model.days.length > 1 && !still) {
      const phase = ((now / STAR.pulseMs + n.phase) % 1 + 1) % 1;
      ctx.globalAlpha = (1 - phase) * 0.6 * dim;
      ctx.strokeStyle = colours[n.card];
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(n.sx, n.sy, r + 3 + phase * 12, 0, Math.PI * 2);
      ctx.stroke();
    }
    starShape(ctx, n.card, n.sx, n.sy, r);
    ctx.globalAlpha = dim * (n.confirmed ? 1 : STAR.proposedNode);
    ctx.fillStyle = colours[n.card];
    ctx.fill();
    ctx.globalAlpha = dim;
    ctx.strokeStyle = n.confirmed ? colours.surface : colours[n.card];
    ctx.lineWidth = 1.2;
    ctx.stroke();
    if (n === star.selected) {
      starShape(ctx, n.card, n.sx, n.sy, r + 4);
      ctx.strokeStyle = colours.text;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  });
  ctx.globalAlpha = 1;
}

// Labels for the focus and its neighbours, else the hubs (or all when zoomed in); one never
// covers another.
function starDrawLabels(ctx, colours, focus) {
  const k = Math.max(STAR.minScale, Math.min(STAR.maxScale, star.cam.k));
  const lead = star.hover || star.selected;
  const wanted = star.nodes
    .map((n, i) => [n, i])
    .filter(([n, i]) => n.shown && (focus ? focus.has(i) : n.degree >= STAR.hubDegree || star.cam.k > STAR.labelZoom))
    .sort(([a], [b]) => (b === lead) - (a === lead) || b.degree - a.degree);
  const placed = [];
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (const [n] of wanted) {
    ctx.font = `${n === lead ? "600 13px" : "500 11.5px"} ${star.font}`;
    const y = n.sy + n.r * k + 5;
    const w = ctx.measureText(n.label).width + 6;
    const box = [n.sx - w / 2, y - 1, n.sx + w / 2, y + 15];
    if (n !== lead && placed.some((p) => box[0] < p[2] && box[2] > p[0] && box[1] < p[3] && box[3] > p[1])) continue;
    placed.push(box);
    ctx.lineWidth = 4;
    ctx.strokeStyle = colours.surface;
    ctx.strokeText(n.label, n.sx, y);
    ctx.fillStyle = n === lead ? colours.text : colours.muted;
    ctx.fillText(n.label, n.sx, y);
  }
  ctx.textBaseline = "alphabetic";
}

function starFrame() {
  if (!star.running) return;
  const now = performance.now();
  if (star.fly) {
    const t = Math.min(1, (now - star.fly.start) / STAR.flyMs);
    const e = easeInOut(t);
    for (const key of ["x", "y", "k"]) star.cam[key] = star.fly.from[key] + (star.fly.to[key] - star.fly.from[key]) * e;
    if (t >= 1) star.fly = null;
  }
  if (!document.hidden) {
    starTick();
    starDraw(now);
  }
  star.frame = requestAnimationFrame(starFrame);
}

function startConstellation() {
  stopConstellation();
  if (!star.canvas || !star.ctx) return;
  star.running = true;
  star.frame = requestAnimationFrame(starFrame);
}

function stopConstellation() {
  star.running = false;
  if (star.frame && typeof cancelAnimationFrame === "function") cancelAnimationFrame(star.frame);
  star.frame = 0;
}

function starHit(sx, sy) {
  const k = Math.max(STAR.minScale, Math.min(STAR.maxScale, star.cam.k));
  let best = null;
  let distance = Infinity;
  for (const n of star.nodes) {
    if (!n.shown) continue;
    const d = Math.hypot(n.sx - sx, n.sy - sy);
    if (d < Math.max(STAR.hitMin, n.r * k + 4) && d < distance) {
      distance = d;
      best = n;
    }
  }
  return best;
}

function starPoint(event) {
  const box = star.canvas.getBoundingClientRect();
  return [event.clientX - box.left, event.clientY - box.top];
}

function bindStarPointer(canvas) {
  canvas.addEventListener("pointerdown", (event) => {
    const [sx, sy] = starPoint(event);
    const n = starHit(sx, sy);
    star.pointer = { x: sx, y: sy, startX: sx, startY: sy, moved: false };
    star.drag = n;
    if (n) {
      n.fixed = true;
      star.alpha = Math.max(star.alpha, STAR.dragAlpha);
    }
    if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => starPointerMove(event, canvas));
  const end = () => {
    const p = star.pointer;
    if (!p) return;
    if (star.drag) star.drag.fixed = false;
    if (!p.moved) clickStar(starHit(p.x, p.y));
    star.drag = null;
    star.pointer = null;
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("pointerleave", () => {
    if (!star.pointer) star.hover = null;
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    star.fly = null;
    const [sx, sy] = starPoint(event);
    const [wx, wy] = toWorld(sx, sy);
    star.cam.k = Math.max(STAR.minZoom, Math.min(STAR.maxZoom, star.cam.k * Math.exp(-event.deltaY * STAR.wheel)));
    star.cam.x = wx - (sx - star.width / 2) / star.cam.k;
    star.cam.y = wy - (sy - star.height / 2) / star.cam.k;
  }, { passive: false });
}

function starPointerMove(event, canvas) {
  const [sx, sy] = starPoint(event);
  const p = star.pointer;
  if (!p) {
    star.hover = starHit(sx, sy);
    canvas.classList.toggle("pointer", Boolean(star.hover));
    canvas.setAttribute("title", star.hover ? `${star.hover.name} (${star.hover.type}): ${plural(star.hover.degree, "link")}, ${plural(star.hover.facts, "fact")}` : "");
    return;
  }
  if (Math.hypot(sx - p.startX, sy - p.startY) > STAR.clickSlop) p.moved = true;
  if (star.drag) {
    [star.drag.x, star.drag.y] = toWorld(sx, sy);
  } else {
    star.cam.x -= (sx - p.x) / star.cam.k;
    star.cam.y -= (sy - p.y) / star.cam.k;
    star.fly = null;
  }
  p.x = sx;
  p.y = sy;
}

// A click on a node opens its facts; the view moves so the panel does not cover it.
function clickStar(n) {
  if (n && n.sx > star.width - STAR.panelShift * 2) flyTo({ ...star.cam, x: n.x + STAR.panelShift / star.cam.k });
  selectStar(n);
}

// The facts panel: the entity's current facts, from its page's model, and a link to the page.
async function selectStar(n) {
  star.selected = n;
  const panel = document.getElementById("kg-star-panel");
  if (!panel) return;
  if (!n) {
    panel.classList.remove("open");
    panel.replaceChildren();
    return;
  }
  const close = el("button", { type: "button", className: "button kg-panel-close", "aria-label": "Close the facts panel" }, "Close");
  close.addEventListener("click", () => selectStar(null));
  const body = el("div", {}, el("p", { className: "placeholder" }, "Loading..."));
  panel.replaceChildren(close, el("h2", {}, n.name), el("div", { className: "meta" }, badge(n.type, "kind"), el("span", { className: `badge kg-axis-badge ${cardClass(n.card)}` }, n.card)), body);
  panel.classList.add("open");
  if (!n.key) return;
  try {
    const page = await api(`/api/knowledge/entity/${encodeURIComponent(n.key)}`);
    if (star.selected !== n) return;
    const facts = page.facts.flatMap((g) => g.facts);
    body.replaceChildren(
      ...present(
        facts.length ? el("ul", { className: "kg-list small" }, facts.slice(0, STAR.panelFacts).map((f) => el("li", {}, el("span", { className: "muted" }, f.predicate), " ", factTarget(f), " ", kgStatus(f.status)))) : el("p", { className: "placeholder" }, "No current facts."),
        facts.length > STAR.panelFacts ? el("p", { className: "muted small" }, `+${facts.length - STAR.panelFacts} more on the page`) : null,
        el("p", {}, el("a", { className: "button", href: entityHref(n.key) }, "Open the entity page")),
      ),
    );
  } catch (error) {
    showError(body, error);
  }
}

function focusStar(n) {
  if (!star.cards.has(n.card)) {
    star.cards.add(n.card);
    renderStarChips();
  }
  if (star.asOf && n.day > star.asOf) setAsOf(star.model.days.length - 1);
  starVisibility();
  flyTo({ x: n.x + STAR.panelShift / STAR.focusZoom, y: n.y, k: STAR.focusZoom });
  selectStar(n);
}

function starSearch() {
  const q = el("input", { type: "search", id: "kg-star-q", placeholder: "Find an entity", "aria-label": "Find an entity", autocomplete: "off", role: "combobox", "aria-expanded": "false", "aria-controls": "kg-star-results" });
  const list = el("ul", { className: "kg-star-results", id: "kg-star-results", role: "listbox" });
  const show = () => {
    list.replaceChildren(...star.results.map((n, i) => {
      const option = el("li", { role: "option", "aria-selected": i === star.pick ? "true" : "false", className: cardClass(n.card) }, el("span", { className: "kg-swatch", "aria-hidden": "true" }), n.name, el("span", { className: "muted small" }, ` ${n.type}`));
      option.addEventListener("click", () => go(n));
      return option;
    }));
    q.setAttribute("aria-expanded", star.results.length ? "true" : "false");
  };
  const go = (n) => {
    star.results = [];
    show();
    q.value = n.name;
    star.hover = null;
    focusStar(n);
  };
  q.addEventListener("input", () => {
    const text = q.value.trim().toLowerCase();
    star.pick = 0;
    star.results = text ? star.nodes.filter((n) => n.name.toLowerCase().includes(text) || n.type.includes(text)).sort((a, b) => a.name.toLowerCase().indexOf(text) - b.name.toLowerCase().indexOf(text) || b.degree - a.degree).slice(0, STAR.results) : [];
    star.hover = star.results[0] || null;
    show();
  });
  q.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      star.pick = Math.max(0, Math.min(star.results.length - 1, star.pick + (event.key === "ArrowDown" ? 1 : -1)));
      star.hover = star.results[star.pick] || null;
      show();
      event.preventDefault();
    } else if (event.key === "Enter" && star.results[star.pick]) {
      event.preventDefault();
      go(star.results[star.pick]);
    } else if (event.key === "Escape") {
      q.value = "";
      star.results = [];
      star.hover = null;
      show();
    }
  });
  return el("div", { className: "kg-star-search" }, q, list);
}

function renderStarChips() {
  const holder = document.getElementById("kg-star-chips");
  if (!holder) return;
  holder.replaceChildren(...star.model.cards.map((c) => {
    const chip = el("button", { type: "button", className: `kg-chip ${cardClass(c.card)}`, "aria-pressed": star.cards.has(c.card) ? "true" : "false" }, el("span", { className: "kg-swatch", "aria-hidden": "true" }), c.title, el("span", { className: "muted small" }, ` ${c.shown}`));
    chip.addEventListener("click", () => {
      if (star.cards.has(c.card) && star.cards.size > 1) star.cards.delete(c.card);
      else star.cards.add(c.card);
      starVisibility();
      star.alpha = Math.max(star.alpha, STAR.reheat);
      renderStarChips();
    });
    return chip;
  }));
}

function setAsOf(index) {
  const days = star.model.days;
  star.asOf = days[Math.max(0, Math.min(days.length - 1, index))] || "";
  starVisibility();
  star.alpha = Math.max(star.alpha, STAR.reheat);
  const slider = document.getElementById("kg-star-day");
  if (slider) slider.value = String(days.indexOf(star.asOf));
  const label = document.getElementById("kg-star-asof");
  if (label) label.textContent = starAsOfText();
}

function starAsOfText() {
  const shown = star.nodes.filter((n) => n.shown).length;
  return `As of ${star.asOf ? dayLabel(star.asOf) : "today"}: ${entityCount(shown)} shown`;
}

function starTimeline(model) {
  if (model.days.length < 2) return el("p", { className: "muted small", id: "kg-star-asof" }, `Everything was first captured on ${model.days[0] || "one day"}: no timeline yet.`);
  const slider = el("input", { type: "range", id: "kg-star-day", min: 0, max: model.days.length - 1, step: 1, value: model.days.length - 1, "aria-label": "Show the graph as of this day" });
  slider.addEventListener("input", () => setAsOf(Number(slider.value)));
  return el("div", { className: "kg-star-time" }, el("label", { for: "kg-star-day", id: "kg-star-asof" }, starAsOfText()), slider, el("div", { className: "muted small" }, `${model.days[0]} to ${model.days[model.days.length - 1]}`));
}

function starZoom() {
  const zoom = (label, factor) => {
    const button = el("button", { type: "button", className: "button", "aria-label": label }, factor ? (factor > 1 ? "+" : "−") : "Fit");
    button.addEventListener("click", () => flyTo(factor ? { ...star.cam, k: Math.max(STAR.minZoom, Math.min(STAR.maxZoom, star.cam.k * factor)) } : fitTarget()));
    return button;
  };
  return el("div", { className: "kg-star-zoom" }, zoom("Zoom in", STAR.zoomStep), zoom("Zoom out", 1 / STAR.zoomStep), zoom("Fit everything", 0));
}

function starLegend() {
  const item = (className, text) => el("li", {}, el("span", { className: `kg-legend-mark ${className}`, "aria-hidden": "true" }), text);
  return el(
    "ul",
    { className: "kg-star-legend small" },
    item("node", "Has a confirmed fact"),
    item("node proposed", "Proposed facts only"),
    item("line", "Confirmed link"),
    item("line proposed", "Proposed link"),
    el("li", { className: "muted" }, "Colour and shape: the card (People circle, Process diamond, Product square, Systems triangle). Size: links."),
  );
}

async function loadConstellation() {
  const target = document.getElementById("knowledge");
  if (!target) return;
  try {
    const model = await api(CONSTELLATION_API);
    target.replaceChildren(...constellationPage(model));
    if (model.exists && model.nodes.length) mountConstellation(model);
  } catch (error) {
    showError(target, error);
  }
}

function starCaption(model) {
  const capped = model.more ? ` Showing the ${model.cap} best connected; ${entityCount(model.more)} left out (see Raw data).` : "";
  const edgesCapped = model.more_edges ? ` ${plural(model.more_edges, "link")} not drawn.` : "";
  return `${entityCount(model.nodes.length)}, ${plural(model.edges.length, "link")}. Hover a node for its neighbours, click it for its facts, drag to move it or the view, scroll to zoom.${capped}${edgesCapped}`;
}

// Shown when the knowledge base changes while the Constellation is open: the graph is not redrawn
// under you; a click loads the new one in place.
function starReloadChip() {
  const chip = el("button", { type: "button", className: "button kg-reload", id: "kg-star-reload", hidden: true, title: "The knowledge base changed since this graph was drawn" }, "Reload: the knowledge base changed");
  chip.addEventListener("click", reloadConstellation);
  return chip;
}

function showStarReload() {
  const chip = document.getElementById("kg-star-reload");
  if (chip) chip.removeAttribute("hidden");
}

function constellationPage(model) {
  if (!model.exists) return [emptyKnowledge(model)];
  if (!model.nodes.length) return [card("", el("p", { className: "placeholder" }, "No entities yet."))];
  const canvas = el("canvas", { id: "kg-star-canvas", className: "kg-star-canvas", role: "img", "aria-label": `Constellation of ${entityCount(model.nodes.length)} by card. Use the search to find one and open its facts.` }, "Your browser cannot draw the constellation; use Raw data.");
  return [
    el("div", { className: "kg-star-tools" }, starSearch(), el("div", { className: "kg-star-chips", id: "kg-star-chips", role: "group", "aria-label": "Show cards" }), starReloadChip(), starZoom()),
    el("div", { className: "kg-star-stage" }, canvas, el("aside", { className: "kg-star-panel", id: "kg-star-panel", "aria-label": "Facts of the selected entity", "aria-live": "polite" })),
    el("div", { className: "kg-star-foot", id: "kg-star-foot" }, starTimeline(model), starLegend()),
    el("p", { className: "muted small", id: "kg-star-caption" }, starCaption(model)),
  ];
}

// The new graph in place: the camera, the hidden cards, the "as of" day (the latest stays the
// latest) and the open facts panel are kept, and entities already drawn keep their positions;
// new ones start where the layout put them. A graph that became empty (or was empty) is redrawn.
async function reloadConstellation() {
  const chip = document.getElementById("kg-star-reload");
  let model;
  try {
    model = await api(CONSTELLATION_API);
  } catch (error) {
    if (chip) chip.textContent = `Reload failed: ${error.message}`;
    return;
  }
  if (!star.ctx || !star.model || !model.exists || !model.nodes.length) {
    loadConstellation();
    return;
  }
  const kept = { cam: { ...star.cam }, latest: star.asOf === star.model.days[star.model.days.length - 1], asOf: star.asOf, selected: star.selected ? star.selected.id : "" };
  const placed = new Map(star.nodes.map((n) => [n.id, n]));
  buildStar(model);
  for (const n of star.nodes) {
    const old = placed.get(n.id);
    if (old) Object.assign(n, { x: old.x, y: old.y });
  }
  Object.assign(star.cam, kept.cam);
  star.fly = null;
  star.alpha = reducedMotion() ? 0 : STAR.reheat;
  const foot = document.getElementById("kg-star-foot");
  if (foot) foot.replaceChildren(starTimeline(model), starLegend());
  const days = model.days;
  setAsOf(kept.latest || !days.includes(kept.asOf) ? days.length - 1 : days.indexOf(kept.asOf));
  renderStarChips();
  const caption = document.getElementById("kg-star-caption");
  if (caption) caption.textContent = starCaption(model);
  selectStar(star.nodes.find((n) => n.id === kept.selected) || null);
  if (chip) {
    chip.setAttribute("hidden", "");
    chip.textContent = "Reload: the knowledge base changed";
  }
}

function mountConstellation(model) {
  const canvas = document.getElementById("kg-star-canvas");
  const ctx = canvas && canvas.getContext ? canvas.getContext("2d") : null;
  if (!ctx) return;
  Object.assign(star, { canvas, ctx, cards: new Set(KG_CARDS), colours: starColours(), cam: { x: 0, y: 0, k: 1 }, fly: null, results: [], pick: 0 });
  star.font = (window.getComputedStyle && window.getComputedStyle(document.body).fontFamily) || "sans-serif";
  starResize();
  buildStar(model);
  Object.assign(star.cam, fitTarget());
  renderStarChips();
  bindStarPointer(canvas);
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(starResize).observe(canvas.parentNode);
  startConstellation();
}

// ---- Knowledge: an entity's page ----

// <keep>: a refresh: no "Loading..." flash, and the history stays open if it was.
async function renderEntity(key, keep) {
  const back = el("a", { className: "back", href: "#/knowledge" }, "← Knowledge");
  const historyOpen = keep && Boolean(document.querySelector("details.kg-history[open]"));
  if (!keep) main().replaceChildren(back, el("p", { className: "placeholder" }, "Loading..."));
  try {
    const page = await api(`/api/knowledge/entity/${encodeURIComponent(key)}`);
    main().replaceChildren(back, ...entityPage(page, historyOpen));
    document.title = `${page.entity.name} - bosun console`;
  } catch (error) {
    main().replaceChildren(back, card("", el("p", { className: "problem" }, error.status === 404 ? "No such entity in the knowledge base." : error.message)));
  }
}

function entityHeader(e) {
  const strong = e.aliases.strong.join(", ");
  const weak = e.aliases.weak.join(", ");
  return el(
    "div",
    { className: "item-head" },
    el("h1", {}, e.name),
    el("div", { className: "meta" }, badge(e.type, "kind"), el("span", { className: `badge kg-axis-badge ${cardClass(e.card)}` }, e.card), el("code", {}, e.id)),
    strong || weak ? el("p", { className: "muted small" }, strong ? `also called ${strong}` : "", strong && weak ? "; " : "", weak ? `related: ${weak}` : "") : null,
    e.summary ? el("p", {}, e.summary) : null,
  );
}

function factsCard(groups) {
  return section(
    "Facts",
    groups.length
      ? groups.map((g) => el("div", { className: "kg-predicate" }, el("h3", {}, g.predicate, g.description ? el("span", { className: "muted small" }, ` ${g.description}`) : null), el("ul", { className: "kg-facts" }, g.facts.map((f) => factItem(f)))))
      : el("p", { className: "placeholder" }, "No current facts."),
  );
}

function historyCard(history, open) {
  if (!history.length) return null;
  return el(
    "details",
    { className: "card kg-history", open },
    el("summary", {}, `History (${plural(history.length, "fact")} no longer current: superseded, closed, rejected or expired)`),
    el("ul", { className: "kg-facts" }, history.map((f) => factItem(f))),
  );
}

function backlinksCard(links) {
  if (!links.length) return null;
  return section("Backlinks", el("ul", { className: "kg-facts" }, links.map((f) => el("li", {}, entityLink(f.subject), " ", el("span", { className: "muted" }, f.predicate), " this ", kgStatus(f.status)))));
}

function relatedCard(related) {
  if (!related) return null;
  const rows = related.items.map((r) => el("li", {}, itemLink(r.id), " ", badge(r.state), ` ${r.title}`, el("span", { className: "muted small" }, ` (${r.how})`)));
  return section(
    "Related ledger items",
    rows.length ? el("ul", { className: "kg-list" }, rows) : el("p", { className: "placeholder" }, "None in the ledger."),
    related.total > rows.length ? el("p", { className: "muted small" }, `+${related.total - rows.length} more`) : null,
  );
}

function linkedText(list) {
  if (!list.length) return "";
  return list.map((x, i) => [i ? ", " : "", x.ref ? entityLink(x.ref) : x.text || "", x.status === "proposed" ? el("span", { className: "muted" }, " (proposed)") : ""]);
}

function stepPairs(d) {
  return pairs([
    ["entry point", linkedText(d.entry_points)],
    ["actor", linkedText(d.actors)],
    ["gated by", linkedText(d.gates)],
    ["uses", linkedText(d.systems)],
    ["posts to", linkedText(d.channels)],
    ["notes", d.notes.length ? d.notes.map((n) => el("div", {}, n.text || "", n.status === "proposed" ? el("span", { className: "muted" }, " (proposed)") : "")) : ""],
  ]);
}

function flowCard(flow) {
  if (!flow) return null;
  const steps = flow.steps.map((s) =>
    el(
      "li",
      { className: `flow-step${s.status === "proposed" ? " proposed" : ""}` },
      el("div", { className: "flow-head" }, el("span", { className: "flow-number", "aria-hidden": "true" }, String(s.number)), entityLink(s.step), " ", badge(s.step.type, "kind"), s.status === "proposed" ? [" ", kgStatus(s.status)] : null),
      stepPairs(s),
    ),
  );
  return section("Flow", stepPairs(flow.process), el("ol", { className: "flow", "aria-label": "Steps in order" }, steps));
}

function graphCard(g, name) {
  if (g.nodes.length < 2) return null;
  const marks = g.edges.map((e) => {
    const [a, b] = [g.nodes[e.from], g.nodes[e.to]];
    return svg("line", { class: `kg-edge${e.confirmed ? "" : " proposed"}`, x1: a.x, y1: a.y, x2: b.x, y2: b.y }, svg("title", {}, `${a.name} ${e.predicates.join(", ")} ${b.name}`));
  });
  for (const n of g.nodes) {
    const center = n.depth === 0;
    const shape = [
      svg("circle", { class: `kg-node ${cardClass(n.card)}${center ? " center" : ""}`, cx: n.x, cy: n.y, r: center ? GRAPH.center : GRAPH.node }),
      svg("text", { class: "kg-label", x: n.x, y: n.y + (center ? GRAPH.centerLabel : GRAPH.label) }, n.label),
      svg("title", {}, `${n.name} (${n.type})`),
    ];
    marks.push(n.key && !center ? svg("a", { href: safeHref(entityHref(n.key)), "aria-label": n.name }, shape) : svg("g", {}, shape));
  }
  const title = `Neighbourhood of ${name}`;
  const picture = svg("svg", { class: "kg-graph", viewBox: `0 0 ${g.width} ${g.height}`, role: "group", "aria-label": title }, marks);
  const more = g.more ? ` (+${g.more} more not shown)` : "";
  return section("Neighbourhood", el("figure", { className: "chart-figure" }, picture, el("figcaption", { className: "muted small" }, `${entityCount(g.nodes.length - 1)} within two links${more}. Dashed: not confirmed yet.`)));
}

function entityPage(page, historyOpen) {
  const e = page.entity;
  const mainColumn = [flowCard(page.flow), factsCard(page.facts), historyCard(page.history, historyOpen)].filter(Boolean);
  const side = [backlinksCard(page.backlinks), relatedCard(page.related)].filter(Boolean);
  return [
    entityHeader(e),
    page.privacy ? card("callout", el("p", { className: "small" }, page.privacy)) : null,
    el(
      "div",
      { className: "item-grid" },
      el("div", { className: "stack" }, mainColumn),
      side.length ? el("aside", { className: "stack", "aria-label": "Links to this entity" }, side) : null,
    ),
    graphCard(page.graph, e.name),
  ].filter(Boolean);
}

// ---- Knowledge: the review batch ----
// The batch kg batch uses, lettered like the CLI. Confirm or reject a group or a fact, skip a fact
// (it stays proposed), or confirm a group except the facts ticked: the ticked ones stay proposed.

function renderReview() {
  main().replaceChildren(
    el("a", { className: "back", href: "#/knowledge" }, "← Knowledge"),
    pageHead("Knowledge Graph review"),
    el("div", { id: "kg-result", "aria-live": "polite" }),
    el("div", { id: "kg-review" }, el("p", { className: "placeholder" }, "Loading...")),
  );
  showDecision();
  loadReview();
}

async function loadReview() {
  const target = document.getElementById("kg-review");
  if (!target) return;
  try {
    const model = await api(REVIEW_API);
    review.csrf = model.csrf || "";
    review.ticks = [];
    target.replaceChildren(...reviewPage(model));
  } catch (error) {
    showError(target, error);
  }
}

// What the three answers mean, above the batch.
function reviewLegend() {
  return el(
    "p",
    { className: "kg-review-legend" },
    el("strong", {}, "Confirm"),
    " = true and current · ",
    el("strong", {}, "Reject"),
    " = wrong · ",
    el("strong", {}, "Skip"),
    " = not sure yet (it stays proposed for a later batch).",
  );
}

// What is already known about an entity, beside the facts being judged (the card stack and the
// review's blocks).
function knownList(name, known) {
  if (!known || !known.length) return null;
  return el(
    "div",
    { className: "kg-card-context" },
    el("div", { className: "muted small" }, `Already known about ${name}`),
    el("ul", { className: "kg-list small" }, known.map((c) => el("li", {}, kgStatus(c.status), " ", c.sentence ? factSentence(c, false) : expandableText(c.text)))),
  );
}

function reviewPage(model) {
  if (!model.exists) return [emptyKnowledge(model)];
  const s = model.summary;
  const source = s.source === "saved" ? `the batch kg batch printed ${when(s.created_at)}` : "the batch kg batch would print now";
  const later = s.later_groups ? ` ${plural(s.later_groups, "more group")} (${plural(s.later_facts, "fact")}) wait for a later batch.` : "";
  return [
    el("p", { className: "muted small" }, `${plural(s.groups, "group")}, ${plural(s.facts, "fact")}, ${s.waiting} waiting: ${source}.${later} Decisions are recorded as yours, from the console.`),
    reviewLegend(),
    ...(model.groups.length ? model.groups.map(reviewGroup) : [card("", el("p", { className: "placeholder" }, "Nothing to review: no proposed facts wait."))]),
  ];
}

function decisionButton(label, action, facts, extra) {
  const button = el("button", { type: "button", className: `button ${extra || ""}`.trim() }, label);
  button.addEventListener("click", () => decide(action, facts(), button));
  return button;
}

function reviewFact(f, ticks, inBlock, described) {
  const tick = f.decidable ? el("input", { type: "checkbox", "aria-label": `Leave ${f.ref} out when confirming the group` }) : null;
  if (tick) {
    ticks.set(f.id, tick);
    review.ticks.push(tick);
  }
  return el(
    "li",
    { className: `kg-review-fact${f.decidable ? "" : " decided"}`, title: f.id },
    el("label", { className: "kg-ref" }, tick, el("strong", { className: "mono" }, f.ref)),
    el("div", { className: "kg-fact-body" }, el("div", { className: "kg-fact-sentence" }, factSentence(f, !inBlock), factBadges(f, f.decidable)), sourcesList(f.sources, described)),
    f.decidable
      ? el("div", { className: "kg-fact-actions" }, decisionButton("Confirm", "confirm", () => [f.id]), decisionButton("Reject", "reject", () => [f.id]), decisionButton("Skip", "defer", () => [f.id]))
      : null,
  );
}

// A fact that places a block's entity ("concerns X", "is part of Y"): the block's subtitle, with
// its own small Reject and Skip (Confirm block and the group's buttons confirm it).
function blockLink(f) {
  return el(
    "span",
    { className: "kg-block-link", title: f.id },
    " · ",
    f.sentence ? sentenceParts(f) : factSentence(f, false),
    f.decidable ? [" ", decisionButton("Reject", "reject", () => [f.id], "small"), decisionButton("Skip", "defer", () => [f.id], "small")] : null,
  );
}

// One entity of a group: its type and name, the facts that place it, its summary, the facts to
// judge in plain words, and what is already known about it.
function reviewBlock(block, byRef, ticks) {
  const facts = block.facts.map((r) => byRef.get(r)).filter(Boolean);
  const links = block.links.map((r) => byRef.get(r)).filter(Boolean);
  const open = [...facts, ...links].filter((f) => f.decidable).map((f) => f.id);
  const head = block.subject;
  const described = new Set(); // memory notes whose summary this block already shows
  return el(
    "section",
    { className: `kg-block ${cardClass(head.card)}` },
    el(
      "div",
      { className: "kg-block-head" },
      el("span", { className: "kg-type" }, head.type_label),
      " ",
      el("strong", {}, entityLink(head)),
      links.map(blockLink),
      open.length > 1 ? el("span", { className: "kg-block-actions" }, decisionButton("Confirm block", "confirm", () => open, "small")) : null,
    ),
    head.summary ? el("p", { className: "muted small kg-block-summary" }, head.summary) : null,
    el("ol", { className: "kg-review-facts" }, facts.map((f) => reviewFact(f, ticks, true, described))),
    knownList(head.name, block.known),
  );
}

function reviewGroup(g) {
  const ticks = new Map(); // fact id -> its "leave out" checkbox
  const byRef = new Map(g.facts.map((f) => [f.ref, f]));
  const facts = g.blocks ? g.blocks.map((b) => reviewBlock(b, byRef, ticks)) : [el("ol", { className: "kg-review-facts" }, g.facts.map((f) => reviewFact(f, ticks, false)))];
  const open = g.facts.filter((f) => f.decidable).map((f) => f.id);
  const unticked = () => open.filter((id) => !(ticks.get(id) && ticks.get(id).checked));
  const actions = open.length
    ? el(
        "div",
        { className: "kg-actions" },
        decisionButton("Confirm group", "confirm", () => open, "primary"),
        decisionButton("Confirm all except ticked", "confirm", unticked),
        decisionButton("Reject group", "reject", () => open),
      )
    : el("p", { className: "muted small" }, "Every fact of this group is decided.");
  return card(
    `kg-group${g.rework ? " state-waiting" : ""}`,
    el("div", { className: "card-head" }, el("h3", {}, `${g.number}. ${g.title}`), badge(plural(g.facts.length, "fact")), g.rework ? badge("prevents rework", "warn") : null),
    facts,
    actions,
  );
}

async function decide(action, facts, button) {
  if (!facts.length) return;
  button.disabled = true;
  review.busy = true;
  try {
    review.result = await post(REVIEW_API, { action, facts }, review.csrf);
  } catch (error) {
    review.result = { error: error.message };
  }
  review.busy = false;
  showDecision();
  loadReview();
}

function showDecision() {
  const target = document.getElementById("kg-result");
  if (!target) return;
  const r = review.result;
  if (!r) {
    target.replaceChildren();
    return;
  }
  if (r.error) {
    target.replaceChildren(card("", el("p", { className: "problem" }, r.error)));
    return;
  }
  target.replaceChildren(
    card(
      "callout",
      el("p", {}, `${r.action} ${r.facts.join(", ")}: ${r.confirmed} confirmed, ${r.rejected} rejected, ${r.left_proposed} left proposed (${when(r.at)}).${r.batch_answered ? " Every fact of the batch kg batch printed is decided: it is marked answered." : ""}`),
      el("details", {}, el("summary", {}, "Details"), el("ul", {}, [...r.messages, ...(r.warnings || [])].map((m) => el("li", { className: "small" }, m)))),
    ),
  );
}

// ---- Library ----------------------------------------------------------------------------------

function badgeKind(text) {
  return { effective: "ok", "in use": "ok", "replaced by site": "", "added to every task": "kind" }[text] || "";
}

function renderLibrary(current) {
  if (current.key) {
    renderLibraryFile(current.key);
    return;
  }
  main().replaceChildren(pageHead("Config"), el("div", { id: "library" }, el("p", { className: "placeholder" }, "Loading...")));
  loadLibrary();
}

async function loadLibrary() {
  const target = document.getElementById("library");
  if (!target) return;
  try {
    target.replaceChildren(...libraryPage(await api("/api/library")));
  } catch (error) {
    showError(target, error);
  }
}

function libraryEntry(e) {
  return el(
    "li",
    {},
    el("a", { href: `#/library/${encodeURIComponent(e.key)}`, className: "mono" }, e.name),
    e.badge ? [" ", badge(e.badge, badgeKind(e.badge))] : null,
    e.title ? el("span", { className: "muted" }, ` ${e.title}`) : null,
    e.summary ? el("div", { className: "muted small" }, e.summary) : null,
  );
}

function libraryPage(model) {
  return [
    el("p", { className: "muted small" }, "Read-only. Site files and memory notes are redacted, and config values whose names hold TOKEN, SECRET, KEY or PASSWORD, and webhook URLs, are masked.", model.memory ? "" : " Memory notes are off (BOSUN_CONSOLE_MEMORY_DIRS)."),
    el("div", { className: "health-grid" }, model.groups.map((g) => section(g.name, el("ul", { className: "library-list" }, g.entries.map(libraryEntry))))),
  ];
}

async function renderLibraryFile(key) {
  const back = el("a", { className: "back", href: "#/library" }, "← Library");
  main().replaceChildren(back, el("p", { className: "placeholder" }, "Loading..."));
  try {
    const file = await api(`/api/library/${encodeURIComponent(key)}`);
    const body = file.error
      ? el("p", { className: "problem" }, `This file ${file.error}.`)
      : file.kind === "markdown" ? markdown(file.body) : el("pre", { className: "code" }, el("code", {}, file.body));
    main().replaceChildren(
      ...present(
        back,
        pageHead(file.name, badge(file.root, "kind"), file.badge ? badge(file.badge, badgeKind(file.badge)) : null, file.redacted ? badge("masked", "warn") : null),
        el("p", { className: "muted small" }, file.group),
        frontMatterTable(file.front_matter) ? card("", el("h2", {}, "Front matter"), frontMatterTable(file.front_matter)) : null,
        card("", body),
      ),
    );
    document.title = `${file.name} - bosun console`;
  } catch (error) {
    main().replaceChildren(back, card("", el("p", { className: "problem" }, error.status === 404 ? "No such file in the Library." : error.message)));
  }
}

// ---- status footer and polling ----------------------------------------------------------------

function showStatus(text, problem) {
  const status = document.getElementById("status");
  status.textContent = text;
  status.classList.toggle("problem", Boolean(problem));
}

function describe(version) {
  const ledger = version.ledger;
  if (ledger.error) return [`Ledger: ${ledger.error}`, true];
  if (!ledger.exists) return ["No ledger yet.", false];
  const updated = ledger.mtime ? new Date(ledger.mtime).toLocaleString() : "unknown";
  return [`${ledger.items} items, ledger updated ${updated}`, false];
}

// Every POLL_MS while the page is visible: the ledger's version (and the open view when it
// changed), the "Needs you" badge, and the Sessions view, whose facts change without the ledger.
async function poll() {
  if (document.hidden) return;
  let version;
  try {
    version = await api("/api/version");
  } catch (error) {
    showStatus(error.message, true);
    return;
  }
  const [text, problem] = describe(version);
  showStatus(text, problem);
  if (lastStamp !== null && version.stamp !== lastStamp) refresh();
  lastStamp = version.stamp;
  const knowledgeStamp = version.knowledge ? version.knowledge.stamp : null;
  if (lastKnowledgeStamp !== null && knowledgeStamp !== lastKnowledgeStamp) refreshKnowledge();
  lastKnowledgeStamp = knowledgeStamp;
  const view = route().view;
  if (view === "sessions") loadSessions();
  if (view === "ledger") loadNeedsYou(); // the strip sets the badge too
  else updateNeedsBadge();
}

window.addEventListener("pagehide", () => commitPending());
window.addEventListener("hashchange", () => {
  render();
  if (route().view !== "ledger") updateNeedsBadge();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) poll();
});
document.addEventListener("DOMContentLoaded", () => {
  render();
  poll();
  setInterval(poll, POLL_MS);
});
