/* The home page's whiteboard ("How does it all fit together?"): the whole of bosun as someone would sketch
   it for a new teammate (docs/index.md). rough.js (vendor/) draws it into the SVG from one table of positions
   per layout (wide, phone). Every shape keeps its seed, so it looks the same on every visit. Colours go to
   rough.js as marker values and are swapped for the site's tokens afterwards, so the light and dark schemes
   need no redraw. It draws itself once, stage by stage, when it comes into view; "Draw it again" replays it. */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const narrowQ = window.matchMedia("(max-width: 640px)");
  const INK = "#000001";
  const TEAL = "#000002";
  const AMBER = "#000003";
  const AMBER_SOFT = "#000004";
  const MUTED = "#000005";
  const TEAL_SOFT = "#000006";
  const SURFACE = "#000008";
  const TOKENS = {
    [INK]: "var(--bx-ink)",
    [TEAL]: "var(--bx-teal)",
    [AMBER]: "var(--bx-amber)",
    [AMBER_SOFT]: "var(--bx-amber-soft)",
    [MUTED]: "var(--bx-muted)",
    [TEAL_SOFT]: "var(--bx-teal-soft)",
    [SURFACE]: "var(--bx-surface)",
  };

  function Board(root) {
    const svg = root.querySelector("svg");
    let rc;
    let seed;

    function paint(node) {
      node.querySelectorAll("path").forEach((p) => {
        for (const a of ["stroke", "fill"]) {
          const v = p.getAttribute(a);
          if (v && TOKENS[v]) {
            p.style[a] = TOKENS[v];
            p.removeAttribute(a);
          }
        }
      });
      return node;
    }
    function opts(o) {
      return Object.assign({ seed: ++seed, roughness: 1.1, bowing: 1.2, stroke: INK, strokeWidth: 1.6 }, o || {});
    }
    function text(g, x, y, str, cls, anchor, style) {
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", x);
      t.setAttribute("y", y);
      t.setAttribute("class", cls);
      if (anchor) t.setAttribute("text-anchor", anchor);
      if (style) t.setAttribute("style", style);
      str.split("\n").forEach((line, i) => {
        const s = document.createElementNS(NS, "tspan");
        s.setAttribute("x", x);
        if (i) s.setAttribute("dy", "1.3em");
        s.textContent = line;
        t.appendChild(s);
      });
      g.appendChild(t);
      return t;
    }
    const add = (g, node) => (g.appendChild(paint(node)), node);
    function arrow(g, pts, o) {
      o = Object.assign({ stroke: TEAL, strokeWidth: 1.7 }, o || {});
      add(g, rc.curve(pts, opts(o)));
      const [a, b] = [pts[pts.length - 2], pts[pts.length - 1]];
      const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
      const h = 11;
      const l = [b[0] - h * Math.cos(ang - 0.45), b[1] - h * Math.sin(ang - 0.45)];
      const r = [b[0] - h * Math.cos(ang + 0.45), b[1] - h * Math.sin(ang + 0.45)];
      add(g, rc.linearPath([l, b, r], opts({ stroke: o.stroke, strokeWidth: o.strokeWidth, roughness: 0.6 })));
    }
    function num(g, x, y, n, dashed) {
      add(g, rc.circle(x, y, 26, opts({ stroke: dashed ? MUTED : TEAL, strokeWidth: 1.5, roughness: 1.4, strokeLineDash: dashed ? [4, 3] : undefined })));
      text(g, x, y + 5, String(n), "t-num" + (dashed ? " is-next" : ""));
    }

    /* ---- The blocks, each drawn from its own origin ---- */
    const BLOCKS = {
      inputs(g, x, y, grid) {
        text(g, x, y, "Comes in", "t-title");
        ["Slack", "Pull requests", "Replies you wait on", "Tickets"].forEach((s, i) => {
          const cx = grid ? x + (i % 2) * 188 : x;
          const cy = grid ? y + 16 + Math.floor(i / 2) * 50 : y + 16 + i * 46;
          add(g, rc.rectangle(cx, cy, grid ? 176 : 160, 36, opts({ roughness: 0.9, strokeWidth: 1.3 })));
          text(g, cx + 12, cy + 23, s, "t-body t-ink");
        });
      },
      orch(g, cx, cy) {
        add(g, rc.ellipse(cx, cy, 200, 92, opts({ stroke: TEAL, strokeWidth: 2.2, fill: TEAL_SOFT, fillStyle: "solid" })));
        text(g, cx, cy - 4, "Orchestrator", "t-title", "middle");
        text(g, cx, cy + 17, "one Claude Code session", "t-body", "middle");
      },
      agents(g, x, y, w, title) {
        text(g, x, y, title, "t-title");
        [["acme/exporter", "Claude Code"], ["acme/web", "Codex"], ["acme/infra", "Antigravity"]].forEach(([repo, agent], i) => {
          const yy = y + 16 + i * 44;
          add(g, rc.rectangle(x, yy, w, 36, opts({ roughness: 0.9, strokeWidth: 1.3 })));
          text(g, x + 10, yy + 23, repo, "t-mono t-ink");
          text(g, x + w - 10, yy + 23, agent, "t-body t-small", "end");
        });
      },
      ledger(g, x, y) {
        num(g, x + 14, y - 6, 1);
        text(g, x + 36, y, "The ledger", "t-title");
        text(g, x + 36, y + 20, "every piece of work, typed, with rules", "t-body");
        add(g, rc.rectangle(x + 16, y + 44, 276, 176, opts({ roughness: 1.3, strokeWidth: 1.2, stroke: MUTED })));
        add(g, rc.rectangle(x + 8, y + 38, 276, 176, opts({ roughness: 1.3, strokeWidth: 1.2, stroke: MUTED })));
        add(g, rc.rectangle(x, y + 32, 276, 176, opts({ fill: SURFACE, fillStyle: "solid", strokeWidth: 1.6 })));
        text(g, x + 14, y + 56, "task:2026-10-20/veembc", "t-mono");
        text(g, x + 14, y + 78, "Export credit notes", "t-body t-ink t-bold");
        /* its states, and what each move needs */
        const st = [["proposed", 14], ["accepted", 88], ["delegated", 160], ["done", 238]];
        st.forEach(([s, dx], i) => {
          text(g, x + dx, y + 116, s, "t-chip" + (i === 3 ? " t-ink" : ""));
          if (i < 3) arrow(g, [[x + dx + 54 + (i === 2 ? 8 : 0), y + 112], [x + st[i + 1][1] - 6, y + 112]], { strokeWidth: 1.2, stroke: MUTED });
        });
        text(g, x + 92, y + 138, "your word", "t-note");
        text(g, x + 206, y + 138, "evidence", "t-note");
        text(g, x + 14, y + 172, "spawned_from  produces  tracks", "t-mono t-teal");
        text(g, x + 14, y + 192, "a mention, a pull request, a ticket", "t-body t-small");
      },
      kg(g, x, y) {
        num(g, x + 14, y - 6, 2);
        text(g, x + 36, y, "The ontology", "t-title");
        text(g, x + 36, y + 20, "how your organization works", "t-body");
        const A = [x + 160, y + 96];
        const B = [x + 40, y + 222];
        const C = [x + 280, y + 222];
        add(g, rc.polygon([A, B, C], opts({ stroke: MUTED, strokeWidth: 1.2, roughness: 1.4 })));
        [[A, "People", AMBER], [B, "Process", TEAL], [C, "Product", INK]].forEach(([p, s, col], i) => {
          add(g, rc.circle(p[0], p[1], 46, opts({ stroke: col, fill: col, fillStyle: "hachure", hachureGap: 5, fillWeight: 1, strokeWidth: 1.6 })));
          text(g, p[0], p[1] + (i === 0 ? -32 : 42), s, "t-title t-corner", "middle");
        });
        text(g, x + 160, y + 172, "Dana reviews exporter", "t-note t-quiet", "middle");
        text(g, x + 160, y + 190, "source: a review", "t-mono", "middle");
      },
      sticky(g, x, y, w, h, lines, tilt) {
        const s = document.createElementNS(NS, "g");
        s.setAttribute("transform", `rotate(${tilt} ${x + w / 2} ${y + h / 2})`);
        g.appendChild(s);
        add(s, rc.rectangle(x, y, w, h, opts({ stroke: AMBER, fill: AMBER_SOFT, fillStyle: "solid", roughness: 0.8, strokeWidth: 1.4 })));
        text(s, x + 14, y + 28, lines, "t-sticky");
      },
      next(g, x, y, w, h, narrow) {
        add(g, rc.rectangle(x, y, w, h, opts({ stroke: MUTED, strokeLineDash: [8, 6], roughness: 1.2, strokeWidth: 1.4 })));
        num(g, x + 26, y + 30, 4, true);
        const tx = x + 50;
        add(g, rc.rectangle(tx, y + 15, 48, 22, opts({ stroke: MUTED, strokeLineDash: [3, 3], roughness: 0.6, strokeWidth: 1.1 })));
        text(g, tx + 24, y + 31, "Next", "t-next", "middle");
        text(g, tx + 60, y + 32, narrow ? "One board for a team" : "The same ledger and ontology, for a whole organization", "t-title t-next-title");
        text(g, tx, y + 58, narrow ? "Macs merge their journals;\nsync for one person first." : "Each Mac's ledger and ontology are journals that merge. Sync for one person comes first.", "t-body");
        const bx = narrow ? x + w / 2 : x + w - 150;
        const by = narrow ? y + 128 : y + h / 2;
        add(g, rc.rectangle(bx - 34, by - 14, 68, 28, opts({ stroke: MUTED, strokeLineDash: [4, 3], strokeWidth: 1.2 })));
        text(g, bx, by + 5, "board", "t-body t-small", "middle");
        const macs = narrow ? [[-120, -14], [-120, 18], [120, -14], [120, 18]] : [[-110, -26], [-110, 22], [110, -26], [110, 22]];
        macs.forEach(([dx, dy]) => {
          add(g, rc.rectangle(bx + dx - 18, by + dy - 11, 36, 22, opts({ stroke: MUTED, strokeWidth: 1.1, roughness: 0.9 })));
          add(g, rc.line(bx + dx + (dx < 0 ? 18 : -18), by + dy, bx + (dx < 0 ? -34 : 34), by, opts({ stroke: MUTED, strokeWidth: 1, strokeLineDash: [3, 4] })));
        });
      },
    };

    /* ---- Where each block goes, and the arrows between them, in drawing order (the stages) ---- */
    const WIDE = {
      w: 1000, h: 820,
      stages: [
        (g) => BLOCKS.inputs(g, 30, 52, false),
        (g) => {
          BLOCKS.orch(g, 330, 150);
          [80, 126, 172, 218].forEach((yy) => arrow(g, [[194, yy], [214, yy], [228, 150 + (yy - 150) * 0.25]], { strokeWidth: 1.3, stroke: MUTED }));
          text(g, 236, 96, "events", "t-note");
        },
        (g) => {
          BLOCKS.ledger(g, 250, 330);
          arrow(g, [[340, 198], [350, 256], [372, 304]]);
          text(g, 372, 262, "records", "t-note");
        },
        (g) => {
          BLOCKS.agents(g, 744, 52, 232, "One agent per repository");
          arrow(g, [[432, 134], [600, 104], [732, 100]]);
          text(g, 560, 94, "hands over each task", "t-note");
          arrow(g, [[820, 202], [700, 260], [572, 352]], {});
          text(g, 700, 236, "reports", "t-note");
        },
        (g) => BLOCKS.kg(g, 650, 320),
        (g) => {
          arrow(g, [[952, 520], [940, 630], [600, 660], [300, 640], [214, 610]], { stroke: TEAL, strokeWidth: 2.2 });
          num(g, 470, 690, 3);
          text(g, 494, 696, "Every evening, it learns", "t-title");
          text(g, 494, 716, "reads the day's sessions and the ledger, finds what went wrong", "t-body");
        },
        (g) => {
          BLOCKS.sticky(g, 30, 556, 176, 92, "You approve\nwhat it learns.", -3);
          BLOCKS.sticky(g, 30, 292, 186, 92, "Nothing goes out\nuntil you\napprove it.", 2);
          arrow(g, [[258, 184], [236, 236], [200, 284]], { stroke: AMBER, strokeWidth: 1.5 });
          arrow(g, [[120, 392], [118, 420], [122, 440]], { stroke: AMBER, strokeWidth: 1.5 });
          text(g, 40, 466, "then Slack, GitHub,\nyour tracker", "t-body");
        },
        (g) => BLOCKS.next(g, 30, 736, 940, 74, false),
      ],
    };
    const NARROW = {
      w: 400, h: 1460,
      stages: [
        (g) => BLOCKS.inputs(g, 12, 30, true),
        (g) => {
          BLOCKS.orch(g, 200, 202);
          arrow(g, [[200, 140], [200, 152]], { strokeWidth: 1.3, stroke: MUTED });
        },
        (g) => {
          BLOCKS.ledger(g, 58, 520);
          arrow(g, [[190, 250], [188, 380], [184, 486]]);
          text(g, 104, 470, "records", "t-note");
        },
        (g) => {
          BLOCKS.agents(g, 200, 300, 192, "One per repository");
          arrow(g, [[270, 240], [290, 262], [296, 282]]);
          text(g, 300, 268, "tasks", "t-note");
          arrow(g, [[390, 440], [390, 480], [340, 540]]);
          text(g, 312, 470, "reports", "t-note");
        },
        (g) => BLOCKS.kg(g, 40, 800),
        (g) => {
          arrow(g, [[372, 1060], [380, 1100], [300, 1110]], { stroke: TEAL, strokeWidth: 2 });
          arrow(g, [[20, 1050], [8, 920], [14, 700], [44, 600]], { stroke: TEAL, strokeWidth: 2 });
          num(g, 24, 1102, 3);
          text(g, 46, 1108, "Every evening, it learns", "t-title");
          text(g, 46, 1130, "reads the day's sessions and the\nledger, finds what went wrong", "t-body");
        },
        (g) => {
          BLOCKS.sticky(g, 110, 1178, 186, 84, "You approve\nwhat it learns.", -3);
          BLOCKS.sticky(g, 8, 300, 164, 92, "Nothing goes out\nuntil you\napprove it.", -2);
          arrow(g, [[130, 236], [110, 262], [100, 290]], { stroke: AMBER, strokeWidth: 1.5 });
          text(g, 16, 432, "then Slack, GitHub,\nyour tracker", "t-body");
        },
        (g) => BLOCKS.next(g, 10, 1298, 380, 156, true),
      ],
    };

    let stages = [];
    function draw() {
      const L = narrowQ.matches ? NARROW : WIDE;
      svg.querySelectorAll(":scope > g").forEach((n) => n.remove());
      svg.setAttribute("viewBox", `0 0 ${L.w} ${L.h}`);
      rc = window.rough.svg(svg);
      seed = 7;
      stages = L.stages.map((fn) => {
        const g = document.createElementNS(NS, "g");
        g.setAttribute("class", "stage");
        svg.appendChild(g);
        fn(g);
        return g;
      });
    }

    /* Drawn stroke by stroke, a stage at a time; with reduced motion, all at once. */
    function prepare() {
      for (const g of stages) {
        g.style.opacity = 0;
        g.querySelectorAll("path").forEach((p) => {
          if (p.getAttribute("stroke-dasharray") || (p.style.fill && p.style.fill !== "none")) return;
          const len = p.getTotalLength();
          p.style.strokeDasharray = `${len} ${len}`;
          p.style.strokeDashoffset = String(len);
        });
      }
    }
    let run = 0;
    async function play() {
      const mine = ++run;
      draw();
      if (reduce.matches) return;
      prepare();
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      for (const g of stages) {
        if (mine !== run) return;
        g.classList.add("is-drawing");
        g.style.opacity = 1;
        g.querySelectorAll("path").forEach((p) => (p.style.strokeDashoffset = "0"));
        await new Promise((r) => setTimeout(r, 520));
      }
    }

    root.querySelector(".bx-demo__fallback")?.remove();
    const replay = root.querySelector(".bx-board__replay");
    replay.hidden = false;
    draw();
    if (!reduce.matches && "IntersectionObserver" in window) {
      prepare();
      const io = new IntersectionObserver((es) => {
        if (!es.some((e) => e.isIntersecting)) return;
        io.disconnect();
        play();
      }, { threshold: 0.3 });
      io.observe(svg);
    }
    replay.addEventListener("click", play);
    narrowQ.addEventListener("change", () => {
      run++;
      draw();
    });
  }

  if (window.rough) document.querySelectorAll("[data-picture='whiteboard']").forEach(Board);
})();
