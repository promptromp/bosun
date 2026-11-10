/* The home page's live examples (docs/index.md, theme/home.html).

   Plain JavaScript with no build step and nothing loaded from elsewhere. Each example draws into
   its placeholder (an element with data-example) from the JSON the page carries (#bosun-examples),
   which tools/docs_examples.py makes from bosun's own tables and its fictional acme data: the
   ledger's state machines, the console's fixture ledger, the fixture knowledge base and the
   console's map layout. Text from the data is only ever set as text, never as markup. */
(function () {
  "use strict";

  const SVG = "http://www.w3.org/2000/svg";
  const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- Small helpers ---------- */

  function build(el, attrs, children) {
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === false || value === null || value === undefined) continue;
      if (key === "class") el.setAttribute("class", value);
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : String(child));
    }
    return el;
  }
  function h(tag, attrs, ...children) {
    return build(document.createElement(tag), attrs, children);
  }
  function s(tag, attrs, ...children) {
    return build(document.createElementNS(SVG, tag), attrs, children);
  }
  function bar(title, extra) {
    return h("div", { class: "bx-demo__bar" }, h("strong", null, title), extra || h("span", null, "Live, with fictional data"));
  }
  function said(answer) {
    if (!answer) return null;
    return h(
      "div",
      { class: answer.ok ? "bx-said" : "bx-said bx-said--no", role: "status" },
      h("b", null, answer.title),
      answer.text,
    );
  }
  function shorten(text, limit) {
    return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
  }

  /* ---------- The ledger: an ask, what came of it, and the rules on each item ---------- */

  const KIND_NAMES = {
    mention: "Slack mention",
    task: "Task",
    "my-pr": "Your pull request",
    ticket: "Ticket",
    outreach: "Question",
    "review-request": "Review request",
    learning: "Learning",
  };
  const KIND_HELP = {
    mention: "A Slack message that may need you.",
    task: "Work we decided to do. Only your word accepts it, and it is done when what it produced has landed.",
    "my-pr": "Your own pull request, followed until it is merged or closed.",
    ticket: "An issue in your tracker, mirrored read-only: its state is whatever the tracker says.",
    outreach: "Something we asked someone and wait on. Recording the reply needs proof: a link to the reply itself.",
  };
  // What each kind of proof looks like in this story, for the moves that need some.
  const PROOFS = {
    outreach: { good: "Dana's reply, with its Slack link", goodEvidence: "https://acme.slack.com/archives/D0000000001/p1792490000000200", bad: "Nothing to link" },
    task: { good: 'You said "go ahead"', goodEvidence: "user: go ahead", bad: "Priya asked for it" },
  };
  const REL_WORDS = { spawned_from: "spawned", produces: "produced", tracks: "tracked by", parent: "waiting on" };

  function ledgerExample(root, data) {
    const story = data.ledger;
    const kinds = story.kinds;
    let items;
    let selected;
    let answer;
    let proof;

    function reset() {
      items = new Map(story.items.map((item) => [item.id, Object.assign({}, item)]));
      selected = story.focus;
      answer = null;
      proof = null;
    }

    function openOutreach(parentId) {
      return [...items.values()].filter(
        (item) => item.kind === "outreach" && item.fields.parent === parentId && !kinds.outreach.terminal.includes(item.state),
      );
    }

    // The rows of `ledger tree`: each item under the one it came from, with how they are linked.
    function rows() {
      const under = new Map();
      const add = (parent, child, rel) => {
        if (!under.has(parent)) under.set(parent, []);
        under.get(parent).push({ id: child, rel: rel });
      };
      const children = new Set();
      for (const item of items.values()) {
        for (const link of item.links) {
          if (link.rel === "spawned_from") add(link.id, item.id, "spawned_from");
          if (link.rel === "produces" || link.rel === "tracks") add(item.id, link.id, link.rel);
          if (["spawned_from", "produces", "tracks"].includes(link.rel)) children.add(link.rel === "spawned_from" ? item.id : link.id);
        }
        if (item.fields.parent) {
          add(item.fields.parent, item.id, "parent");
          children.add(item.id);
        }
      }
      const out = [];
      const walk = (id, depth, rel) => {
        out.push({ id: id, depth: depth, rel: rel });
        for (const child of under.get(id) || []) walk(child.id, depth + 1, child.rel);
      };
      for (const item of items.values()) if (!children.has(item.id)) walk(item.id, 0, "");
      return out;
    }

    function stateClass(item) {
      const kind = kinds[item.kind];
      if (kind.terminal.includes(item.state)) return "bx-state bx-state--closed";
      if (item.state === "proposed") return "bx-state bx-state--you";
      return "bx-state";
    }

    function itemTitle(item) {
      if (item.kind === "ticket") return item.id.split(":")[1] + ": " + item.title;
      if (item.kind === "my-pr") return "PR #" + item.id.split("#")[1] + ": " + item.title;
      if (item.kind === "outreach") return "to " + item.fields.to + ": " + item.title;
      return item.title;
    }

    function row(entry) {
      const item = items.get(entry.id);
      const done = kinds[item.kind].terminal.includes(item.state);
      const carriedOn = item.links.find((link) => link.rel === "duplicate_of" || link.rel === "folded_into");
      return h(
        "li",
        { class: "bx-tree__depth-" + Math.min(entry.depth, 2) },
        h(
          "button",
          {
            type: "button",
            class: "bx-tree__row" + (done ? " bx-tree__row--done" : ""),
            "aria-pressed": String(entry.id === selected),
            onclick: () => {
              selected = entry.id;
              answer = null;
              proof = null;
              draw();
            },
          },
          h(
            "span",
            { class: "bx-tree__what" },
            entry.rel ? h("span", { class: "bx-tree__rel" }, REL_WORDS[entry.rel] || entry.rel) : null,
            h("span", { class: "bx-tree__rel" }, KIND_NAMES[item.kind] || item.kind),
            h("span", { class: "bx-tree__title" }, shorten(itemTitle(item), 64)),
            carriedOn ? h("span", { class: "bx-tree__rel" }, " (" + carriedOn.rel.replace("_", " ") + " the task above)") : null,
          ),
          h("span", { class: stateClass(item) }, item.state),
        ),
      );
    }

    function refuse(title, text) {
      answer = { ok: false, title: title, text: text };
    }

    function move(item, target, evidence) {
      const kind = kinds[item.kind];
      const from = item.state;
      const needsProof = kind.evidence_required.some((m) => m[0] === from && m[1] === target);
      const needsMe = kind.evidence_from_me.includes(target);
      if (kind.mirror) {
        refuse("Refused: a ticket mirrors its tracker", item.id + " changes only with what the orchestrator read in the tracker. Bosun never moves a ticket itself; it proposes the change for you to make there.");
      } else if ((needsProof || needsMe) && evidence === undefined) {
        proof = target;
        answer = null;
      } else if (needsMe && !String(evidence).startsWith("user:")) {
        refuse("Refused: only your word accepts work", item.id + ": " + target + " needs your approval as 'user: <what you said>'. A colleague's message never accepts work.");
      } else if (needsProof && !evidence) {
        refuse("Refused: this move needs proof", item.id + ": " + from + " -> " + target + " needs evidence: " + kind.evidence);
      } else if (item.kind === "outreach" && from === "sent" && target === "applied" && item.fields.purpose !== "fyi") {
        refuse("Refused: a question needs its reply first", item.id + ": sent -> applied is only for one-way messages, which need no reply. Record the reply first.");
      } else if (kind.terminal.includes(target) && openOutreach(item.id).length) {
        const waiting = openOutreach(item.id).map((o) => o.id + " (" + o.state + ", to " + o.fields.to + ")");
        refuse("Refused: it is still waiting on someone", item.id + " is waiting on " + waiting.join(", ") + ". Hand the reply over, or drop the question if it no longer matters.");
      } else {
        item.state = target;
        proof = null;
        answer = {
          ok: true,
          title: "Moved: " + from + " to " + target,
          text: evidence ? "Recorded with its evidence: " + evidence : "Recorded in the item's history.",
        };
      }
    }

    function mismatch(item) {
      if (item.kind !== "ticket" || !kinds.ticket.terminal.includes(item.state)) return null;
      const open = [...items.values()].filter(
        (other) => other.links.some((link) => link.rel === "tracks" && link.id === item.id) && !kinds[other.kind].terminal.includes(other.state),
      );
      if (!open.length) return null;
      return h(
        "div",
        { class: "bx-said bx-said--no" },
        h("b", null, "The tracker and the work disagree"),
        item.id.split(":")[1] + " is " + item.state + " in Jira, but " + open.map((o) => o.id).join(", ") + " is still open. Bosun proposes the change for you to make in Jira.",
      );
    }

    function panel() {
      const item = items.get(selected);
      const kind = kinds[item.kind];
      const targets = (kind.transitions[item.state] || []).slice().sort((a, b) => kind.states.indexOf(a) - kind.states.indexOf(b));
      const proofs = PROOFS[item.kind] || { good: "With proof", goodEvidence: "a reference", bad: "Without proof" };
      const carriedOn = item.links.find((link) => link.rel === "duplicate_of" || link.rel === "folded_into");
      return h(
        "div",
        { class: "bx-item" },
        h(
          "div",
          { class: "bx-item__head" },
          h("span", { class: "bx-item__kind" }, KIND_NAMES[item.kind] || item.kind),
          h("span", { class: "bx-id" }, item.id),
        ),
        h("p", { class: "bx-item__desc" }, KIND_HELP[item.kind] || kind.description),
        h(
          "ul",
          { class: "bx-machine", "aria-label": "Its states" },
          kind.states.map((state) =>
            h(
              "li",
              {
                class: "bx-machine__state" + (state === item.state ? " bx-machine__state--now" : targets.includes(state) ? " bx-machine__state--next" : ""),
              },
              state,
            ),
          ),
        ),
        kind.mirror
          ? h(
              "div",
              { class: "bx-moves" },
              h("button", { type: "button", class: "bx-btn", onclick: () => { move(item, "in-progress"); draw(); } }, "Try to move it in bosun"),
            )
          : targets.length
            ? h(
                "div",
                { class: "bx-moves" },
                h("span", { class: "bx-moves__label" }, "Move to"),
                targets.map((target) =>
                  h("button", { type: "button", class: "bx-btn", onclick: () => { move(item, target); draw(); } }, target),
                ),
              )
            : h("p", { class: "bx-fine" }, "Finished" + (carriedOn ? ": " + carriedOn.rel.replace("_", " ") + " " + carriedOn.id + "." : ".")),
        proof
          ? h(
              "div",
              { class: "bx-proof" },
              h("span", { class: "bx-moves__label" }, "Moving to " + proof + ". The proof:"),
              h("button", { type: "button", class: "bx-btn bx-btn--go", onclick: () => { move(item, proof, proofs.goodEvidence); draw(); } }, proofs.good),
              h("button", { type: "button", class: "bx-btn", onclick: () => { move(item, proof, ""); draw(); } }, proofs.bad),
            )
          : null,
        said(answer),
        mismatch(item),
      );
    }

    function draw() {
      root.replaceChildren(
        bar(
          "The ledger, around one ask",
          h("button", { type: "button", class: "bx-btn bx-btn--quiet", onclick: () => { reset(); draw(); } }, "Start again"),
        ),
        h("ul", { class: "bx-tree", "aria-label": "What came of the ask" }, rows().map(row)),
        panel(),
      );
    }

    reset();
    draw();
  }

  /* ---------- Needs you: the exact actions waiting for your yes ---------- */

  function needsYouExample(root, data) {
    let pending;
    let done;
    let editing;

    function reset() {
      const order = ["outreach", "task", "learning"];
      pending = data.needs_you
        .map((item) => Object.assign({ text: item.action }, item))
        .sort((x, y) => order.indexOf(x.kind) - order.indexOf(y.kind));
      done = [];
      editing = null;
    }

    function finish(item, line) {
      pending = pending.filter((other) => other.id !== item.id);
      done.unshift(line);
      editing = null;
      draw();
    }

    function card(item) {
      const changed = item.text !== item.action;
      if (item.kind === "outreach") {
        const cut = item.action.indexOf(": ");
        const where = item.action.slice(0, cut);
        const to = where.split(" to ").pop();
        return h(
          "li",
          null,
          h("div", { class: "bx-needs__title" }, where),
          editing === item.id
            ? h("textarea", {
                "aria-label": "The message",
                oninput: (event) => {
                  item.text = item.action.slice(0, cut + 2) + event.target.value;
                  const note = event.target.parentElement.querySelector(".bx-needs__changed");
                  note.hidden = item.text === item.action;
                },
              }, item.text.slice(cut + 2))
            : h("p", { class: "bx-needs__action" }, "“" + item.text.slice(cut + 2) + "”"),
          h("p", { class: "bx-needs__changed", hidden: !changed }, "Changed. That is a new message, so it needs your yes again."),
          h(
            "div",
            { class: "bx-needs__buttons" },
            h("button", {
              type: "button",
              class: "bx-btn bx-btn--go",
              onclick: () => finish(item, changed ? "Sent your wording to " + to + ", as you approved it." : "Sent to " + to + " exactly as written. Had an agent changed a word, the guard would have stopped it and asked you again."),
            }, changed ? "Approve this wording" : "Approve"),
            editing === item.id
              ? null
              : h("button", { type: "button", class: "bx-btn", onclick: () => { editing = item.id; draw(); } }, "Edit"),
            h("button", { type: "button", class: "bx-btn bx-btn--quiet", onclick: () => finish(item, "Not sent: you said no to the message to " + to + ".") }, "No"),
          ),
        );
      }
      const isTask = item.kind === "task";
      return h(
        "li",
        null,
        h("div", { class: "bx-needs__title" }, isTask ? "Take on: " + item.title : "Learning: " + item.title),
        h("p", { class: "bx-needs__action" + (isTask ? " bx-needs__action--command" : "") }, item.action),
        h(
          "div",
          { class: "bx-needs__buttons" },
          h("button", {
            type: "button",
            class: "bx-btn bx-btn--go",
            onclick: () => finish(item, isTask ? "Accepted on your word and handed to the exporter agent: " + item.title + "." : "Applied: " + item.action.replace(/^Add to /, "added to ") + ". The orchestrator reads it in its next conversation."),
          }, isTask ? "Accept" : "Apply"),
          h("button", { type: "button", class: "bx-btn bx-btn--quiet", onclick: () => finish(item, (isTask ? "Declined: " : "Rejected: ") + item.title + ".") }, isTask ? "Decline" : "Reject"),
        ),
      );
    }

    function draw() {
      root.replaceChildren(
        bar(pending.length ? pending.length + " need you" : "Nothing needs you", h("button", { type: "button", class: "bx-btn bx-btn--quiet", onclick: () => { reset(); draw(); } }, "Start again")),
        pending.length ? h("ul", { class: "bx-needs" }, pending.map(card)) : h("p", { class: "bx-fine" }, "Bosun carries on with the rest."),
        done.length ? h("ul", { class: "bx-done", role: "status" }, done.map((line) => h("li", null, line))) : null,
      );
    }

    reset();
    draw();
  }

  /* ---------- What it knows: the knowledge map, laid out as the console lays it out ---------- */

  const TYPE_NAMES = {
    process: "Process",
    step: "Step",
    gate: "Check",
    system: "System",
    channel: "Slack channel",
    repo: "Repository",
    environment: "Environment",
    lesson: "Lesson",
    person: "Person",
    team: "Team",
  };
  const PREDICATES = {
    has_step: (fact) => "has step " + fact.order + ":",
    rule: () => "has a rule:",
    actor: () => "is done by",
    posts_to: () => "posts to",
    entry_point: () => "starts in",
    depends_on: () => "depends on",
    reviews: () => "reviews",
    about: () => "is about",
    states: () => "says:",
    status: () => "is",
    caveat: () => "has a caveat:",
  };
  const AUTHORITY = { user_said: "you said so", stakeholder_said: "a colleague said so", observed: "seen in the work", inferred: "inferred" };

  function sourceName(source) {
    const [kind, rest] = [source.slice(0, source.indexOf(":")), source.slice(source.indexOf(":") + 1)];
    if (kind === "ledger") return rest.startsWith("mypr:") ? "PR " + rest.slice(5) : "ledger item " + rest;
    if (kind === "pr") return "PR " + rest;
    if (kind === "memory") return "memory note " + rest;
    if (kind === "report") return "a repo agent's report";
    if (kind === "transcript") return "what you said in a session";
    if (kind === "slack") return "a Slack message";
    return kind;
  }

  function knowledgeExample(root, data) {
    const k = data.knowledge;
    // The centre in the middle and its neighbours on an ellipse around it (the first one to the
    // left); each node further out sits beside the node it was reached from, away from the centre.
    const W = 900;
    const H = 470;
    const RING = [205, 128];
    const OUT = 215;
    const STACK = 90;
    function layout(view) {
      const at = new Map([[view.nodes[0].id, [W / 2, H / 2]]]);
      const first = view.nodes.filter((node) => node.depth === 1);
      // Two neighbours sit on a slant rather than level, so their labels and what lies beyond
      // them do not line up in one row.
      const tilt = first.length === 2 ? -0.45 : 0;
      first.forEach((node, i) => {
        const a = Math.PI + tilt + (i * 2 * Math.PI) / first.length;
        at.set(node.id, [W / 2 + RING[0] * Math.cos(a), H / 2 + RING[1] * Math.sin(a)]);
      });
      const byParent = new Map();
      for (const node of view.nodes.filter((n) => n.depth >= 2)) {
        if (!byParent.has(node.parent)) byParent.set(node.parent, []);
        byParent.get(node.parent).push(node);
      }
      // Parents first, nearest the centre first, so a third ring stands beside the second.
      for (const node of view.nodes) {
        const kids = byParent.get(node.id) || [];
        const [px, py] = at.get(node.id) || [W / 2, H / 2];
        const side = px < W / 2 ? -1 : 1;
        kids.forEach((kid, j) => {
          at.set(kid.id, [px + side * OUT, py + (j - (kids.length - 1) / 2) * STACK]);
        });
      }
      return new Map([...at].map(([id, xy]) => [id, [Math.round(xy[0]), Math.round(xy[1])]]));
    }
    let center = k.center;
    let shownAt = new Map();
    let shownBox = null;
    const status = k.facts.map((fact) => fact.status);
    const nodes = new Map();
    const svg = s("svg", { class: "bx-map", viewBox: "0 0 " + W + " " + H, role: "group", "aria-label": "The knowledge map" });
    const edgeLayer = s("g");
    const nodeLayer = s("g");
    svg.append(edgeLayer, nodeLayer);
    const details = h("div");

    function name(id) {
      return k.entities[id] ? k.entities[id].name : id;
    }
    function factsAbout(id) {
      return k.facts.map((fact, index) => ({ fact: fact, index: index })).filter((f) => f.fact.subject === id || f.fact.object === id);
    }
    function edgeStatus(a, b) {
      const behind = k.facts.map((fact, i) => ({ fact: fact, i: i })).filter(
        (f) => (f.fact.subject === a && f.fact.object === b) || (f.fact.subject === b && f.fact.object === a),
      );
      const live = behind.filter((f) => status[f.i] !== "rejected");
      if (!live.length) return "rejected";
      return live.some((f) => status[f.i] === "proposed") ? "proposed" : "confirmed";
    }

    function nodeFor(id) {
      if (nodes.has(id)) return nodes.get(id);
      const label = shorten(name(id), 22);
      const select = () => {
        if (id !== center) {
          center = id;
          draw(true);
        }
      };
      const group = s(
        "g",
        {
          class: "bx-map__node",
          tabindex: "0",
          role: "button",
          "aria-label": name(id) + ", " + (TYPE_NAMES[k.entities[id].type] || k.entities[id].type),
          onclick: select,
          onkeydown: (event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              select();
            }
          },
        },
        s("circle", { r: "18" }),
        s("text", { "text-anchor": "middle", y: "48" }, label),
      );
      nodes.set(id, group);
      return group;
    }

    function along(dy) {
      if (dy < -20) return 0.32;
      return dy > 20 ? 0.74 : 0.5;
    }

    function place(positions) {
      for (const [id, at] of positions) nodes.get(id).setAttribute("transform", "translate(" + at[0] + " " + at[1] + ")");
      edgeLayer.replaceChildren();
      for (const edge of k.views[center].edges) {
        const a = positions.get(edge.source);
        const b = positions.get(edge.target);
        const state = edgeStatus(edge.source, edge.target);
        if (!a || !b || state === "rejected") continue;
        // The label sits just above the line, clear of the node labels (below each node): nearer
        // the centre on a line going up, further along on one going down.
        const near = Math.hypot(a[0] - W / 2, a[1] - H / 2) <= Math.hypot(b[0] - W / 2, b[1] - H / 2) ? [a, b] : [b, a];
        const [dx, dy] = [near[1][0] - near[0][0], near[1][1] - near[0][1]];
        const length = Math.hypot(dx, dy) || 1;
        const up = dx >= 0 ? [dy / length, -dx / length] : [-dy / length, dx / length];
        edgeLayer.append(
          s("line", { class: "bx-map__edge" + (state === "proposed" ? " bx-map__edge--proposed" : ""), x1: a[0], y1: a[1], x2: b[0], y2: b[1] }),
          s(
            "text",
            {
              class: "bx-map__edge-label",
              x: near[0][0] + dx * along(dy) + up[0] * 16,
              y: near[0][1] + dy * along(dy) + up[1] * 16,
              "text-anchor": "middle",
            },
            edge.label.replace(/_/g, " "),
          ),
        );
      }
    }

    // Move the nodes from where they were to the new layout; new ones grow out of the clicked node.
    // The frame around a layout, with room for the labels, in the picture's own proportions: a
    // small neighbourhood is shown closer, never closer than MIN_FRAME.
    const MIN_FRAME = 560;
    function frame(at) {
      const xs = [...at.values()].map((xy) => xy[0]);
      const ys = [...at.values()].map((xy) => xy[1]);
      const [x0, x1, y0, y1] = [Math.min(...xs) - 120, Math.max(...xs) + 120, Math.min(...ys) - 45, Math.max(...ys) + 70];
      let w = Math.max(x1 - x0, MIN_FRAME);
      let hgt = y1 - y0;
      if (w / hgt > W / H) hgt = w / (W / H);
      else w = hgt * (W / H);
      return [(x0 + x1) / 2 - w / 2, (y0 + y1) / 2 - hgt / 2, w, hgt];
    }
    function show(at, box) {
      svg.setAttribute("viewBox", box.map((v) => Math.round(v)).join(" "));
      place(at);
    }

    // Move the nodes from where they were to the new layout, and the frame with them; new nodes
    // grow out of the node that was clicked.
    function animate(target) {
      const origin = shownAt.get(center) || target.get(center);
      const from = new Map([...target].map(([id]) => [id, shownAt.get(id) || origin]));
      const fromBox = shownBox || frame(target);
      const toBox = frame(target);
      shownAt = target;
      shownBox = toBox;
      if (REDUCED_MOTION) {
        show(target, toBox);
        return;
      }
      const start = performance.now();
      const step = (now) => {
        const t = Math.min(1, (now - start) / 450);
        const ease = 1 - Math.pow(1 - t, 3);
        const mix = (a, b) => a + (b - a) * ease;
        const at = new Map([...target].map(([id, end]) => [id, [mix(from.get(id)[0], end[0]), mix(from.get(id)[1], end[1])]]));
        show(at, fromBox.map((v, i) => mix(v, toBox[i])));
        if (t < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }

    function factItem(entry) {
      const fact = entry.fact;
      const verb = (PREDICATES[fact.predicate] || (() => fact.predicate.replace(/_/g, " ")))(fact);
      const what = fact.object ? name(fact.object) : fact.value;
      const state = status[entry.index];
      return h(
        "li",
        null,
        h(
          "div",
          { class: "bx-fact__line" },
          h("span", { style: state === "rejected" ? "text-decoration: line-through" : null }, name(fact.subject) + " " + verb + " " + what),
          h(
            "span",
            { class: state === "proposed" ? "bx-state bx-state--you" : state === "rejected" ? "bx-state bx-state--closed" : "bx-state" },
            state === "proposed" ? "waiting for you" : state,
          ),
        ),
        h("div", { class: "bx-fact__sources" }, "From " + fact.sources.map(sourceName).join(", ") + "; " + (AUTHORITY[fact.authority] || fact.authority) + "."),
        state === "proposed"
          ? h(
              "div",
              { class: "bx-fact__review" },
              h("button", { type: "button", class: "bx-btn bx-btn--go", onclick: () => { status[entry.index] = "confirmed"; draw(false); } }, "Confirm"),
              h("button", { type: "button", class: "bx-btn", onclick: () => { status[entry.index] = "rejected"; draw(false); } }, "Not true"),
            )
          : null,
      );
    }

    function draw(moved) {
      const view = k.views[center];
      const target = layout(view);
      nodeLayer.replaceChildren(...view.nodes.map((node) => nodeFor(node.id)));
      for (const [id, group] of nodes) group.setAttribute("class", "bx-map__node" + (id === center ? " bx-map__node--center" : ""));
      if (moved) animate(target);
      else {
        shownAt = target;
        shownBox = frame(target);
        show(target, shownBox);
      }
      const entity = k.entities[center];
      const waiting = factsAbout(center).filter((f) => status[f.index] === "proposed").length;
      details.replaceChildren(
        h(
          "div",
          { class: "bx-item" },
          h("div", { class: "bx-item__head" }, h("span", { class: "bx-item__kind" }, entity.name), h("span", { class: "bx-id" }, (TYPE_NAMES[entity.type] || entity.type) + ", " + center)),
          entity.summary ? h("p", { class: "bx-item__desc" }, entity.summary) : null,
          waiting ? h("p", { class: "bx-fine" }, waiting === 1 ? "One fact waits for your review." : waiting + " facts wait for your review.") : null,
          h("ul", { class: "bx-facts" }, factsAbout(center).map(factItem)),
        ),
      );
    }

    root.replaceChildren(
      bar("What bosun knows, around one process", h("button", { type: "button", class: "bx-btn bx-btn--quiet", onclick: () => { center = k.center; draw(true); } }, "Back to the start")),
      h("div", { class: "bx-map-layout" }, svg, details),
    );
    draw(false);
  }

  /* ---------- A day with bosun ---------- */

  const WINDOWS = ["orchestrator", "exporter", "pdf-renderer", "checkin"];
  // Each moment of the day, and what it changes in each window (the rest stay as they were).
  const DAY = [
    { at: "07:30", text: "Your Mac is asleep. Nothing runs.", windows: { orchestrator: ["asleep", ""], exporter: ["asleep", ""], "pdf-renderer": ["asleep", ""], checkin: ["asleep", ""] } },
    { at: "08:00", text: "The Mac wakes on schedule. Logins are refreshed, and each agent picks up where it stopped.", windows: { orchestrator: ["working", "starting the day"], exporter: ["working", "finishing yesterday's work"], "pdf-renderer": ["idle", "nothing in flight"], checkin: ["idle", "every hour"] } },
    { at: "08:20", text: "Morning chores: the orchestrator reads Slack and your pull requests, and records each new ask in the ledger. A review request goes to the pdf-renderer agent.", windows: { orchestrator: ["working", "morning chores"], "pdf-renderer": ["working", "reviewing a pull request"] } },
    { at: "09:10", text: "The pdf-renderer agent posts its review. Its task's rules allow that, so nothing waits for you.", windows: { orchestrator: ["idle", ""], exporter: ["idle", "waiting for review"], "pdf-renderer": ["idle", "review posted"] } },
    { at: "10:00", text: "Check-in: there is something new, review comments on your pull request. The orchestrator hands them to the exporter agent.", windows: { checkin: ["working", "asks for a check-in"], orchestrator: ["working", "check-in"], exporter: ["working", "addressing review comments"] } },
    { at: "11:40", text: "Dana answers the question about credit-note numbering. The ledger matches her reply to the question and hands it to the exporter agent.", windows: { checkin: ["idle", "every hour"], orchestrator: ["idle", ""], exporter: ["working", "credit notes: numbering"] } },
    { at: "12:10", you: true, text: "The orchestrator drafts a reply to Priya: credit notes are coming in 2.4. It waits for your yes.", windows: { orchestrator: ["you", "a reply to Priya"] } },
    { at: "12:25", text: "You approve it from your phone, between meetings. It is sent exactly as written.", windows: { orchestrator: ["idle", "reply sent"] } },
    { at: "15:05", you: true, text: "The exporter agent stops at a question only you can answer. When it has waited too long, the watchdog tells you.", windows: { exporter: ["you", "a question for you"] } },
    { at: "15:20", text: "You answer in its window, and it carries on.", windows: { exporter: ["working", "credit notes"] } },
    { at: "17:40", text: "The exporter agent reports back: the credit notes pull request is up for review. The ledger records the report.", windows: { exporter: ["idle", "reported back"] } },
    { at: "18:30", you: true, text: "Learning reads the day and proposes one fix: ask the finance owner before reusing a number sequence. It waits for your yes.", windows: {} },
    { at: "20:00", text: "Bosun stops keeping the Mac awake. Tomorrow starts where today stopped.", windows: { orchestrator: ["idle", "until tomorrow"], exporter: ["idle", "until tomorrow"], "pdf-renderer": ["idle", "until tomorrow"], checkin: ["idle", "until tomorrow"] } },
  ];
  const STATUS_WORDS = { asleep: "asleep", working: "working", idle: "idle", you: "waiting for you" };

  function minutes(at) {
    const [hours, mins] = at.split(":").map(Number);
    return hours * 60 + mins;
  }

  function dayExample(root) {
    const first = minutes(DAY[0].at);
    const last = minutes(DAY[DAY.length - 1].at);
    const percent = (at) => ((minutes(at) - first) / (last - first)) * 100;
    let index = 0;
    let timer = null;

    const time = h("div", { class: "bx-day__time" });
    const what = h("p", { class: "bx-day__what", "aria-live": "polite" });
    const slider = h("input", {
      type: "range",
      min: String(first),
      max: String(last),
      step: "5",
      value: String(first),
      "aria-label": "Time of day",
      oninput: (event) => {
        stop();
        const value = Number(event.target.value);
        index = DAY.reduce((found, moment, i) => (minutes(moment.at) <= value ? i : found), 0);
        show(false);
      },
    });
    const windows = h("div", { class: "bx-windows" });
    const play = h("button", { type: "button", class: "bx-btn bx-btn--go", onclick: () => (timer ? stop() : start()) }, "Play the day");

    function stateAt(i) {
      const state = {};
      for (const moment of DAY.slice(0, i + 1)) Object.assign(state, moment.windows);
      return state;
    }

    function show(moveSlider) {
      const moment = DAY[index];
      time.textContent = moment.at;
      what.textContent = moment.text;
      what.className = "bx-day__what" + (moment.you ? " bx-day__what--you" : "");
      if (moveSlider) slider.value = String(minutes(moment.at));
      const state = stateAt(index);
      windows.replaceChildren(
        ...WINDOWS.map((name) => {
          const [status, doing] = state[name] || ["asleep", ""];
          return h(
            "div",
            { class: "bx-window bx-window--" + status },
            h("div", { class: "bx-window__name" }, name),
            h("div", { class: "bx-window__status" }, STATUS_WORDS[status]),
            doing ? h("div", { class: "bx-window__doing" }, doing) : null,
          );
        }),
      );
    }

    function start() {
      if (index === DAY.length - 1) index = 0;
      play.textContent = "Pause";
      show(true);
      timer = setInterval(() => {
        if (index >= DAY.length - 1) return stop();
        index += 1;
        show(true);
      }, 2200);
    }
    function stop() {
      clearInterval(timer);
      timer = null;
      play.textContent = "Play the day";
    }

    root.replaceChildren(
      bar("One day, on one Mac"),
      h("div", { class: "bx-day__top" }, time, what),
      h(
        "div",
        { class: "bx-day__track" },
        slider,
        h(
          "div",
          { class: "bx-day__ticks", "aria-hidden": "true" },
          DAY.map((moment) => h("span", { class: "bx-day__tick" + (moment.you ? " bx-day__tick--you" : ""), style: "left: " + percent(moment.at) + "%" })),
        ),
        h("div", { class: "bx-day__scale", "aria-hidden": "true" }, h("span", null, DAY[0].at), h("span", null, "noon"), h("span", null, DAY[DAY.length - 1].at)),
      ),
      windows,
      h("div", { class: "bx-day__controls" }, play, h("span", { class: "bx-fine" }, "Amber marks are the moments that wait for you.")),
    );
    show(true);
  }

  /* ---------- The "On this page" rail follows the reader ---------- */

  function followReader() {
    const links = [...document.querySelectorAll(".bx-rail a")];
    const byId = new Map(links.map((link) => [decodeURIComponent(link.hash.slice(1)), link]));
    const headings = [...byId.keys()].map((id) => document.getElementById(id)).filter(Boolean);
    if (!headings.length || !("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          for (const link of links) link.classList.toggle("is-current", link === byId.get(entry.target.id));
        }
      },
      { rootMargin: "-20% 0px -70% 0px" },
    );
    headings.forEach((heading) => observer.observe(heading));
  }

  /* ---------- Draw them ---------- */

  const source = document.getElementById("bosun-examples");
  if (!source) return;
  const data = JSON.parse(source.textContent);
  const EXAMPLES = { ledger: ledgerExample, "needs-you": needsYouExample, knowledge: knowledgeExample, day: dayExample };
  for (const placeholder of document.querySelectorAll("[data-example]")) {
    const draw = EXAMPLES[placeholder.dataset.example];
    if (draw) draw(placeholder, data);
  }
  followReader();
})();
