/* =========================================================================
   TREATMENT D: Radial explorer
   "You" at the center; rings = axis -> entity type -> entity. Zoom-to-
   segment tween, breadcrumbs, hierarchical edge bundling (Holten 2006)
   on hover via straightened B-splines (port of d3 curveBasis/curveBundle).
   ========================================================================= */
KG.treatments.D = (function () {
  let svg, gArcs, gEdges, gLabels, gCenter, W = 0, H = 0, R = 280, cx = 0, cy = 0;
  let root, all = [], dom = [0, 1], tween = null, focus = null, hoverLeaf = null, selected = null, kbd = null;
  const BETA = .82;

  // ---- Hierarchy + partition ---------------------------------------------
  function build() {
    root = { name: 'You', depth: 0, children: [], id: 'root' };
    KG.AXIS_ORDER.forEach(ax => {
      const a = { name: KG.AXES[ax].label, axis: ax, depth: 1, parent: root, children: [], id: 'ax:' + ax };
      const types = [...new Set(KG.entities.filter(e => e.axis === ax).map(e => e.type))];
      types.forEach(t => {
        const tn = { name: KG.TYPE_PLURAL[t], type: t, axis: ax, depth: 2, parent: a, children: [], id: 'ty:' + t };
        KG.entities.filter(e => e.type === t).sort((x, y) => y.degree - x.degree).forEach(e => tn.children.push({ name: e.name, e, axis: ax, depth: 3, parent: tn, id: e.id, value: 1 + e.nFacts * .22 }));
        a.children.push(tn);
      });
      root.children.push(a);
    });
    const sum = n => n.value = n.children ? n.children.reduce((s, c) => s + sum(c), 0) : n.value;
    sum(root);
    const part = (n, x0) => { n.x0 = x0; n.x1 = x0 + n.value / root.value; let x = x0; (n.children || []).forEach(c => { part(c, x); x = c.x1; }); };
    part(root, 0);
    all = []; const walk = n => { if (n.depth) all.push(n); (n.children || []).forEach(walk); }; walk(root);
    focus = root;
  }
  const ringR = d => [[0, R * .2], [R * .21, R * .34], [R * .35, R * .5], [R * .51, R * .73]][d];
  const ang = x => clamp((x - dom[0]) / (dom[1] - dom[0]), 0, 1) * Math.PI * 2;
  const pt = (a, r) => [cx + Math.sin(a) * r, cy - Math.cos(a) * r];
  function arcPath(a0, a1, r0, r1) {
    if (a1 - a0 < 1e-4) return '';
    const pad = Math.min(.006, (a1 - a0) / 4); a0 += pad; a1 -= pad;
    const large = a1 - a0 > Math.PI ? 1 : 0;
    if (a1 - a0 > Math.PI * 2 - .02) { // full ring: two halves
      const m = (a0 + a1) / 2;
      return arcPath(a0 - pad, m + pad / 2, r0, r1) + arcPath(m - pad / 2, a1 + pad, r0, r1);
    }
    const [x0, y0] = pt(a0, r1), [x1, y1] = pt(a1, r1), [x2, y2] = pt(a1, r0), [x3, y3] = pt(a0, r0);
    return `M${x0.toFixed(1)},${y0.toFixed(1)}A${r1},${r1} 0 ${large} 1 ${x1.toFixed(1)},${y1.toFixed(1)}L${x2.toFixed(1)},${y2.toFixed(1)}A${r0},${r0} 0 ${large} 0 ${x3.toFixed(1)},${y3.toFixed(1)}Z`;
  }

  // ---- DOM build ---------------------------------------------------------
  function fill(n) {
    if (n.depth === 1) return `var(--${n.axis})`;
    if (n.depth === 2) return `color-mix(in srgb, var(--${n.axis}) 55%, var(--surface))`;
    const conf = KG.factsBy[n.id].some(f => f.status === 'confirmed');
    const sup = KG.factsBy[n.id].some(f => f.s === n.id && f.status === 'superseded');
    return sup ? 'var(--surface-3)' : `color-mix(in srgb, var(--${n.axis}) ${conf ? 75 : 32}%, var(--surface))`;
  }
  function createEls() {
    svg = $('#d-svg'); svg.innerHTML = '';
    gArcs = svgEl('g', {}, svg); gEdges = svgEl('g', {}, svg); gLabels = svgEl('g', {}, svg); gCenter = svgEl('g', { class: 'd-center', tabindex: 0, role: 'button', 'aria-label': 'Zoom out' }, svg);
    all.forEach(n => {
      n.el = svgEl('path', { class: 'd-arc', fill: fill(n), tabindex: -1, role: 'button', 'data-id': n.id, 'aria-label': `${n.name}${n.depth < 3 ? ', ' + (n.children.length) + ' children' : ''}` }, gArcs);
      n.lab = svgEl('text', { class: 'd-lab' + (n.depth === 1 ? ' ax' : ''), 'dominant-baseline': 'middle' }, gLabels);
      n.lab.textContent = n.name;
      if (n.depth === 1) n.lab.classList.add('on-axis');
    });
    svgEl('circle', { r: 1, fill: 'var(--surface-2)', stroke: 'var(--line-2)', class: 'center-c' }, gCenter);
    svgEl('text', { class: 'd-lab ax', 'text-anchor': 'middle', style: 'font-size:18px' }, gCenter).textContent = 'You';
    svgEl('text', { class: 'd-lab', 'text-anchor': 'middle', style: 'fill: var(--ink-2)' }, gCenter);
  }
  function layout() {
    const box = $('#d-sun').getBoundingClientRect();
    W = box.width; H = box.height; cx = W / 2; cy = H / 2 + 8;
    R = Math.min(W, H) / 2 - 26;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const c = $('.center-c', gCenter); c.setAttribute('cx', cx); c.setAttribute('cy', cy); c.setAttribute('r', ringR(0)[1] - 2);
    const [t1, t2] = $$('text', gCenter);
    t1.setAttribute('x', cx); t1.setAttribute('y', cy - 4);
    t2.setAttribute('x', cx); t2.setAttribute('y', cy + 16); t2.style.fontSize = '11.5px';
  }
  function render() {
    const fs = selected || hoverLeaf ? new Set([(selected || hoverLeaf).id, ...KG.nbrs[(selected || hoverLeaf).id]]) : null;
    all.forEach(n => {
      const a0 = ang(n.x0), a1 = ang(n.x1), [r0, r1] = ringR(n.depth);
      n.el.setAttribute('d', arcPath(a0, a1, r0, r1));
      n.vis = a1 - a0 > 1e-4;
      const dimmed = fs && n.depth === 3 && !fs.has(n.id);
      n.el.style.opacity = dimmed ? .22 : fs && n.depth < 3 ? .55 : 1;
      if (n === selected) { n.el.style.stroke = 'var(--ink)'; n.el.style.strokeWidth = 2; } else { n.el.style.stroke = ''; n.el.style.strokeWidth = ''; }
      // Labels.
      const span = a1 - a0, mid = (a0 + a1) / 2, lab = n.lab;
      if (n.depth === 3) {
        const ok = span * r1 > 10.5;
        lab.style.display = ok ? '' : 'none'; if (!ok) return;
        const deg = mid * 180 / Math.PI - 90, flip = mid > Math.PI;
        const [x, y] = pt(mid, r1 + 6);
        lab.setAttribute('transform', `translate(${x.toFixed(1)},${y.toFixed(1)}) rotate(${flip ? deg + 180 : deg})`);
        lab.setAttribute('text-anchor', flip ? 'end' : 'start');
        lab.style.opacity = dimmed ? .3 : 1;
        lab.style.fontWeight = n === selected || n === hoverLeaf ? 650 : '';
      } else {
        const rm = (r0 + r1) / 2;
        const ok = span * rm > (n.depth === 1 ? 50 : 40);
        lab.style.display = ok ? '' : 'none'; if (!ok) return;
        const [x, y] = pt(mid, rm);
        let deg = mid * 180 / Math.PI; if (deg > 90 && deg < 270) deg += 180;
        const full = span > Math.PI * 1.9;
        lab.setAttribute('transform', `translate(${x.toFixed(1)},${y.toFixed(1)}) rotate(${n.depth === 1 || full ? 0 : deg})`);
        lab.setAttribute('text-anchor', 'middle');
        if (n.depth === 2) lab.style.fontSize = '10.5px';
      }
    });
    const [, t2] = $$('text', gCenter);
    t2.textContent = focus === root ? `${KG.entities.length} entities` : focus.name;
    drawEdges();
  }

  // ---- Edge bundling -----------------------------------------------------
  function anchorFor(n) {
    if (n.depth === 3) return pt(ang((n.x0 + n.x1) / 2), ringR(3)[0] - 2);
    if (n.depth === 2) return pt(ang((n.x0 + n.x1) / 2), (ringR(2)[0] + ringR(2)[1]) / 2);
    if (n.depth === 1) return pt(ang((n.x0 + n.x1) / 2), (ringR(1)[0] + ringR(1)[1]) / 2);
    return [cx, cy];
  }
  function pathNodes(a, b) {
    const up = [], down = [];
    let x = a, y = b;
    while (x.depth > y.depth) { up.push(x); x = x.parent; }
    while (y.depth > x.depth) { down.push(y); y = y.parent; }
    while (x !== y) { up.push(x); down.push(y); x = x.parent; y = y.parent; }
    return [...up, x, ...down.reverse()];
  }
  function basis(pts) {
    // Port of d3.curveBasis.
    let d = '', x0, y0, x1, y1, p = 0;
    const bz = (x, y) => { d += `C${((2 * x0 + x1) / 3).toFixed(1)},${((2 * y0 + y1) / 3).toFixed(1)} ${((x0 + 2 * x1) / 3).toFixed(1)},${((y0 + 2 * y1) / 3).toFixed(1)} ${((x0 + 4 * x1 + x) / 6).toFixed(1)},${((y0 + 4 * y1 + y) / 6).toFixed(1)}`; };
    for (const [x, y] of pts) {
      if (p === 0) { p = 1; d += `M${x},${y}`; }
      else if (p === 1) p = 2;
      else { if (p === 2) { p = 3; d += `L${((5 * x0 + x1) / 6).toFixed(1)},${((5 * y0 + y1) / 6).toFixed(1)}`; } bz(x, y); }
      x0 = x1; x1 = x; y0 = y1; y1 = y;
    }
    if (p === 3) { bz(x1, y1); d += `L${x1},${y1}`; } else if (p === 2) d += `L${x1},${y1}`;
    return d;
  }
  function bundle(pts) {
    const n = pts.length - 1, [ax, ay] = pts[0], [bx, by] = pts[n];
    return pts.map(([x, y], i) => [BETA * x + (1 - BETA) * (ax + (bx - ax) * i / n), BETA * y + (1 - BETA) * (ay + (by - ay) * i / n)]);
  }
  function drawEdges() {
    gEdges.innerHTML = '';
    const src = hoverLeaf || selected; if (!src || !src.vis) return;
    const byId = Object.fromEntries(all.filter(n => n.depth === 3).map(n => [n.id, n]));
    KG.nbrs[src.id].forEach(id => {
      const t = byId[id]; if (!t || !t.vis) return;
      const pts = bundle(pathNodes(src, t).map(anchorFor));
      const l = KG.links.find(L => (L.s === src.id && L.t === id) || (L.t === src.id && L.s === id));
      const st = l ? KG.linkStatus(l) : 'proposed';
      svgEl('path', { d: basis(pts), class: 'd-edge', stroke: `var(--${t.axis})`, opacity: .9, 'stroke-dasharray': st === 'confirmed' ? 'none' : '4 3' }, gEdges);
      const [ex, ey] = anchorFor(t);
      svgEl('circle', { cx: ex, cy: ey, r: 2.5, fill: `var(--${t.axis})` }, gEdges);
    });
  }

  // ---- Zoom + selection --------------------------------------------------
  function zoomTo(n) {
    if (!n) return;
    focus = n;
    const target = [n.x0, n.x1];
    if (KG.reduced()) { dom = target; render(); renderCrumbs(); renderSide(); return; }
    const from = dom.slice(), t0 = performance.now();
    cancelAnimationFrame(tween);
    const step = now => {
      const t = clamp((now - t0) / 650, 0, 1), e = easeInOut(t);
      dom = [lerp(from[0], target[0], e), lerp(from[1], target[1], e)]; render();
      if (t < 1) tween = requestAnimationFrame(step);
    };
    tween = requestAnimationFrame(step);
    renderCrumbs(); renderSide();
  }
  function selectLeaf(n) {
    selected = n; render(); renderCrumbs(); renderSide();
  }
  function renderCrumbs() {
    const chain = []; let n = selected || focus; while (n) { chain.unshift(n); n = n.parent; }
    $('#d-crumbs').innerHTML = chain.map((c, i) => `${i ? '<span class="sep">/</span>' : ''}<button data-crumb="${c.id}">${esc(c.name)}</button>`).join('');
  }
  function renderSide() {
    const box = $('#d-detail');
    if (selected) { KG.renderDetail(box, selected.id); return; }
    const f = focus;
    const leaves = []; const walk = n => n.children ? n.children.forEach(walk) : leaves.push(n); walk(f);
    const top = leaves.slice().sort((a, b) => b.e.degree - a.e.degree).slice(0, 10);
    const groups = (f.depth === 2 ? [f] : f.depth === 1 ? f.children : f.children.flatMap(a => a.children));
    const facts = new Set(); leaves.forEach(l => KG.factsBy[l.id].forEach(x => facts.add(x)));
    const prop = [...facts].filter(x => x.status === 'proposed').length;
    box.innerHTML = `<div class="kicker">${f.axis ? KG.shapeIcon(f.axis) + ' ' + KG.AXES[f.axis].label : 'Whole knowledge base'}</div>
      <h2>${esc(f === root ? 'Everything you know' : f.name)}</h2>
      <p class="summary">${f === root ? 'Four axes, from people and teams to the systems they run on. Click a ring to zoom.' : 'Click an entity in the outer ring to see its facts and relations.'}</p>
      <div class="mini-stats"><div><b>${leaves.length}</b>entities</div><div><b>${facts.size}</b>facts</div><div><b>${prop}</b>proposed</div></div>
      <h4>Composition</h4>
      ${groups.map(g => { const n = g.children.length; return `<div style="display:flex; align-items:center; gap:8px; margin:4px 0; font-size:12.5px"><span style="width:110px">${esc(g.name)}</span><span style="flex:1; height:8px; border-radius:4px; background:var(--surface-3); overflow:hidden"><span style="display:block; height:100%; width:${n / Math.max(...groups.map(x => x.children.length)) * 100}%; background:var(--${g.axis}); border-radius:4px"></span></span><span class="muted" style="width:20px; text-align:right">${n}</span></div>`; }).join('')}
      <h4>Most connected here</h4><div style="display:flex; flex-wrap:wrap; gap:6px">${top.map(l => KG.chip(l.id)).join('')}</div>`;
  }
  const nodeById = id => id === 'root' ? root : all.find(n => n.id === id);
  function activateNode(n) {
    if (!n) return;
    if (n.depth === 3) { selectLeaf(n); return; }
    selected = null;
    if (n === focus && n.parent) zoomTo(n.parent); else zoomTo(n);
  }

  function bind() {
    svg.addEventListener('pointermove', e => {
      const p = e.target.closest('[data-id]'); const n = p && nodeById(p.dataset.id);
      if (n) {
        KG.tip.show(`<b>${esc(n.name)}</b><div class="t2">${n.depth === 3 ? `${n.e.type} &middot; ${n.e.degree} links &middot; ${n.e.nFacts} facts` : `${n.children.length} ${n.depth === 1 ? 'types' : 'entities'} &middot; click to zoom`}</div>`, e.clientX, e.clientY);
        const leaf = n.depth === 3 ? n : null;
        if (leaf !== hoverLeaf) { hoverLeaf = leaf; render(); }
      } else { KG.tip.hide(); if (hoverLeaf) { hoverLeaf = null; render(); } }
    });
    svg.addEventListener('pointerleave', () => { KG.tip.hide(); if (hoverLeaf) { hoverLeaf = null; render(); } });
    svg.addEventListener('click', e => {
      const p = e.target.closest('[data-id]');
      if (p) { activateNode(nodeById(p.dataset.id)); return; }
      if (e.target.closest('g') === gCenter) { selected = null; if (focus.parent) zoomTo(focus.parent); else { render(); renderCrumbs(); renderSide(); } }
    });
    gCenter.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selected = null; zoomTo(focus.parent || root); } });
    // Roving keyboard navigation over arcs.
    svg.addEventListener('keydown', e => {
      const cur = kbd || (focus.children ? focus.children[0] : null); if (!cur) return;
      let next = null;
      const sib = cur.parent.children, i = sib.indexOf(cur);
      if (e.key === 'ArrowRight') next = sib[(i + 1) % sib.length];
      else if (e.key === 'ArrowLeft') next = sib[(i - 1 + sib.length) % sib.length];
      else if (e.key === 'ArrowDown' && cur.children) next = cur.children[0];
      else if (e.key === 'ArrowUp' && cur.parent !== root) next = cur.parent;
      else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activateNode(cur); return; }
      if (next) { e.preventDefault(); kbd = next; all.forEach(n => n.el.tabIndex = n === next ? 0 : -1); next.el.focus(); if (next.depth === 3) { hoverLeaf = next; render(); } }
    });
    $('#d-crumbs').addEventListener('click', e => {
      const b = e.target.closest('[data-crumb]'); if (!b) return;
      const n = nodeById(b.dataset.crumb);
      if (n.depth === 3) return;
      selected = null; zoomTo(n);
    });
    new ResizeObserver(() => { if (KG.active === 'D') { layout(); render(); } }).observe($('#d-sun'));
    KG.bus.on('status', () => { all.forEach(n => n.el.setAttribute('fill', fill(n))); if (KG.active === 'D') renderSide(); });
  }

  return {
    init() {
      build(); createEls(); layout(); bind();
      all[0].el.tabIndex = 0;
      // Entrance: unfold from a closed fan.
      if (!KG.reduced()) { dom = [0, 1]; const t0 = performance.now(); const grow = now => { const t = clamp((now - t0) / 900, 0, 1), e = 1 - Math.pow(1 - t, 3); dom = [0, 1 / Math.max(e, .02)]; render(); if (t < 1) requestAnimationFrame(grow); }; requestAnimationFrame(grow); }
      else render();
      renderCrumbs(); renderSide();
    },
    show() { layout(); render(); },
    hide() { KG.tip.hide(); hoverLeaf = null; },
    focus(id) {
      const n = all.find(x => x.id === id); if (!n) return;
      // Zoom to the entity's type unless it's already in view.
      if (!(n.x0 >= dom[0] - 1e-9 && n.x1 <= dom[1] + 1e-9) || focus === root) zoomTo(n.parent);
      selectLeaf(n);
    },
  };
})();

// =========================================================================
// BOOT
// =========================================================================
(function boot() {
  const setHdr = () => document.documentElement.style.setProperty('--hdr', $('.top').offsetHeight + 'px');
  setHdr(); window.addEventListener('resize', setHdr);
  if (/[?&]rm\b/.test(location.search)) { document.documentElement.classList.add('reduce-motion'); $('#motionBtn').setAttribute('aria-pressed', 'true'); }
  KG.readColors();
  KG.renderPills();
  const initial = (location.hash || '#A').slice(1).toUpperCase();
  activate(['A', 'B', 'C', 'D'].includes(initial) ? initial : 'A', false);
  window.addEventListener('hashchange', () => activate((location.hash || '#A').slice(1).toUpperCase(), false));
})();
