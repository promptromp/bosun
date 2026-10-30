/* =========================================================================
   TREATMENT A: Constellation map
   Canvas renderer + small hand-written force simulation (O(n^2) charge is
   fine below ~400 nodes; swap in a quadtree / d3-force beyond that).
   ========================================================================= */
KG.treatments.A = (function () {
  const ANCHORS = { people: [-440, 30], process: [-60, -210], product: [420, -20], systems: [40, 260] };
  let canvas, ctx, W = 0, H = 0, dpr = 1, raf = 0, running = false;
  let nodes = [], nodeById = {}, links = [], stars = [];
  const cam = { x: 0, y: 0, k: 1 };
  let camAnim = null;
  let alpha = 1, alphaTarget = 0;
  let hover = null, selected = null, drag = null, pointer = { x: 0, y: 0, down: false, moved: false, sx: 0, sy: 0, panning: false };
  let asOf = 7, playTimer = null;
  const axesOn = new Set(KG.AXIS_ORDER);
  const t0 = performance.now();
  let FF = 'sans-serif'; // cached font stack for canvas text

  // ---- Model ------------------------------------------------------------
  function build() {
    const rnd = mulberry32(7);
    nodes = KG.entities.map((e, i) => {
      const [ax, ay] = ANCHORS[e.axis];
      const ang = rnd() * Math.PI * 2, rad = 40 + rnd() * 140;
      return {
        id: e.id, e, axis: e.axis, shape: KG.AXES[e.axis].shape,
        r: e.id === 'p.you' ? 15 : 4.5 + Math.sqrt(e.degree) * 3.1,
        x: ax + Math.cos(ang) * rad, y: ay + Math.sin(ang) * rad, vx: 0, vy: 0,
        a: 0, want: true, appearAt: 0, hl: 1, phase: rnd() * Math.PI * 2, fixed: false,
      };
    });
    nodeById = Object.fromEntries(nodes.map(n => [n.id, n]));
    links = KG.links.map(l => ({ l, s: nodeById[l.s], t: nodeById[l.t], same: nodeById[l.s].axis === nodeById[l.t].axis }));
    refreshConfirmed();
    // Pre-settle so the first paint is calm, then let it breathe.
    nodes.forEach(n => n.want = true);
    for (let i = 0; i < 320; i++) { alpha = Math.max(.02, 1 - i / 320); tick(); }
    alpha = .25;
    const rs = mulberry32(3);
    stars = Array.from({ length: 160 }, () => ({ x: rs(), y: rs(), r: rs() * 1.1 + .2, a: rs() * .35 + .05 }));
  }
  function refreshConfirmed() { nodes.forEach(n => n.confirmed = KG.factsBy[n.id].some(f => f.status === 'confirmed')); }

  function updateVisibility(animateIn) {
    const now = performance.now();
    nodes.forEach(n => {
      const want = axesOn.has(n.axis) && n.e.day <= asOf;
      if (want && !n.want) {
        // New arrivals spawn next to a visible neighbor so growth reads as growth.
        const nb = [...KG.nbrs[n.id]].map(id => nodeById[id]).find(m => m.want);
        if (nb) { n.x = nb.x + (Math.random() - .5) * 30; n.y = nb.y + (Math.random() - .5) * 30; }
        n.appearAt = animateIn ? now + Math.random() * 250 : 0;
      }
      n.want = want;
    });
    reheat(.5);
  }
  function reheat(a) { alpha = Math.max(alpha, a); }

  // ---- Physics ----------------------------------------------------------
  function tick() {
    alpha += (alphaTarget - alpha) * .02;
    if (alpha < .003) return;
    const act = nodes.filter(n => n.want);
    for (const n of act) {
      const [ax, ay] = ANCHORS[n.axis];
      n.vx += (ax - n.x) * .06 * alpha; n.vy += (ay - n.y) * .06 * alpha;
    }
    for (let i = 0; i < act.length; i++) {
      const a = act[i];
      for (let j = i + 1; j < act.length; j++) {
        const b = act[j];
        let dx = b.x - a.x, dy = b.y - a.y; let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
        if (d2 < 90000) {
          const f = 260 * alpha / d2;
          a.vx -= dx * f; a.vy -= dy * f; b.vx += dx * f; b.vy += dy * f;
        }
        const min = a.r + b.r + 9;
        if (d2 < min * min) {
          const d = Math.sqrt(d2), push = (min - d) / d * .25;
          a.x -= dx * push; a.y -= dy * push; b.x += dx * push; b.y += dy * push;
        }
      }
    }
    for (const L of links) {
      if (!L.s.want || !L.t.want) continue;
      const dx = L.t.x - L.s.x, dy = L.t.y - L.s.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const len = L.same ? 52 : 170, f = (d - len) / d * alpha * (L.same ? .22 : .025);
      L.s.vx += dx * f; L.s.vy += dy * f; L.t.vx -= dx * f; L.t.vy -= dy * f;
    }
    for (const n of act) {
      if (n.fixed) { n.vx = n.vy = 0; continue; }
      n.vx *= .58; n.vy *= .58; n.x += n.vx; n.y += n.vy;
    }
  }

  // ---- Camera -----------------------------------------------------------
  const toScreen = (x, y) => [(x - cam.x) * cam.k + W / 2, (y - cam.y) * cam.k + H / 2];
  const toWorld = (sx, sy) => [(sx - W / 2) / cam.k + cam.x, (sy - H / 2) / cam.k + cam.y];
  function fitTarget() {
    const vis = nodes.filter(n => n.want);
    if (!vis.length) return { x: 0, y: 0, k: 1 };
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    vis.forEach(n => { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); });
    const k = clamp(Math.min((W - 220) / (x1 - x0 + 120), (H - 230) / (y1 - y0 + 120)), .5, 1.9);
    return { x: (x0 + x1) / 2 - 20 / k, y: (y0 + y1) / 2 + 10 / k, k };
  }
  function flyTo(target, ms = 750) {
    if (KG.reduced()) { Object.assign(cam, target); camAnim = null; return; }
    camAnim = { from: { ...cam }, to: target, t0: performance.now(), ms };
  }

  // ---- Rendering --------------------------------------------------------
  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = r.width; H = r.height;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  }
  function focusSet() {
    const f = hover || selected;
    if (!f) return null;
    const s = new Set([f.id]); KG.nbrs[f.id].forEach(id => s.add(id));
    return s;
  }
  function easeOutBack(t) { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); }

  function draw(now) {
    const C = KG.colors, dark = KG.dark, reduced = KG.reduced();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    // Star field with slight parallax (dark only, purely decorative).
    if (dark) {
      ctx.fillStyle = '#ffffff';
      for (const s of stars) {
        const x = ((s.x * W * 1.4 - cam.x * .08 * cam.k) % W + W) % W, y = ((s.y * H * 1.4 - cam.y * .08 * cam.k) % H + H) % H;
        ctx.globalAlpha = s.a * (reduced ? 1 : .75 + .25 * Math.sin(now * .001 + s.x * 40));
        ctx.fillRect(x, y, s.r, s.r);
      }
      ctx.globalAlpha = 1;
    }
    const fs = focusSet();
    const breathe = reduced ? 0 : 1;
    // Per-node animated values.
    for (const n of nodes) {
      const targetA = n.want ? (n.appearAt > now ? 0 : 1) : 0;
      n.a = reduced ? targetA : lerp(n.a, targetA, targetA > n.a ? .09 : .14);
      const targetH = !fs || fs.has(n.id) ? 1 : .13;
      n.hl = reduced ? targetH : lerp(n.hl, targetH, .16);
      n.ox = Math.sin(now * .00055 + n.phase) * 1.6 * breathe;
      n.oy = Math.cos(now * .00047 + n.phase * 1.3) * 1.6 * breathe;
      const [sx, sy] = toScreen(n.x + n.ox, n.y + n.oy); n.sx = sx; n.sy = sy;
    }
    // Axis cluster captions.
    ctx.font = '650 11px ' + FF;
    ctx.textAlign = 'center';
    for (const ax of KG.AXIS_ORDER) {
      if (!axesOn.has(ax)) continue;
      const vis = nodes.filter(n => n.axis === ax && n.a > .3);
      if (!vis.length) continue;
      let cx = 0, minY = Infinity; vis.forEach(n => { cx += n.sx; minY = Math.min(minY, n.sy); }); cx /= vis.length;
      ctx.globalAlpha = (fs ? .25 : .6);
      ctx.fillStyle = C[ax];
      ctx.fillText(KG.AXES[ax].label.toUpperCase().split('').join(' '), cx, minY - 26);
    }
    ctx.globalAlpha = 1;
    // Links.
    const f = hover || selected;
    for (const L of links) {
      const a = Math.min(L.s.a, L.t.a); if (a < .02) continue;
      if (L.l.day > asOf) continue;
      const st = KG.linkStatus(L.l);
      const inc = f && (L.s === f || L.t === f);
      ctx.globalAlpha = a * (f ? (inc ? .95 : .05) : (st === 'confirmed' ? .5 : .3));
      ctx.strokeStyle = inc ? C[(L.s === f ? L.t : L.s).axis] : C['ink-3'];
      ctx.lineWidth = inc ? 1.6 : 1;
      ctx.setLineDash(st === 'confirmed' ? [] : st === 'superseded' ? [1, 4] : [3, 4]);
      ctx.beginPath(); ctx.moveTo(L.s.sx, L.s.sy); ctx.lineTo(L.t.sx, L.t.sy); ctx.stroke();
    }
    ctx.setLineDash([]); ctx.globalAlpha = 1;
    // Nodes.
    const k = clamp(cam.k, .6, 1.8);
    for (const n of nodes) {
      if (n.a < .02) continue;
      const appear = n.want ? easeOutBack(clamp(n.a, 0, 1)) : n.a;
      const r = n.r * k * Math.max(0, appear);
      const col = C[n.axis];
      const al = n.a * n.hl;
      if (dark && al > .2) {
        const g = ctx.createRadialGradient(n.sx, n.sy, r * .4, n.sx, n.sy, r * 2.8);
        g.addColorStop(0, hexA(col, .32 * al)); g.addColorStop(1, hexA(col, 0));
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(n.sx, n.sy, r * 2.8, 0, Math.PI * 2); ctx.fill();
      }
      // "New on this day" pulse.
      if (n.e.day === asOf && n.want && al > .5) {
        const ph = reduced ? .4 : ((now - t0) / 1800 + n.phase / 6) % 1;
        ctx.globalAlpha = (1 - ph) * .7 * al; ctx.strokeStyle = col; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(n.sx, n.sy, r + 3 + ph * 12, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.globalAlpha = al;
      KG.shapeCanvas(ctx, n.shape, n.sx, n.sy, r);
      ctx.fillStyle = n.confirmed ? col : hexA(col, .42); ctx.fill();
      ctx.strokeStyle = n.confirmed ? (dark ? 'rgba(255,255,255,.35)' : 'rgba(0,0,0,.25)') : col;
      ctx.lineWidth = 1.2; ctx.stroke();
      if (n === selected) { ctx.strokeStyle = C.ink; ctx.lineWidth = 2; KG.shapeCanvas(ctx, n.shape, n.sx, n.sy, r + 4); ctx.stroke(); }
    }
    ctx.globalAlpha = 1;
    // Labels: focus neighborhood, hubs, or everything when zoomed in.
    // Greedy collision avoidance: focus first, then by connectedness.
    ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.lineJoin = 'round';
    const placed = [];
    const cands = nodes.filter(n => n.a >= .5 && ((fs && fs.has(n.id)) || (!fs && (n.e.degree >= 6 || cam.k > 1.35 || n.id === 'p.you'))))
      .sort((a, b) => (b === f) - (a === f) || b.e.degree - a.e.degree);
    for (const n of cands) {
      const inFocus = fs && fs.has(n.id);
      const strong = n === f;
      ctx.font = (strong ? '650 13px ' : '500 11.5px ') + FF;
      const y = n.sy + n.r * k + 5;
      const w = ctx.measureText(n.e.name).width + 6, box = [n.sx - w / 2, y - 1, n.sx + w / 2, y + 15];
      if (!strong && placed.some(p => box[0] < p[2] && box[2] > p[0] && box[1] < p[3] && box[3] > p[1])) continue;
      placed.push(box);
      ctx.globalAlpha = n.a * (fs && !inFocus ? .2 : 1);
      ctx.strokeStyle = C.bg; ctx.lineWidth = 4; ctx.strokeText(n.e.name, n.sx, y);
      ctx.fillStyle = strong ? C.ink : C['ink-2']; ctx.fillText(n.e.name, n.sx, y);
    }
    ctx.globalAlpha = 1; ctx.textBaseline = 'alphabetic';
  }

  function loop() {
    if (!running) return;
    const now = performance.now();
    if (camAnim) {
      const t = clamp((now - camAnim.t0) / camAnim.ms, 0, 1), e = easeInOut(t);
      cam.x = lerp(camAnim.from.x, camAnim.to.x, e); cam.y = lerp(camAnim.from.y, camAnim.to.y, e); cam.k = lerp(camAnim.from.k, camAnim.to.k, e);
      if (t >= 1) camAnim = null;
    }
    tick(); draw(now);
    raf = requestAnimationFrame(loop);
  }

  // ---- Interaction ------------------------------------------------------
  function hit(sx, sy) {
    let best = null, bd = Infinity;
    for (const n of nodes) {
      if (n.a < .5) continue;
      const d = Math.hypot(n.sx - sx, n.sy - sy), r = Math.max(8, n.r * clamp(cam.k, .6, 1.8) + 4);
      if (d < r && d < bd) { bd = d; best = n; }
    }
    return best;
  }
  function select(n, open = true) {
    selected = n;
    if (n && open) KG.drawer.open(n.id);
  }
  function tipFor(n) {
    const facts = KG.factsBy[n.id];
    const p = facts.filter(f => f.status === 'proposed').length;
    return `<div><b>${esc(n.e.name)}</b></div><div class="t2">${KG.AXES[n.axis].label} / ${n.e.type} &middot; ${n.e.degree} links &middot; ${facts.length} facts (${p} proposed)</div>`;
  }
  function bindPointer() {
    canvas.addEventListener('pointerdown', e => {
      const r = canvas.getBoundingClientRect(); const sx = e.clientX - r.left, sy = e.clientY - r.top;
      pointer = { x: sx, y: sy, sx, sy, down: true, moved: false, panning: false };
      const n = hit(sx, sy);
      if (n) { drag = n; n.fixed = true; alphaTarget = .25; reheat(.25); }
      else pointer.panning = true;
      canvas.setPointerCapture(e.pointerId); canvas.classList.add('dragging');
    });
    canvas.addEventListener('pointermove', e => {
      const r = canvas.getBoundingClientRect(); const sx = e.clientX - r.left, sy = e.clientY - r.top;
      if (pointer.down) {
        if (Math.hypot(sx - pointer.sx, sy - pointer.sy) > 4) pointer.moved = true;
        if (drag) { const [wx, wy] = toWorld(sx, sy); drag.x = wx; drag.y = wy; }
        else if (pointer.panning) { cam.x -= (sx - pointer.x) / cam.k; cam.y -= (sy - pointer.y) / cam.k; camAnim = null; }
        pointer.x = sx; pointer.y = sy; KG.tip.hide();
        return;
      }
      const n = hit(sx, sy);
      if (n !== hover) { hover = n; canvas.classList.toggle('pointer', !!n); }
      if (n) KG.tip.show(tipFor(n), e.clientX, e.clientY); else KG.tip.hide();
    });
    const end = e => {
      if (!pointer.down) return;
      canvas.classList.remove('dragging');
      if (drag) { drag.fixed = false; alphaTarget = 0; }
      if (!pointer.moved) {
        const n = hit(pointer.x, pointer.y);
        if (n) select(n); else { selected = null; KG.drawer.close(); }
      }
      drag = null; pointer.down = false;
    };
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointercancel', end);
    canvas.addEventListener('pointerleave', () => { if (!pointer.down) { hover = null; KG.tip.hide(); } });
    canvas.addEventListener('wheel', e => {
      e.preventDefault(); camAnim = null;
      const r = canvas.getBoundingClientRect(); const sx = e.clientX - r.left, sy = e.clientY - r.top;
      const [wx, wy] = toWorld(sx, sy);
      cam.k = clamp(cam.k * Math.exp(-e.deltaY * .0016), .35, 3.5);
      cam.x = wx - (sx - W / 2) / cam.k; cam.y = wy - (sy - H / 2) / cam.k;
    }, { passive: false });
  }
  function zoomBy(f) { flyTo({ x: cam.x, y: cam.y, k: clamp(cam.k * f, .35, 3.5) }, 300); }

  // Search with fly-to.
  function bindSearch() {
    const q = $('#a-q'), box = $('#a-results');
    let items = [], idx = 0;
    const render = () => {
      box.innerHTML = items.map((e, i) => `<button role="option" aria-selected="${i === idx}" data-id="${e.id}">${KG.shapeIcon(e.axis)}<span>${esc(e.name)}</span><span class="t">${e.type}</span></button>`).join('');
      box.classList.toggle('show', items.length > 0); q.setAttribute('aria-expanded', String(items.length > 0));
    };
    q.addEventListener('input', () => {
      const s = q.value.trim().toLowerCase(); idx = 0;
      items = s ? KG.entities.filter(e => e.name.toLowerCase().includes(s) || e.type.includes(s) || (e.summary || '').toLowerCase().includes(s))
        .sort((a, b) => a.name.toLowerCase().indexOf(s) - b.name.toLowerCase().indexOf(s) || b.degree - a.degree).slice(0, 8) : [];
      render();
      hover = items[0] ? nodeById[items[0].id] : null;
    });
    q.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { idx = Math.min(idx + 1, items.length - 1); render(); hover = items[idx] && nodeById[items[idx].id]; e.preventDefault(); }
      else if (e.key === 'ArrowUp') { idx = Math.max(idx - 1, 0); render(); hover = items[idx] && nodeById[items[idx].id]; e.preventDefault(); }
      else if (e.key === 'Enter' && items[idx]) { go(items[idx].id); }
      else if (e.key === 'Escape') { q.value = ''; items = []; render(); hover = null; q.blur(); }
    });
    box.addEventListener('click', e => { const b = e.target.closest('[data-id]'); if (b) go(b.dataset.id); });
    const go = id => { items = []; render(); q.value = KG.byId[id].name; hover = null; KG.tip.hide(); api.focus(id); q.blur(); };
    document.addEventListener('keydown', e => {
      if (KG.active === 'A' && e.key === '/' && document.activeElement !== q && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); q.focus(); q.select(); }
    });
  }

  function renderChips() {
    $('#a-chips').innerHTML = KG.AXIS_ORDER.map(ax => {
      const n = KG.entities.filter(e => e.axis === ax).length;
      return `<button class="axis-chip" aria-pressed="${axesOn.has(ax)}" data-ax="${ax}">${KG.shapeIcon(ax)}${KG.AXES[ax].label} <span class="n">${n}</span></button>`;
    }).join('');
  }
  function renderTime() {
    const per = [1, 2, 3, 4, 5, 6, 7].map(d => KG.facts.filter(f => f.day === d).length), mx = Math.max(...per);
    $('#a-bars').innerHTML = per.map((c, i) => `<i class="${i + 1 <= asOf ? 'on' : ''}" style="height:${Math.max(8, c / mx * 100)}%" title="${KG.DAYS[i]}: ${c} facts"></i>`).join('');
    $('#a-asof').textContent = KG.DAYS[asOf - 1];
    const nf = KG.facts.filter(f => f.day <= asOf).length, ne = KG.entities.filter(e => e.day <= asOf).length;
    $('#a-asof-n').textContent = `${ne} entities, ${nf} facts`;
    $('#a-day').value = asOf;
  }
  function setAsOf(d, animate = true) { asOf = d; renderTime(); updateVisibility(animate); }
  function renderLegend() {
    const ic = (shape, fill, stroke) => `<svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true"><path d="${KG.shapePath(shape, 5)}" fill="${fill}" stroke="${stroke}" stroke-width="1.2"/></svg>`;
    $('#a-legend').innerHTML = `
      <div class="row">${ic('circle', 'var(--ink-3)', 'none')} Has confirmed facts</div>
      <div class="row">${ic('circle', 'color-mix(in srgb, var(--ink-3) 40%, transparent)', 'var(--ink-3)')} Proposed only</div>
      <div class="row"><svg width="22" height="6" aria-hidden="true"><line x1="0" y1="3" x2="22" y2="3" stroke="var(--ink-3)" stroke-width="1.5"/></svg> Confirmed relation</div>
      <div class="row"><svg width="22" height="6" aria-hidden="true"><line x1="0" y1="3" x2="22" y2="3" stroke="var(--ink-3)" stroke-width="1.5" stroke-dasharray="3 3"/></svg> Proposed relation</div>
      <div class="row"><svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true"><circle r="5.5" fill="none" stroke="var(--ink-3)" stroke-width="1.2"/><circle r="2.5" fill="var(--ink-3)"/></svg> Pulse: new on the as-of day</div>
      <div class="row muted">Size = connectedness. Shape = axis.</div>`;
  }

  const api = {
    init() {
      canvas = $('#a-canvas'); ctx = canvas.getContext('2d'); FF = getComputedStyle(document.body).fontFamily;
      resize(); build();
      new ResizeObserver(() => { resize(); }).observe(canvas.parentElement);
      Object.assign(cam, fitTarget());
      // Entrance: nodes pop in day by day.
      const now = performance.now();
      nodes.forEach(n => { n.a = 0; n.appearAt = KG.reduced() ? 0 : now + (n.e.day - 1) * 110 + Math.random() * 180; });
      bindPointer(); bindSearch(); renderChips(); renderTime(); renderLegend();
      $('#a-chips').addEventListener('click', e => {
        const b = e.target.closest('[data-ax]'); if (!b) return;
        const ax = b.dataset.ax;
        if (axesOn.has(ax) && axesOn.size > 1) axesOn.delete(ax); else axesOn.add(ax);
        renderChips(); updateVisibility(true);
      });
      $('#a-day').addEventListener('input', e => { stopPlay(); setAsOf(+e.target.value); });
      $('#a-play').addEventListener('click', () => playTimer ? stopPlay() : startPlay());
      $('#a-zin').addEventListener('click', () => zoomBy(1.3));
      $('#a-zout').addEventListener('click', () => zoomBy(1 / 1.3));
      $('#a-fit').addEventListener('click', () => flyTo(fitTarget()));
      KG.bus.on('status', () => refreshConfirmed());
      KG.bus.on('drawerclose', () => { selected = null; });
    },
    show() { running = true; resize(); raf = requestAnimationFrame(loop); },
    hide() { running = false; cancelAnimationFrame(raf); stopPlay(); hover = null; selected = null; },
    // Test hook: screen position of a node (used by the headless check).
    _screenPos(id) { const n = nodeById[id]; const r = canvas.getBoundingClientRect(); return n ? [r.left + n.sx, r.top + n.sy] : null; },
    focus(id) {
      const n = nodeById[id]; if (!n) return;
      if (!axesOn.has(n.axis)) { axesOn.add(n.axis); renderChips(); }
      if (n.e.day > asOf) setAsOf(7);
      updateVisibility(true);
      flyTo({ x: n.x + 150 / 1.25, y: n.y, k: 1.25 }); // offset so the drawer does not cover the neighborhood
      select(n);
    },
  };
  const playIcon = '<svg width="12" height="12" viewBox="0 0 12 12"><path d="M3 1.5l7 4.5-7 4.5z" fill="currentColor"/></svg>';
  const pauseIcon = '<svg width="12" height="12" viewBox="0 0 12 12"><rect x="2.5" y="2" width="2.5" height="8" fill="currentColor"/><rect x="7" y="2" width="2.5" height="8" fill="currentColor"/></svg>';
  function startPlay() {
    setAsOf(1); $('#a-play').innerHTML = pauseIcon; $('#a-play').setAttribute('aria-label', 'Pause replay');
    playTimer = setInterval(() => { if (asOf >= 7) { stopPlay(); flyTo(fitTarget()); return; } setAsOf(asOf + 1); }, 1000);
  }
  function stopPlay() { if (!playTimer) return; clearInterval(playTimer); playTimer = null; $('#a-play').innerHTML = playIcon; $('#a-play').setAttribute('aria-label', 'Replay the week'); }
  return api;
})();
