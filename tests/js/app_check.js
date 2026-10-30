// A check of console/app.js without a browser or npm packages: node runs the page's script in a
// sandbox with a small fake DOM, then exercises its helpers and renders views from API models
// (tests/js/api_models.py writes them). It fails on what a browser would show silently: text such
// as "[object HTMLElement]", "undefined" or "NaN", a style or on* attribute (the CSP forbids them),
// a link that is not a console route or http(s), and any HTML-parsing API in the source. Canvas
// drawing goes to a fake 2D context that records its calls (numbers must be finite, text clean).
// Run it through tests/js_check.sh: node app_check.js <app.js> <models.json>
"use strict";

const fs = require("fs");
const vm = require("vm");

const HTML_NS = "http://www.w3.org/1999/xhtml";
const SVG_NS = "http://www.w3.org/2000/svg";
const BAD_TEXT = /\[object |\bundefined\b|\bNaN\b/;
const BANNED_SOURCE = /\.innerHTML|\.outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function/;

// ---- a fake DOM: just what app.js uses ----------------------------------------------------------

class FakeNode {
  constructor(nodeType, name, namespace) {
    this.nodeType = nodeType;
    this.nodeName = name;
    this.namespaceURI = namespace;
    this.childNodes = [];
    this.attributes = new Map();
    this.listeners = {};
    this.dataset = {};
    this.parentNode = null;
    this.data = "";
    this.value = "";
    this.style = fakeStyle(); // CSSOM: custom properties set by script (the CSP allows these)
    const node = this;
    this.classList = {
      contains: (name) => node.className.split(/\s+/).includes(name),
      add: (name) => node.classList.toggle(name, true),
      remove: (name) => node.classList.toggle(name, false),
      toggle(name, force) {
        const names = new Set(node.className.split(/\s+/).filter(Boolean));
        const on = force === undefined ? !names.has(name) : Boolean(force);
        if (on) names.add(name);
        else names.delete(name);
        node.className = [...names].join(" ");
        return on;
      },
    };
  }

  get tagName() {
    return this.nodeName;
  }

  get className() {
    return this.attributes.get("class") || "";
  }

  set className(value) {
    this.attributes.set("class", String(value));
  }

  get id() {
    return this.attributes.get("id") || "";
  }

  get children() {
    return this.childNodes.filter((child) => child.nodeType === 1);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  focus() {
    document.activeElement = this;
  }

  setPointerCapture() {}

  getBoundingClientRect() {
    return { left: 0, top: 0, width: STAGE.width, height: STAGE.height };
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  addEventListener(type, listener) {
    (this.listeners[type] = this.listeners[type] || []).push(listener);
  }

  // As a browser does: anything that is not a node becomes text (so a stray element in an array
  // shows up here as "[object ...]").
  append(...nodes) {
    for (const node of nodes) {
      const child = node instanceof FakeNode ? node : textNode(String(node));
      child.parentNode = this;
      this.childNodes.push(child);
    }
  }

  replaceChildren(...nodes) {
    this.childNodes = [];
    this.append(...nodes);
  }

  get textContent() {
    return this.nodeType === 3 ? this.data : this.childNodes.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this.childNodes = [];
    this.append(textNode(String(value)));
  }
}

function textNode(text) {
  const node = new FakeNode(3, "#text", null);
  node.data = text;
  return node;
}

function* walk(node) {
  yield node;
  for (const child of node.childNodes) yield* walk(child);
}

function element(tag, id) {
  const node = new FakeNode(1, tag.toUpperCase(), HTML_NS);
  if (id) node.setAttribute("id", id);
  if (tag === "canvas") node.getContext = (kind) => (kind === "2d" ? fakeContext() : null);
  return node;
}

function fakeStyle() {
  const props = new Map();
  return { props, setProperty: (name, value) => props.set(name, String(value)), removeProperty: (name) => props.delete(name) };
}

// A 2D context that accepts any drawing call and records it (canvasCalls), with measureText.
const STAGE = { width: 800, height: 520 };
const canvasCalls = [];
function fakeContext() {
  const state = { measureText: (text) => ({ width: String(text).length * 6 }) };
  return new Proxy(state, {
    get(target, name) {
      if (name in target) return target[name];
      return (...args) => {
        canvasCalls.push({ name, args });
      };
    },
    set(target, name, value) {
      target[name] = value;
      canvasCalls.push({ name: `set ${String(name)}`, args: [value] });
      return true;
    },
  });
}

const body = element("body");
for (const id of ["main", "status", "needs-count", "needs-badge"]) body.append(element(id === "main" ? "main" : "span", id));

const document = {
  hidden: false,
  title: "",
  body,
  createElement: (tag) => element(tag),
  createElementNS: (namespace, tag) => new FakeNode(1, tag, namespace),
  createTextNode: textNode,
  getElementById: (id) => [...walk(body)].find((node) => node.nodeType === 1 && node.id === id) || null,
  documentElement: element("html"),
  activeElement: null,
  listeners: [],
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: (type) => document.listeners.push(type),
};

// ---- the sandbox --------------------------------------------------------------------------------

const [appFile, modelsFile] = process.argv.slice(2);
const source = fs.readFileSync(appFile, "utf8");
const models = JSON.parse(fs.readFileSync(modelsFile, "utf8"));
const requested = [];
const posted = []; // the writes the page sent: { path, options }

// GETs answer models[path]; a POST answers models["POST " + path] and is recorded in <posted>.
function fakeFetch(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const key = method === "GET" ? path : `${method} ${path}`;
  requested.push(key);
  if (method !== "GET") posted.push({ path, options });
  const found = Object.prototype.hasOwnProperty.call(models, key);
  return Promise.resolve({
    ok: found,
    status: found ? 200 : 404,
    json: () => Promise.resolve(found ? models[key] : { error: "not found" }),
  });
}

const frames = []; // requestAnimationFrame callbacks, run by runFrames()
const media = { reduced: false };
const sandbox = {
  document,
  window: {
    addEventListener: () => {},
    getSelection: () => ({ selectAllChildren: () => {} }),
    matchMedia: (query) => ({ matches: query.includes("reduced-motion") && media.reduced }),
    getComputedStyle: () => ({ getPropertyValue: (name) => (name.startsWith("--") ? "#2f55cc" : ""), fontFamily: "sans-serif" }),
    devicePixelRatio: 2,
  },
  requestAnimationFrame: (callback) => frames.push(callback),
  cancelAnimationFrame: () => {},
  performance,
  location: { hash: "" },
  history: { replaceState: () => {} },
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  fetch: fakeFetch,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  Node: FakeNode,
  URL,
  URLSearchParams,
  console,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: "app.js" });

// ---- checks -------------------------------------------------------------------------------------

let failures = 0;
function check(description, test) {
  try {
    const problem = test();
    if (problem) throw new Error(problem);
    console.log(`  pass  ${description}`);
  } catch (error) {
    failures += 1;
    console.log(`  FAIL  ${description}: ${error.message}`);
  }
}

// What a browser would show wrongly, or the CSP would block, anywhere under <root>.
function problems(root) {
  for (const node of walk(root)) {
    if (node.nodeType === 3 && BAD_TEXT.test(node.data)) return `text "${node.data.slice(0, 80)}"`;
    if (node.nodeType === 3 && node.data === "null") return "a null passed to append() or replaceChildren() shows as the text null";
    for (const [name, value] of node.attributes) {
      if (name === "style" || name.startsWith("on")) return `attribute ${name} on <${node.nodeName}>`;
      if (BAD_TEXT.test(value)) return `attribute ${name}="${value.slice(0, 80)}"`;
      if (name === "href" && !(value.startsWith("#/") || /^https?:\/\/[^\s/]+/.test(value))) return `href ${value}`;
    }
  }
  return null;
}

// A top-level const of app.js (const bindings are not properties of the sandbox's global).
function live(name) {
  return vm.runInContext(name, sandbox);
}

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function rendered(hash, settle = 4) {
  sandbox.location.hash = hash;
  sandbox.render();
  for (let i = 0; i < settle; i += 1) await flush();
  return document.getElementById("main");
}

function count(root, test) {
  return [...walk(root)].filter(test).length;
}

async function main() {
  const { el, pairs, svg, stackedBars, bars, sparkline, markdown } = sandbox;

  check("the source uses no HTML-parsing API", () => (BANNED_SOURCE.test(source) ? `found ${source.match(BANNED_SOURCE)[0]}` : null));

  check("el() flattens nested child arrays into elements", () => {
    const node = el("ul", {}, [[el("li", {}, "a")], [[el("li", {}, "b"), null, false]]], "c");
    return node.children.length === 2 && node.textContent === "abc" ? problems(node) : `got ${node.textContent}`;
  });

  check("pairs() renders names and values, nested ones too, as elements", () => {
    const node = pairs([["window", "@exporter"], ["results", [el("div", {}, "one"), el("div", {}, "two")]], ["empty", ""]]);
    const names = node.children.filter((child) => child.nodeName === "DT").map((child) => child.textContent);
    return names.join() === "window,results" ? problems(node) : `names ${names}`;
  });

  check("el() refuses a link that is not a route or http(s)", () => {
    for (const href of ["javascript:alert(1)", "data:text/html,x", "/etc/passwd", "file:///x"]) {
      let refused = false;
      try {
        el("a", { href }, "x");
      } catch (error) {
        refused = true;
      }
      if (!refused) return `accepted ${href}`;
    }
    return null;
  });

  check("svg() makes SVG elements, and charts carry a title and desc", () => {
    const figure = stackedBars("Opened", ["2026-10-19", "2026-10-20"], [{ name: "task", values: [1, 2] }, { name: "my-pr", values: [0, 3] }]);
    const chart = [...walk(figure)].find((node) => node.nodeName === "svg");
    if (!chart || chart.namespaceURI !== "http://www.w3.org/2000/svg") return "no SVG element";
    if (chart.getAttribute("role") !== "img") return "no role=img";
    const [first, second] = chart.children;
    if (first.nodeName !== "title" || second.nodeName !== "desc") return "title and desc are not first";
    if (count(chart, (n) => n.nodeName === "rect") !== 3) return "expected 3 bar segments";
    if (svg("g", {}).namespaceURI !== SVG_NS) return "svg() namespace";
    return problems(figure);
  });

  check("bars() and sparkline() draw from numbers, empty ones too", () => {
    for (const node of [bars("People", [{ name: "Dana", count: 2 }, { name: "Lee", count: 1 }]), bars("None", []), sparkline("Per day", [0, 0, 0]), sparkline("One", [3])]) {
      const problem = problems(node);
      if (problem) return problem;
    }
    return null;
  });

  check("markdown() keeps HTML as text and links only routes and http(s)", () => {
    const node = markdown("<script>alert(1)</script> <img src=x onerror=alert(1)>\n\n[ok](https://example.com/a) [bad](javascript:alert(1)) [route](#/ledger)");
    if (count(node, (n) => ["script", "img", "SCRIPT", "IMG"].includes(n.nodeName))) return "an HTML element was made";
    if (!node.textContent.includes("<script>alert(1)</script>")) return "the HTML text is gone";
    const links = [...walk(node)].filter((n) => n.nodeName === "A").map((n) => n.getAttribute("href"));
    if (links.join() !== "https://example.com/a,#/ledger") return `links ${links}`;
    return node.textContent.includes("bad (javascript:alert(1))") ? problems(node) : "the refused link's text is gone";
  });

  check("markdown() blocks: headings, nested lists, code, tables, quotes, rules", () => {
    const text = [
      "# Title", "", "Some **bold**, *em*, `code` and snake_case_name.", "",
      "- one", "  - nested", "- two", "", "1. first", "2. second", "",
      "```", "<b>raw</b>", "```", "", "| a | b |", "| --- | --- |", "| 1 | 2 |", "", "> quoted", "", "---",
    ].join("\n");
    const node = markdown(text);
    const names = (tag) => count(node, (n) => n.nodeName === tag);
    const wanted = { H2: 1, UL: 2, OL: 1, LI: 5, PRE: 1, TABLE: 1, TD: 2, BLOCKQUOTE: 1, HR: 1, STRONG: 1, EM: 1, CODE: 2 };
    for (const [tag, n] of Object.entries(wanted)) if (names(tag) !== n) return `${names(tag)} ${tag}, expected ${n}`;
    if (!node.textContent.includes("snake_case_name")) return "underscores inside a word became emphasis";
    return problems(node);
  });

  for (const [hash, needs] of [...VIEWS, ...(models.__views__ || [])]) {
    const main = await rendered(hash);
    check(`${hash} renders from its API model`, () => {
      if (!main.textContent.includes(needs)) return `no "${needs}" in the page`;
      if (main.textContent.includes("Coming soon")) return "still a placeholder";
      return problems(main);
    });
  }

  await expandableChecks();
  await impactChecks();
  await knowledgeChecks();
  await briefingChecks();
  await constellationChecks();

  check("the views asked only for the API paths they know", () => {
    const unknown = requested.filter((path) => !Object.prototype.hasOwnProperty.call(models, path));
    return unknown.length ? `unknown: ${unknown.join(", ")}` : null;
  });

  if (failures) {
    console.log(`${failures} JS check(s) failed`);
    process.exit(1);
  }
}

function elements(root, test) {
  return [...walk(root)].filter((n) => n.nodeType === 1 && test(n));
}

function button(root, label) {
  return elements(root, (n) => n.nodeName === "BUTTON" && n.textContent === label)[0];
}

async function click(node) {
  for (const listener of node.listeners.click || []) listener({ preventDefault() {} });
  for (let i = 0; i < 4; i += 1) await flush();
}

// The Knowledge view beyond rendering: a process's flow and graph, the empty states, and the
// review's writes (method, CSRF header, JSON body, "except" leaves ticked facts out).
async function impactChecks() {
  const page = await rendered("#/impact");
  const m = models["/api/impact"];
  check("#/impact: tiles against the baseline, estimates badged, the start line, the band", () => {
    const text = page.textContent;
    for (const needed of ["PRs merged per week", "baseline 1.9 a week", "+174%", "16.8x", "Where agent time went", "Your time and agent time", "for every hour you spent prompting", "Agent hours per hour of yours", "What learning has produced", "Export retried on a closed invoice", "Not measured yet"]) {
      if (!text.includes(needed)) return `no "${needed}"`;
    }
    if (count(page, (n) => n.nodeType === 1 && (n.className || "").includes("estimate")) < 3) return "estimates are not badged";
    if (!count(page, (n) => n.nodeName === "line" && n.getAttribute("class") === "start-line")) return "no start line";
    if (!count(page, (n) => n.nodeName === "rect" && n.getAttribute("class") === "baseline-band")) return "no baseline band";
    if (!text.includes("mystery-1")) return "the unpriced model is not named";
    return problems(page);
  });
  check("#/impact: every merged PR is a link in the week-by-week list", () => {
    const urls = new Set(elements(page, (n) => n.nodeName === "A").map((a) => a.getAttribute("href")));
    const missing = m.weeks.flatMap((w) => w.pr_links).filter((p) => !urls.has(p.url));
    return missing.length ? `${missing.length} PRs without a link, e.g. ${missing[0].url}` : null;
  });
  check("#/impact without a snapshot says how to collect one", () => {
    const holder = sandbox.el("div", {}, sandbox.impactPage({ exists: false }));
    return holder.textContent.includes("bosun impact collect") ? problems(holder) : "no pointer to the collector";
  });
  check("an early snapshot says so", () => {
    const holder = sandbox.el("div", {}, sandbox.impactPage({ ...m, early: true }));
    return holder.textContent.includes("Early data") ? null : "no early-data note";
  });
  check("#/impact: the time chart draws agent and touch hours side by side, with a legend", () => {
    const bars = (series) => count(page, (n) => n.nodeName === "rect" && (n.getAttribute("class") || "").includes(`impact-bar ${series}`));
    if (!bars("series-0") || bars("series-0") !== bars("series-1")) return "the two series are not drawn week by week";
    return page.textContent.includes("Your touch time (h)") ? null : "no legend for the touch time";
  });
  check("#/impact: the Overhead card stays; the fix comparison and guardrails moved to the internals page", () => {
    const text = page.textContent;
    for (const needed of ["Overhead: what bosun itself costs", "overhead share, last 7 days", "32%", "cost per check-in", "Bosun internals"]) {
      if (!text.includes(needed)) return `no "${needed}"`;
    }
    if (text.includes("Before and after the overhead fixes")) return "the fix comparison is still on the Impact page";
    return problems(page);
  });
  check("#/impact: a snapshot without turn data says to collect again", () => {
    const holder = sandbox.el("div", {}, sandbox.impactPage({ ...m, overhead: { exists: false } }));
    return holder.textContent.includes("collect again") ? problems(holder) : "no pointer to collect again";
  });
  check("#/impact: the Cost per task panel shows the shares, the groups, the top items and the unattributed rest", () => {
    const text = page.textContent;
    for (const needed of ["Cost per task", "attributed to an item", "in subagents", "By outcome", "By task type", "fix-my-pr", "Unattributed, by window", "The 3 most expensive items", "Export invoices as CSV", "within 8 hours"]) {
      if (!text.includes(needed)) return `no "${needed}"`;
    }
    const pr = elements(page, (n) => n.nodeName === "A").find((a) => a.getAttribute("href") === "https://github.com/acme/exporter/pull/7" && a.textContent === "PR");
    return pr ? problems(page) : "the top item's PR is not linked";
  });
  check("#/impact: no attributed turns says how to get some", () => {
    const holder = sandbox.el("div", {}, sandbox.impactPage({ ...m, tasks: { exists: false } }));
    return holder.textContent.includes("No turns attributed yet") ? problems(holder) : "no pointer for cost per task";
  });
  const internals = await rendered("#/impact/internals");
  check("#/impact/internals: the fix, contexts, cost per turn and the guardrails", () => {
    const text = internals.textContent;
    for (const needed of ["Bosun internals", "Back to Impact", "Before and after the overhead fixes (2026-10-13)", "Context re-read per turn, by window", "orchestrator", "Guardrails: the work, week by week", "Check-ins sent / skipped as nothing new", "10 / 18"]) {
      if (!text.includes(needed)) return `no "${needed}"`;
    }
    if (!text.includes("57%")) return "the before side of the fix is missing";
    return problems(internals);
  });
}

async function expandableChecks() {
  const long = "a gotcha that takes a while to explain, ".repeat(6);
  const node = sandbox.expandableText(long);
  const body = elements(node, (n) => n.className.includes("kg-clamp"))[0];
  const toggle = button(node, "Show more");
  check("a long fact text is whole but clamped, with a Show more button", () => {
    if (!body || body.textContent !== long) return "the text is not all there";
    if (!toggle || toggle.getAttribute("aria-expanded") !== "false") return "no Show more button";
    return problems(node);
  });
  await click(toggle);
  check("... Show more opens it", () => {
    if (!body.className.includes("open") || toggle.textContent !== "Show less") return "it did not open";
    if (toggle.getAttribute("aria-expanded") !== "true") return "aria-expanded is not true";
    return null;
  });
  await click(toggle);
  check("... closed again", () => (body.className.includes("open") || toggle.textContent !== "Show more" ? "it did not close" : null));
  check("a short fact text has no toggle", () => (button(sandbox.expandableText("short"), "Show more") ? "a toggle on a short text" : null));
}

async function knowledgeChecks() {
  const empty = models.__knowledge_empty__;
  check("the knowledge views have empty states that point to kg import", () => {
    const nodes = [...sandbox.knowledgePage(empty.overview), ...sandbox.reviewPage(empty.review)];
    const holder = sandbox.el("div", {}, nodes);
    if (!holder.textContent.includes("bosun kg import")) return "no pointer to kg import";
    return problems(holder);
  });

  const releaseKey = Object.keys(models).find((path) => path.startsWith("/api/knowledge/entity/") && models[path].entity.id === "process/release");
  const page = await rendered(`#/knowledge/${releaseKey.split("/").pop()}`);
  check("a process page shows its steps in order, with arrows and a linked graph", () => {
    const steps = elements(page, (n) => n.nodeName === "LI" && n.className.includes("flow-step"));
    const names = steps.map((s) => elements(s, (n) => n.nodeName === "A")[0].textContent);
    if (names.join() !== "Merge to main,Smoke tests,Gradual rollout") return `steps ${names}`;
    const graph = elements(page, (n) => n.nodeName === "svg" && n.getAttribute("class") === "kg-graph")[0];
    if (!graph) return "no graph";
    const links = elements(graph, (n) => n.nodeName === "a").map((a) => a.getAttribute("href"));
    if (!links.length || !links.every((href) => href.startsWith("#/knowledge/"))) return `graph links ${links}`;
    return problems(page);
  });

  const reviewPage = await rendered("#/knowledge/review");
  check("the review: a legend, one block per entity, facts in plain words, sources in words", () => {
    const text = reviewPage.textContent;
    if (!text.includes("Confirm = true and current") || !text.includes("Skip = not sure yet")) return "no legend";
    const blocks = elements(reviewPage, (n) => n.nodeName === "SECTION" && n.className.includes("kg-block"));
    const heads = blocks.map((b) => elements(b, (n) => n.className.includes("kg-type"))[0].textContent + " " + elements(b, (n) => n.nodeName === "A")[0].textContent);
    if (heads[0] !== "process Release process") return `first block ${heads[0]}`;
    if (!text.includes("Includes the step Gradual rollout (step 3)") || !text.includes("Has the rule: Every release deploys to staging first")) return "no plain sentences";
    if (/\bhas_step\b|\bcaveat\b/.test(text)) return "a raw predicate is shown";
    if (elements(reviewPage, (n) => n.className.includes("badge") && n.textContent === "proposed").length) return "a proposed badge in the review";
    if (!text.includes("Memory note: deploys")) return "a memory source is not in words";
    if (!text.includes("Already known about Release process")) return "no already known list";
    const confirmBlock = button(blocks[0], "Confirm block");
    return confirmBlock ? problems(reviewPage) : "no Confirm block button";
  });

  check("a fact that places the entity is the block's subtitle, with its own Reject and Skip", () => {
    const base = models["/api/knowledge/review"].groups[0];
    const fact = base.facts[0];
    const link = { ...fact, id: "f900", ref: "1z", predicate: "about", sentence: [{ text: "concerns " }, { entity: fact.subject }] };
    const head = { ...fact.subject, type_label: "process", summary: "How a change ships." };
    const node = sandbox.reviewGroup({ ...base, facts: [fact, link], blocks: [{ subject: head, links: ["1z"], facts: [fact.ref], known: [] }] });
    const subtitle = elements(node, (n) => n.className.includes("kg-block-link"))[0];
    if (!subtitle || !subtitle.textContent.includes("concerns Release process")) return "no subtitle";
    if (!button(subtitle, "Reject") || !button(subtitle, "Skip")) return "no Reject and Skip on it";
    if (!node.textContent.includes("How a change ships.")) return "no summary";
    return problems(node);
  });

  check("a memory note's summary shows once per block, not under every fact citing it", () => {
    const base = models["/api/knowledge/review"].groups[0];
    const note = { kind: "memory", kind_label: "Memory note", label: "deploys", ref: "deploys.md", trusted: true, library: "k1", detail: "Every release deploys to staging first" };
    const [one, two] = base.facts.slice(0, 2).map((f) => ({ ...f, subject: base.facts[0].subject, sources: [note] }));
    const head = { ...one.subject, type_label: "process", summary: "" };
    const node = sandbox.reviewGroup({ ...base, facts: [one, two], blocks: [{ subject: head, links: [], facts: [one.ref, two.ref], known: [] }] });
    const summaries = elements(node, (n) => n.className.includes("kg-source-detail"));
    const titles = elements(node, (n) => n.nodeName === "A" && n.textContent === "deploys");
    if (summaries.length !== 1) return `${summaries.length} summaries`;
    return titles.length === 2 ? problems(node) : `${titles.length} note links`;
  });

  posted.length = 0;
  await click(button(elements(reviewPage, (n) => n.nodeName === "SECTION" && n.className.includes("kg-block"))[0], "Confirm block"));
  check("Confirm block POSTs that entity's waiting facts only", () => {
    const body = posted.length ? JSON.parse(posted[0].options.body) : null;
    const block = models["/api/knowledge/review"].groups[0].blocks[0];
    const byRef = new Map(models["/api/knowledge/review"].groups[0].facts.map((f) => [f.ref, f]));
    const wanted = [...block.facts, ...block.links].map((r) => byRef.get(r)).filter((f) => f.decidable).map((f) => f.id);
    return body && body.action === "confirm" && body.facts.join() === wanted.join() ? null : `body ${JSON.stringify(body)}`;
  });

  posted.length = 0;
  await click(button(reviewPage, "Confirm group"));
  check("Confirm group POSTs the group's facts as JSON with the CSRF token", () => {
    const [sent] = posted;
    if (!sent || sent.path !== "/api/knowledge/review") return "nothing posted";
    if (sent.options.headers["X-Bosun-CSRF"] !== models["/api/knowledge/review"].csrf) return "no CSRF header";
    if (sent.options.headers["Content-Type"] !== "application/json") return "not JSON";
    const body = JSON.parse(sent.options.body);
    if (body.action !== "confirm" || body.facts.join() !== empty.first.join()) return `body ${sent.options.body}`;
    const shown = document.getElementById("kg-result").textContent;
    return shown.includes(`${empty.first.length} confirmed`) ? problems(document.getElementById("main")) : `result "${shown}"`;
  });

  const again = await rendered("#/knowledge/review");
  const [tick] = elements(again, (n) => n.nodeName === "INPUT" && n.getAttribute("type") === "checkbox");
  tick.checked = true;
  posted.length = 0;
  await click(button(again, "Confirm all except ticked"));
  check("Confirm all except ticked leaves the ticked fact out", () => {
    const body = posted.length ? JSON.parse(posted[0].options.body) : null;
    return body && body.facts.join() === empty.first.slice(1).join() ? null : `body ${JSON.stringify(body)}`;
  });

  const inProgress = await rendered("#/knowledge/review");
  const [kept] = elements(inProgress, (n) => n.nodeName === "INPUT" && n.getAttribute("type") === "checkbox");
  kept.checked = true;
  sandbox.refreshKnowledge();
  for (let i = 0; i < 4; i += 1) await flush();
  const stillThere = elements(document.getElementById("main"), (n) => n === kept).length === 1;
  kept.checked = false;
  sandbox.refreshKnowledge();
  for (let i = 0; i < 4; i += 1) await flush();
  const replaced = elements(document.getElementById("main"), (n) => n === kept).length === 0;
  check("a refresh on poll keeps a review with ticks, and reloads one without", () => {
    if (!stillThere) return "the ticked review was re-rendered";
    return replaced ? null : "the review without ticks was not reloaded";
  });
}

// ---- the Knowledge Graph's tabs: Briefing (and its review stack) and Constellation ----

function keydown(node, key) {
  for (const listener of node.listeners.keydown || []) listener({ key, preventDefault() {} });
}

function fire(node, type, event = {}) {
  for (const listener of node.listeners[type] || []) listener({ preventDefault() {}, pointerId: 1, ...event });
}

async function settle(rounds = 8) {
  for (let i = 0; i < rounds; i += 1) await flush();
}

function postedBodies() {
  return posted.map((p) => {
    if (p.options.headers["X-Bosun-CSRF"] !== models["/api/knowledge/review"].csrf) throw new Error("a POST without the CSRF header");
    return JSON.parse(p.options.body);
  });
}

async function briefingChecks() {
  const empty = models.__knowledge_empty__;
  check("the Briefing and the Constellation have empty states that point to kg import", () => {
    const holder = sandbox.el("div", {}, sandbox.briefingPage(empty.briefing, empty.review), sandbox.constellationPage(empty.constellation));
    if (!holder.textContent.includes("bosun kg import")) return "no pointer to kg import";
    return problems(holder);
  });

  const page = await rendered("#/knowledge", 8);
  check("#/knowledge is the Briefing: tabs, headline, coverage, placeholder, timeline, most connected", () => {
    const tabs = elements(page, (n) => n.getAttribute("role") === "tab");
    const labels = tabs.map((t) => t.textContent).join();
    if (labels !== "Briefing,Constellation,Raw data,Metro (prototype)") return `tabs ${labels}`;
    if (tabs[0].getAttribute("aria-selected") !== "true") return "Briefing is not the selected tab";
    if (tabs[3].getAttribute("aria-disabled") !== "true" || tabs[3].hasAttribute("href")) return "the Metro tab is not disabled";
    const text = page.textContent;
    for (const needed of ["Facts captured", "Confirmed", "Coverage", "Rediscovery saved", "Not measured yet", "This week we learned", "Before", "Now", "Most connected", "Today's review"]) {
      if (!text.includes(needed)) return `no "${needed}"`;
    }
    const rings = elements(page, (n) => n.nodeName === "svg" && (n.getAttribute("class") || "").startsWith("kg-ring"));
    if (rings.length !== 4) return `${rings.length} coverage rings`;
    return problems(page);
  });

  const stackBox = document.getElementById("kg-stack");
  const cards = models["/api/knowledge/review"].groups.flatMap((g) => g.facts.filter((f) => f.decidable).map((f) => f.id));
  posted.length = 0;
  keydown(stackBox, "y");
  const afterFirst = posted.length;
  keydown(stackBox, "s");
  await settle();
  keydown(stackBox, "z");
  keydown(stackBox, "n");
  await sandbox.commitPending();
  await settle();
  check("the card stack: Y, S, Z, N send confirm then reject (Z took the skip back before it was sent)", () => {
    if (afterFirst !== 0) return "a decision was sent before Z could take it back";
    const bodies = postedBodies();
    const wanted = [{ action: "confirm", facts: [cards[0]] }, { action: "reject", facts: [cards[1]] }];
    if (JSON.stringify(bodies) !== JSON.stringify(wanted)) return `sent ${JSON.stringify(bodies)}`;
    if (live("stack").index !== 2) return `the stack is at card ${live("stack").index}`;
    return problems(document.getElementById("main"));
  });

  check("the stack's keys are bound to the stack only, never to the whole page", () => (document.listeners.includes("keydown") ? "a keydown listener on the document" : null));

  posted.length = 0;
  const top = elements(document.getElementById("kg-stack-cards"), (n) => n.className.includes("depth-0") && !n.className.includes("leaving"))[0];
  fire(top, "pointerdown", { clientX: 100, clientY: 100, target: top });
  fire(top, "pointermove", { clientX: 260, clientY: 110 });
  const dragged = top.style.props.get("--dx");
  fire(top, "pointerup", {});
  await sandbox.commitPending();
  await settle();
  check("dragging the top card right confirms it, through CSS custom properties", () => {
    if (dragged !== "160px") return `--dx ${dragged}`;
    const bodies = postedBodies();
    return bodies.length === 1 && bodies[0].action === "confirm" && bodies[0].facts[0] === cards[2] ? null : `sent ${JSON.stringify(bodies)}`;
  });

  posted.length = 0;
  for (let i = live("stack").index; i < cards.length; i += 1) keydown(stackBox, "s");
  await sandbox.commitPending();
  await settle();
  check("after the last card the batch is answered, and skips are sent as defer", () => {
    const bodies = postedBodies();
    if (!bodies.length || !bodies.every((b) => b.action === "defer")) return `sent ${JSON.stringify(bodies)}`;
    const text = document.getElementById("kg-stack-cards").textContent;
    return text.includes("Today's batch is answered") ? problems(document.getElementById("main")) : `shown "${text}"`;
  });
}

function runFrames(count) {
  for (let i = 0; i < count && frames.length; i += 1) frames.shift()(sandbox.performance.now());
}

function canvasProblem() {
  for (const call of canvasCalls) {
    for (const arg of call.args) {
      if (typeof arg === "number" && !Number.isFinite(arg)) return `${call.name}(${call.args.join(", ")})`;
      if (typeof arg === "string" && BAD_TEXT.test(arg)) return `${call.name}("${arg}")`;
      if (arg === undefined || arg === null) return `${call.name} with ${arg}`;
    }
  }
  return null;
}

async function constellationChecks() {
  const model = models["/api/knowledge/constellation"];
  canvasCalls.length = 0;
  frames.length = 0;
  const page = await rendered("#/knowledge?view=constellation", 8);
  runFrames(5);
  const star = live("star");
  const node = star.nodes[0];
  fire(star.canvas, "pointermove", { clientX: node.sx, clientY: node.sy });
  runFrames(2);
  check("?view=constellation draws every node on a canvas, with clean labels", () => {
    if (!star.canvas || star.canvas.nodeName !== "CANVAS") return "no canvas";
    if (!star.running) return "the animation is not running";
    if (star.nodes.length !== model.nodes.length) return `${star.nodes.length} nodes of ${model.nodes.length}`;
    if (star.hover !== node) return "hovering a node does not highlight it";
    const labels = canvasCalls.filter((c) => c.name === "fillText").map((c) => c.args[0]);
    if (!labels.includes(node.label)) return `no label for the hovered node (${labels.join(", ")})`;
    if (!canvasCalls.some((c) => c.name === "setLineDash" && c.args[0].length) && model.edges.some((e) => !e.confirmed)) return "no dashed proposed link";
    return canvasProblem() || problems(page);
  });

  const search = document.getElementById("kg-star-q");
  search.value = node.name.slice(0, 4);
  fire(search, "input");
  keydown(search, "Enter");
  await settle();
  check("the search flies to an entity and opens its facts with a link to its page", () => {
    const panel = document.getElementById("kg-star-panel");
    if (!panel.className.includes("open")) return "the panel did not open";
    const links = elements(panel, (n) => n.nodeName === "A").map((a) => a.getAttribute("href"));
    if (!links.some((href) => href.startsWith("#/knowledge/"))) return `panel links ${links}`;
    return problems(panel);
  });

  const chips = elements(document.getElementById("kg-star-chips"), (n) => n.nodeName === "BUTTON");
  fire(chips[0], "click");
  check("an axis chip hides its card's nodes", () => {
    const hidden = star.nodes.filter((n) => n.card === model.cards[0].card);
    if (!hidden.length) return null;
    return hidden.every((n) => !n.shown) ? null : "nodes of a hidden card are still shown";
  });

  media.reduced = true;
  await rendered("#/knowledge?view=constellation", 8);
  const before = live("star").nodes.map((n) => `${n.x},${n.y}`).join();
  canvasCalls.length = 0;
  runFrames(3);
  const after = live("star").nodes.map((n) => `${n.x},${n.y}`).join();
  media.reduced = false;
  check("with reduced motion the layout is still and settled before the first frame", () => (before === after ? canvasProblem() : "nodes moved"));

  await reloadChecks(model);

  await rendered("#/knowledge?view=raw", 8);
  check("leaving the Constellation stops its animation", () => (live("star").running ? "still running" : null));
}

// A knowledge base change while the Constellation is open shows the Reload chip and redraws
// nothing; the chip loads the new graph in place, keeping the camera and the positions drawn.
async function reloadChecks(model) {
  await rendered("#/knowledge?view=constellation", 8);
  runFrames(2);
  const star = live("star");
  const chip = document.getElementById("kg-star-reload");
  const hiddenAtFirst = chip && chip.hasAttribute("hidden");
  Object.assign(star.cam, { x: 12, y: -7, k: 2 });
  const first = star.nodes[0];
  const where = `${first.x},${first.y}`;
  const fetched = requested.length;
  sandbox.refreshKnowledge();
  await settle();
  const shown = !chip.hasAttribute("hidden");
  const quiet = requested.length === fetched && live("star").nodes[0] === first;
  const grown = { ...model, nodes: [...model.nodes, { ...model.nodes[0], key: null, id: "system/new-one", name: "New one", label: "New one", degree: 0 }] };
  models["/api/knowledge/constellation"] = grown;
  fire(chip, "click");
  await settle();
  models["/api/knowledge/constellation"] = model;
  const after = live("star");
  check("a knowledge base change shows the Reload chip, and the chip loads the new graph in place", () => {
    if (!chip || !hiddenAtFirst) return "no hidden Reload chip on the Constellation";
    if (!shown) return "the chip did not appear when the knowledge base changed";
    if (!quiet) return "the graph was reloaded without a click";
    if (after.nodes.length !== grown.nodes.length) return `${after.nodes.length} nodes after the reload`;
    if (after.cam.k !== 2 || after.cam.x !== 12 || after.cam.y !== -7) return `the camera moved: ${JSON.stringify(after.cam)}`;
    const same = after.nodes.find((n) => n.id === first.id);
    if (`${same.x},${same.y}` !== where) return "an entity already drawn moved";
    if (!chip.hasAttribute("hidden")) return "the chip is still shown";
    if (!document.getElementById("kg-star-caption").textContent.includes(entityText(grown.nodes.length))) return "the caption was not updated";
    return problems(document.getElementById("main"));
  });
}

function entityText(count) {
  return count === 1 ? "1 entity" : `${count} entities`;
}

// Each view to render, and a text it must show.
const VIEWS = [
  ["#/impact", "PRs merged per week"],
  ["#/stats?days=7", "Throughput"],
  ["#/stats?days=30", "Reached out to"],
  ["#/learning", "Findings remembered"],
  ["#/library", "Task types"],
];

main().catch((error) => {
  console.log(`  FAIL  ${error.stack || error}`);
  process.exit(1);
});
