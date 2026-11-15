/* The home page's chart ("How it works"): the ontology (the knowledge base) drawn as a sea chart, the orchestrator leading
   one boat per repository, and the ledger beside it as the ship's log (docs/index.md). Drawn into the SVG from
   one table per layout (wide, narrow), so a phone gets a chart composed for its width rather than a shrunken
   one. Islands are closed curves from a few harmonics; the depth contours are the same curve, wider. The names
   are the fictional acme world of the other examples (tests/fixtures/knowledge/acme-import.jsonl). No library. */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const narrowQ = window.matchMedia("(max-width: 620px)");

  const ISLANDS = {
    people: { name: "People", sub: "work facts only", seed: 1 },
    process: { name: "Process", sub: "how work gets done", seed: 2 },
    product: { name: "Product", sub: "what you build and run", seed: 3 },
  };
  /* Landmarks, the graph's entities: [id, island, label, type, fact, source] */
  const MARKS = [
    ["person/dana", "people", "Dana", "person", "Dana reviews invoice exporter changes.", "Source: a review in the ledger and a task's report."],
    ["person/lee", "people", "Lee", "person", "Lee wants review requests in #acme-prs, several questions as one table.", "Source: Lee said so in Slack, and a message in the ledger."],
    ["team/billing", "people", "Billing team", "team", "Dana is in the billing team.", "Source: the company directory."],
    ["process/release", "process", "Release", "process", "Every release goes to staging first; the rollout starts once the canary checks pass.", "Source: you said so in a session, and a memory note."],
    ["gate/smoke-tests", "process", "Smoke tests", "gate", "Smoke tests are the second step of every release.", "Source: a pull request and a memory note."],
    ["channel/acme-prs", "process", "#acme-prs", "channel", "Review requests for the exporter team go to #acme-prs.", "Source: a message in the ledger."],
    ["repo/invoice-exporter", "product", "invoice exporter", "repo", "Invoice totals round per line, not per invoice; tests compare to the cent.", "Source: a memory note."],
    ["repo/pdf-renderer", "product", "pdf renderer", "repo", "The invoice exporter depends on the pdf renderer.", "Source: a task's report and a pull request."],
    ["environment/staging-2", "product", "staging 2", "environment", "staging 2 is retired: never point new test runs at it.", "Source: a memory note and a task. Waits in today's review batch."],
  ];
  /* Facts drawn as leading lines: [from, to, predicate] */
  const FACTS = [
    ["person/dana", "repo/invoice-exporter", "reviews"],
    ["person/dana", "team/billing", "member_of"],
    ["process/release", "gate/smoke-tests", "has_step"],
  ];
  const BOATS = [
    ["b-exporter", "acme/exporter", "Claude Code"],
    ["b-web", "acme/web", "Codex"],
    ["b-infra", "acme/infra", "Antigravity"],
  ];
  const LAYOUTS = {
    wide: {
      w: 960, h: 500,
      islands: { people: [180, 352, 72], process: [540, 150, 80], product: [620, 376, 70] },
      marks: {
        "person/dana": [-40, -22], "person/lee": [34, 6], "team/billing": [-6, 34],
        "process/release": [-50, -14], "gate/smoke-tests": [34, 26], "channel/acme-prs": [12, -44],
        "repo/invoice-exporter": [-30, -16], "repo/pdf-renderer": [30, 2], "environment/staging-2": [-8, 32],
      },
      labelSide: {},
      regionAt: { people: [-70, -88], process: [-176, -66], product: [86, 54] },
      fleet: { x: 350, y: 232, heading: 8, spread: 1.55 },
      bearings: [["process/release", "takes bearings before each task", 0.3], ["repo/invoice-exporter", "", 0.5]],
      cartouche: [24, 24, 236, 74],
      rose: [318, 70, 32],
      edge: 790,
      ghosts: [[915, 150, 62, 7], [925, 380, 56, 8]],
      next: [800, 236],
    },
    narrow: {
      w: 400, h: 720,
      islands: { people: [112, 476, 50], process: [268, 196, 56], product: [282, 566, 52] },
      marks: {
        "person/dana": [-26, -16], "person/lee": [18, 2], "team/billing": [0, 24],
        "process/release": [-34, -6], "gate/smoke-tests": [20, 20], "channel/acme-prs": [6, -34],
        "repo/invoice-exporter": [-26, -12], "repo/pdf-renderer": [22, 4], "environment/staging-2": [-8, 26],
      },
      labelSide: { "repo/invoice-exporter": "start" },
      regionAt: { people: [-56, 68], process: [-150, -60], product: [-10, -70] },
      fleet: { x: 168, y: 340, heading: 0, spread: 1 },
      boats: [[122, 376], [112, 302], [56, 330]],
      bearings: [["process/release", "", 0.55], ["repo/invoice-exporter", "", 0.5]],
      cartouche: [16, 16, 192, 66],
      rose: [350, 62, 22],
      edge: 652,
      ghosts: [[110, 748, 50, 7], [320, 756, 46, 8]],
      next: [22, 662],
      edgeAxis: "y",
    },
  };

  function el(name, attrs, parent, text) {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  /* A closed island outline: a radius from a few harmonics, smoothed with Catmull-Rom. */
  function blob(cx, cy, r, seed, grow) {
    const N = 44;
    const pts = [];
    const a = [0.16, 0.11, 0.07].map((x, i) => x * (1 - grow * 0.012 * (i + 1)));
    for (let i = 0; i < N; i++) {
      const t = (i / N) * Math.PI * 2;
      const k = 1 + a[0] * Math.sin(2 * t + seed * 1.7) + a[1] * Math.sin(3 * t + seed * 2.9) + a[2] * Math.sin(5 * t + seed * 0.8);
      const rr = r * k + grow;
      pts.push([cx + Math.cos(t) * rr, cy + Math.sin(t) * rr * 0.82]);
    }
    const f = (n) => n.toFixed(1);
    let d = `M${f(pts[0][0])},${f(pts[0][1])}`;
    for (let i = 0; i < N; i++) {
      const p0 = pts[(i - 1 + N) % N];
      const p1 = pts[i];
      const p2 = pts[(i + 1) % N];
      const p3 = pts[(i + 2) % N];
      const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
      const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
      d += `C${f(c1[0])},${f(c1[1])} ${f(c2[0])},${f(c2[1])} ${f(p2[0])},${f(p2[1])}`;
    }
    return d + "Z";
  }
  /* A boat seen from above, pointing along +x, with its wake behind. */
  function boat(g, x, y, heading, len, lead) {
    const b = el("g", { transform: `translate(${x},${y}) rotate(${heading})` }, g);
    const w = len * 0.34;
    el("path", { class: "wake", d: `M${-len * 0.5},${-w * 0.35} L${-len * 2.6},${-w * 1.9} M${-len * 0.5},${w * 0.35} L${-len * 2.6},${w * 1.9}` }, b);
    el("path", {
      class: "hull" + (lead ? " lead-boat" : ""),
      d: `M${len * 0.5},0 C${len * 0.25},${-w * 0.62} ${-len * 0.2},${-w * 0.55} ${-len * 0.5},${-w * 0.42} L${-len * 0.5},${w * 0.42} C${-len * 0.2},${w * 0.55} ${len * 0.25},${w * 0.62} ${len * 0.5},0Z`,
    }, b);
    return b;
  }

  function Chart(root) {
    const svg = root.querySelector("svg");
    const tipEl = root.querySelector(".bx-chart__tip");
    const log = root.dataset.log ? document.querySelector(root.dataset.log) : null;
    let current = null;
    let shown = reduce.matches;

    function tip(g, fact, source) {
      const wrap = root.getBoundingClientRect();
      const r = g.getBoundingClientRect();
      tipEl.replaceChildren(Object.assign(document.createElement("b"), { textContent: fact }), Object.assign(document.createElement("span"), { textContent: source }));
      tipEl.hidden = false;
      const tw = tipEl.offsetWidth;
      const x = Math.max(4, Math.min(r.left - wrap.left + r.width / 2 - tw / 2, wrap.width - tw - 4));
      let y = r.top - wrap.top - tipEl.offsetHeight - 8;
      if (y < 4) y = r.bottom - wrap.top + 8;
      tipEl.style.left = x + "px";
      tipEl.style.top = y + "px";
    }
    function hideTip() {
      tipEl.hidden = true;
    }
    function hoverable(g, fact, source) {
      g.addEventListener("mouseenter", () => tip(g, fact, source));
      g.addEventListener("focus", () => tip(g, fact, source));
      g.addEventListener("mouseleave", hideTip);
      g.addEventListener("blur", hideTip);
    }
    function highlight(id) {
      svg.querySelectorAll(".is-hl").forEach((n) => n.classList.remove("is-hl"));
      const t = id && svg.querySelector(`[data-id="${id}"]`);
      if (t) t.classList.add("is-hl");
    }

    function sea(L, vertical, defs) {
      const grad = el("linearGradient", vertical ? { id: "bx-chart-fade", x1: 0, y1: 0, x2: 0, y2: 1 } : { id: "bx-chart-fade", x1: 0, y1: 0, x2: 1, y2: 0 }, defs);
      el("stop", { offset: "0", class: "fade-a" }, grad);
      el("stop", { offset: "1", class: "fade-b" }, grad);
      const clip = el("clipPath", { id: "bx-chart-inside" }, defs);
      el("rect", { x: 10, y: 10, width: L.w - 20, height: L.h - 20 }, clip);
      el("rect", { class: "paper", x: 0, y: 0, width: L.w, height: L.h }, svg);
      const g = el("g", { "clip-path": "url(#bx-chart-inside)" }, svg);
      for (const key in ISLANDS) {
        const [cx, cy, r] = L.islands[key];
        const s = ISLANDS[key].seed;
        for (let k = 3; k >= 1; k--) el("path", { class: "contour" + (k > 1 ? " deep" : ""), d: blob(cx, cy, r, s, k * 13) }, g);
        el("path", { class: "shoal", d: blob(cx, cy, r, s, 13) }, g);
        el("path", { class: "contour", d: blob(cx, cy, r, s, 13) }, g);
      }
      for (const key in ISLANDS) {
        const [cx, cy, r] = L.islands[key];
        el("path", { class: "land", d: blob(cx, cy, r, ISLANDS[key].seed, 0) }, g);
      }
      /* Past the edge, a teammate's chart: not drawn yet */
      for (const [gx, gy, gr, gs] of L.ghosts) {
        el("path", { class: "ghost-land", d: blob(gx, gy, gr, gs, 0) }, g);
        el("path", { class: "ghost-land", d: blob(gx, gy, gr, gs, 16), opacity: 0.5 }, g);
      }
      if (vertical) el("rect", { x: 0, y: L.edge - 40, width: L.w, height: L.h - L.edge + 40, fill: "url(#bx-chart-fade)", opacity: 0.85 }, g);
      else el("rect", { x: L.edge - 50, y: 0, width: L.w - L.edge + 50, height: L.h, fill: "url(#bx-chart-fade)", opacity: 0.85 }, g);
      return g;
    }

    function landmarks(L, vertical, g, pos) {
      const facts = el("g", {}, g);
      for (const [a, b, pred] of FACTS) {
        const [x1, y1] = pos[a];
        const [x2, y2] = pos[b];
        const mx = (x1 + x2) / 2;
        const my = (y1 + y2) / 2 - Math.abs(x2 - x1) * 0.12;
        el("path", { class: "lead", d: `M${x1},${y1} Q${mx},${my} ${x2},${y2}` }, facts);
        if (vertical) continue;
        el("text", { class: "pred", x: mx, y: (y1 + y2) / 2 - Math.abs(x2 - x1) * 0.06 - 4, "text-anchor": "middle" }, facts, pred);
      }
      for (const key in ISLANDS) {
        const [cx, cy] = L.islands[key];
        const [dx, dy] = L.regionAt[key];
        const t = el("text", { class: "region", x: cx + dx, y: cy + dy }, g, ISLANDS[key].name);
        el("tspan", { class: "region-sub", x: cx + dx, dy: 14 }, t, ISLANDS[key].sub);
      }
      for (const [id, island, label, type, fact, source] of MARKS) {
        const [x, y] = pos[id];
        const m = el("g", { tabindex: 0, role: "button", "aria-label": `${label}, ${type}. ${fact} ${source}`, "data-id": id }, g);
        el("circle", { class: "mark", cx: x, cy: y, r: 4.5 }, m);
        el("circle", { class: "mark-dot", cx: x, cy: y, r: 1.4 }, m);
        const anchor = L.labelSide[id] || (x > L.islands[island][0] + 4 ? "start" : "end");
        const tx = anchor === "start" ? x + 8 : x - 8;
        el("text", { class: "mark-label", x: tx, y: y + 1, "text-anchor": anchor }, m, label);
        el("text", { class: "mark-type", x: tx, y: y + 12, "text-anchor": anchor }, m, type);
        hoverable(m, fact, source);
      }
    }

    function fleet(L, g, pos) {
      const F = L.fleet;
      const rad = (F.heading * Math.PI) / 180;
      const back = (d, side) => [F.x - Math.cos(rad) * d + Math.sin(rad) * side, F.y - Math.sin(rad) * d - Math.cos(rad) * side];
      const s = F.spread;
      const places = L.boats || [back(46 * s, -26 * s), back(52 * s, 24 * s), back(92 * s, -2 * s)];
      const bearings = el("g", {}, g);
      for (const [to, label, at] of L.bearings) {
        const [x2, y2] = pos[to];
        const p = el("path", { class: "bearing", d: `M${F.x},${F.y} L${x2},${y2}` }, bearings);
        p.dataset.len = Math.hypot(x2 - F.x, y2 - F.y);
        if (label) el("text", { class: "bearing-label", x: F.x + (x2 - F.x) * at + 6, y: F.y + (y2 - F.y) * at - 6 }, bearings, label);
      }
      const boats = el("g", {}, g);
      BOATS.forEach(([id, repo, agent], i) => {
        const [x, y] = places[i];
        const b = el("g", { tabindex: 0, role: "button", "aria-label": `${repo}, a ${agent} session`, "data-id": id }, boats);
        boat(b, x, y, F.heading, 20, false);
        const t = el("text", { class: "boat-label", x: x - 14, y: y + 22 }, b);
        el("tspan", {}, t, repo);
        el("tspan", { class: "sub", x: x - 14, dy: 12 }, t, agent);
        hoverable(b, `A ${agent} session working in ${repo}.`, "The orchestrator hands it tasks; it reports to the ledger.");
      });
      const lead = el("g", { "data-id": "orchestrator" }, boats);
      boat(lead, F.x, F.y, F.heading, 30, true);
      const lt = el("text", { class: "boat-label big", x: F.x + 18, y: F.y + 20 }, lead);
      el("tspan", {}, lt, "Orchestrator");
      el("tspan", { class: "sub", x: F.x + 18, dy: 13 }, lt, "the bosun on deck");
    }

    function furniture(L, vertical, g) {
      const [nx, ny] = L.next;
      const pill = el("g", { class: "next-pill" }, g);
      el("rect", { x: nx, y: ny - 12, width: 40, height: 17, rx: 8.5 }, pill);
      el("text", { x: nx + 20, y: ny + 0.5, "text-anchor": "middle" }, pill, "Next");
      const ut = el("text", { class: "uncharted", x: nx, y: ny + 22 }, g);
      const lines = vertical
        ? [["h", "Not charted yet: a teammate's chart,"], ["", "merged with yours from their journals."]]
        : [["h", "Not charted yet"], ["", "A teammate's chart,"], ["", "merged with yours"], ["", "from their journals."]];
      lines.forEach(([cls, line], i) => el("tspan", { class: cls, x: nx, dy: i ? (vertical ? 15 : i === 1 ? 16 : 15) : 0 }, ut, line));

      const [rx, ry, rr] = L.rose;
      const rose = el("g", { class: "rose", "aria-hidden": "true" }, g);
      el("circle", { cx: rx, cy: ry, r: rr }, rose);
      el("circle", { cx: rx, cy: ry, r: rr * 0.78 }, rose);
      for (let i = 0; i < 32; i++) {
        const t = (i / 32) * Math.PI * 2;
        const l = i % 4 === 0 ? 0.78 : 0.88;
        el("line", { x1: rx + Math.cos(t) * rr * l, y1: ry + Math.sin(t) * rr * l, x2: rx + Math.cos(t) * rr, y2: ry + Math.sin(t) * rr }, rose);
      }
      for (let i = 0; i < 4; i++) {
        const t = (i / 4) * Math.PI * 2 - Math.PI / 2;
        const n = rr * 0.74;
        const w = rr * 0.16;
        const tip_ = [rx + Math.cos(t) * n, ry + Math.sin(t) * n];
        const l = [rx + Math.cos(t - Math.PI / 2) * w, ry + Math.sin(t - Math.PI / 2) * w];
        const r = [rx + Math.cos(t + Math.PI / 2) * w, ry + Math.sin(t + Math.PI / 2) * w];
        el("path", { d: `M${rx},${ry} L${l[0]},${l[1]} L${tip_[0]},${tip_[1]}Z` }, rose);
        el("path", { class: "light", d: `M${rx},${ry} L${r[0]},${r[1]} L${tip_[0]},${tip_[1]}Z` }, rose);
      }
      el("text", { x: rx, y: ry - rr - 4 }, rose, "N");

      const [cx0, cy0, cw, ch] = L.cartouche;
      const ca = el("g", { class: "cartouche" }, svg);
      el("rect", { x: cx0, y: cy0, width: cw, height: ch }, ca);
      el("rect", { x: cx0 + 3, y: cy0 + 3, width: cw - 6, height: ch - 6 }, ca);
      el("text", { class: "t", x: cx0 + 14, y: cy0 + 26 }, ca, "The ontology");
      el("text", { class: "s", x: cx0 + 14, y: cy0 + 44 }, ca, "How your organization works.");
      el("text", { class: "s", x: cx0 + 14, y: cy0 + 58 }, ca, "Every fact with its source.");

      /* The neat line with a border scale, as charts have */
      const frame = el("g", {}, svg);
      el("rect", { class: "neat-outer", x: 1, y: 1, width: L.w - 2, height: L.h - 2 }, frame);
      el("rect", { class: "neat", x: 10, y: 10, width: L.w - 20, height: L.h - 20 }, frame);
      const step = 20;
      for (let x = 10, i = 0; x < L.w - 10; x += step, i++) {
        if (i % 2) continue;
        el("rect", { class: "tick", x, y: 7, width: Math.min(step, L.w - 10 - x), height: 3 }, frame);
        el("rect", { class: "tick", x, y: L.h - 10, width: Math.min(step, L.w - 10 - x), height: 3 }, frame);
      }
      for (let y = 10, i = 0; y < L.h - 10; y += step, i++) {
        if (i % 2) continue;
        el("rect", { class: "tick", x: 7, y, width: 3, height: Math.min(step, L.h - 10 - y) }, frame);
        el("rect", { class: "tick", x: L.w - 10, y, width: 3, height: Math.min(step, L.h - 10 - y) }, frame);
      }
    }

    /* The bearings draw themselves once, when the chart comes into view. */
    function setBearings() {
      svg.querySelectorAll(".bearing").forEach((p) => {
        const len = parseFloat(p.dataset.len);
        p.style.strokeDasharray = shown ? "" : `${len} ${len}`;
        p.style.strokeDashoffset = shown ? "" : String(len);
      });
    }

    function draw() {
      const L = narrowQ.matches ? LAYOUTS.narrow : LAYOUTS.wide;
      const vertical = L.edgeAxis === "y";
      svg.querySelectorAll(":scope > g, :scope > defs, :scope > rect").forEach((n) => n.remove());
      svg.setAttribute("viewBox", `0 0 ${L.w} ${L.h}`);
      const g = sea(L, vertical, el("defs", {}, svg));
      const pos = {};
      for (const [id, island] of MARKS) {
        const [cx, cy] = L.islands[island];
        const [dx, dy] = L.marks[id];
        pos[id] = [cx + dx, cy + dy];
      }
      landmarks(L, vertical, g, pos);
      fleet(L, g, pos);
      furniture(L, vertical, g);
      highlight(current);
      setBearings();
    }

    if (!shown && "IntersectionObserver" in window) {
      const io = new IntersectionObserver((es) => {
        if (!es.some((e) => e.isIntersecting)) return;
        io.disconnect();
        svg.querySelectorAll(".bearing").forEach((p) => {
          p.classList.add("is-drawing");
          p.style.strokeDashoffset = "0";
          p.addEventListener("transitionend", () => (p.style.strokeDasharray = ""), { once: true });
        });
        shown = true;
      }, { threshold: 0.4 });
      io.observe(svg);
    } else shown = true;

    /* A log entry shows where it sits on the chart */
    if (log) {
      log.addEventListener("click", (e) => {
        const b = e.target.closest("button[data-at]");
        if (!b) return;
        const on = b.getAttribute("aria-pressed") !== "true";
        log.querySelectorAll("button[data-at]").forEach((x) => x.setAttribute("aria-pressed", "false"));
        b.setAttribute("aria-pressed", String(on));
        current = on ? b.dataset.at : null;
        highlight(current);
      });
    }
    root.querySelector(".bx-demo__fallback")?.remove();
    draw();
    narrowQ.addEventListener("change", draw);
  }

  document.querySelectorAll("[data-picture='chart']").forEach(Chart);
})();
