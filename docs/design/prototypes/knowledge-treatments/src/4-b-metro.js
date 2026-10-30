/* =========================================================================
   TREATMENT B: Process metro map + atlas (People bubbles, Product treemap)
   The metro uses a hand-tuned grid plus a tiny octilinear router. In
   production: one row per process, shared stations snapped to columns.
   ========================================================================= */
KG.treatments.B = (function () {
  const GX = 54, GY = 86, OX = 44, OY = 40;
  const px = (gx, gy) => [OX + gx * GX, OY + gy * GY];

  // Stations: label (\n wraps), ref entity, grid position, label placement, gate flag.
  const S = {
    l10nFreeze: { label: 'String\nfreeze', ref: 'pr.l10n', g: [0.2, 3], lp: 'b' },
    l10nTrans:  { label: 'Translate', ref: 'sv.trans', g: [1.5, 3], lp: 'a' },
    l10nFlag:   { label: 'Behind a\nflag', ref: 'sy.flags', g: [2.8, 3], lp: 'b' },
    merge:      { label: 'Merge to\nmain', ref: 'st.merge', g: [4.1, 3], lp: 'a' },
    ci:         { label: 'CI pipeline', ref: 'sy.ci', g: [5.6, 3], lp: 'a' },
    staging:    { label: 'Deploy to\nstaging', ref: 'st.staging', g: [7.1, 3], lp: 'b' },
    canary:     { label: 'Canary\nchecks', ref: 'st.canary', g: [8.6, 3], lp: 'a' },
    smoke:      { label: 'Smoke\ntests', ref: 'g.smoke', g: [10.1, 3], lp: 'a', gate: true },
    errors:     { label: 'Error\nrate', ref: 'g.errors', g: [11.6, 3], lp: 'bl', gate: true },
    rollout:    { label: 'Gradual\nrollout', ref: 'st.rollout', g: [13.1, 3], lp: 'br' },
    prod:       { label: 'Live in\nprod', ref: 'en.prod', g: [14.6, 3], lp: 'a' },
    announce:   { label: '#deploys', ref: 'ch.deploys', g: [16, 3], lp: 'b' },
    // Customer bug report
    report:     { label: 'Customer\nreport', ref: 'ch.support', g: [2.2, 0.75], lp: 'b' },
    triage:     { label: 'Ticket\ntriaged', ref: 'sy.tickets', g: [3.9, 0.75], lp: 'a' },
    fix:        { label: 'Owner fixes', ref: 'p.you', g: [9.3, 0.75], lp: 'a' },
    told:       { label: 'Customer\ntold', ref: 'pr.bug', g: [10.9, 0.75], lp: 'b' },
    // Code review
    rPR:        { label: 'Open a\nPR', ref: 'r.invoices', g: [0, 5.3], lp: 'b' },
    rReview:    { label: 'Tech lead\nreviews', ref: 'p.tomas', g: [1.6, 5.3], lp: 'b' },
    rChecks:    { label: 'PR checks\npass', ref: 'g.checks', g: [3.2, 5.3], lp: 'b', gate: true },
    // Feature launch (vertical through the gradual rollout)
    lStart:     { label: 'Flag created', ref: 'pr.launch', g: [13.1, 0.55], lp: 'r' },
    lBeta:      { label: 'Beta customers', ref: 'p.priya', g: [13.1, 1.75], lp: 'r' },
    lDone:      { label: 'Flag\nremoved', ref: 'sy.flags', g: [13.1, 5.35], lp: 'r' },
    // Incident (vertical through the error-rate gate)
    iAlert:     { label: 'Alert fires', ref: 'sy.obs', g: [11.6, 6.3], lp: 'l' },
    iDeclare:   { label: 'Declare in\n#incidents', ref: 'ch.incidents', g: [11.6, 5.3], lp: 'l' },
    iFollow:    { label: 'Follow-up\nticket', ref: 'sy.tickets', g: [11.6, 1.75], lp: 'l' },
    // Hotfix (parallel express track below the trunk)
    hCut:       { label: 'Cut from\nprod tag', ref: 'pr.hotfix', g: [4.6, 4.2], lp: 'b' },
    hCI:        { label: '', ref: 'sy.ci', g: [5.6, 4.2], lp: 'b' },
    hSmoke:     { label: 'Skips the\ngradual rollout', ref: 'g.smoke', g: [10.1, 4.2], lp: 'b' },
    hProd:      { label: '', ref: 'en.prod', g: [14.6, 4.2], lp: 'b' },
  };
  const LINES = [
    { id: 'pr.release', name: 'Release', stations: ['merge', 'ci', 'staging', 'canary', 'smoke', 'errors', 'rollout', 'prod', 'announce'], tag: [4.1, 3] },
    { id: 'pr.bug', name: 'Bug report', stations: ['report', 'triage', 'staging', 'fix', 'told'] },
    { id: 'pr.review', name: 'Code review', stations: ['rPR', 'rReview', 'rChecks', 'merge'] },
    { id: 'pr.launch', name: 'Launch', stations: ['lStart', 'lBeta', 'rollout', 'lDone'] },
    { id: 'pr.incident', name: 'Incident', stations: ['iAlert', 'iDeclare', 'errors', 'iFollow'] },
    { id: 'pr.l10n', name: 'Localization', stations: ['l10nFreeze', 'l10nTrans', 'l10nFlag', 'merge'] },
    { id: 'pr.hotfix', name: 'Hotfix', stations: ['hCut', 'hCI', 'hSmoke', 'hProd'], dashed: true },
  ];
  // Vertical interchange connectors between the trunk and the hotfix track.
  const CONNECTORS = [['ci', 'hCI'], ['smoke', 'hSmoke'], ['prod', 'hProd']];
  const TAG_POS = { 'pr.release': ['announce', -81, -50], 'pr.bug': ['report', -6, -56], 'pr.review': ['rPR', -6, -28], 'pr.launch': ['lStart', -40, -22],
    'pr.incident': ['iAlert', -6, 30], 'pr.l10n': ['l10nFreeze', -6, -64], 'pr.hotfix': ['hProd', 16, 0] };

  let svg, linesG, stationsG, trainsG, raf = 0, running = false;
  const lineEls = {}, stationEls = {}, personEls = {}, tileEls = {};
  let trains = [];

  // Octilinear route between two px points: straight run, then 45 degrees.
  function route(a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1], adx = Math.abs(dx), ady = Math.abs(dy);
    if (adx < .5 || ady < .5) return [a, b];
    if (adx >= ady) { const m = [b[0] - Math.sign(dx) * ady, a[1]]; return [a, m, b]; }
    const m = [a[0], b[1] - Math.sign(dy) * adx]; return [a, m, b];
  }
  function roundedPath(pts, r = 14) {
    let d = `M${pts[0][0]},${pts[0][1]}`;
    for (let i = 1; i < pts.length - 1; i++) {
      const p0 = pts[i - 1], p1 = pts[i], p2 = pts[i + 1];
      const v1 = [p1[0] - p0[0], p1[1] - p0[1]], v2 = [p2[0] - p1[0], p2[1] - p1[1]];
      const l1 = Math.hypot(...v1), l2 = Math.hypot(...v2);
      if (l1 < 1 || l2 < 1) continue;
      const rr = Math.min(r, l1 / 2, l2 / 2);
      const a = [p1[0] - v1[0] / l1 * rr, p1[1] - v1[1] / l1 * rr], b = [p1[0] + v2[0] / l2 * rr, p1[1] + v2[1] / l2 * rr];
      d += `L${a[0].toFixed(1)},${a[1].toFixed(1)}Q${p1[0]},${p1[1]} ${b[0].toFixed(1)},${b[1].toFixed(1)}`;
    }
    const last = pts[pts.length - 1]; return d + `L${last[0]},${last[1]}`;
  }
  function linePoints(line) {
    const pts = [];
    line.stations.forEach((k, i) => {
      const p = px(...S[k].g);
      if (i === 0) { pts.push(p); return; }
      const seg = route(pts[pts.length - 1], p); seg.slice(1).forEach(q => pts.push(q));
    });
    return pts;
  }

  // Which lines touch each station (for interchanges).
  function stationLines() {
    const m = {}; LINES.forEach((l, i) => l.stations.forEach(k => (m[k] = m[k] || []).push(i))); return m;
  }

  function drawMetro() {
    svg = $('#b-metro'); svg.innerHTML = '';
    svg.setAttribute('viewBox', '0 0 990 610'); svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    const defs = svgEl('defs', {}, svg);
    const glow = svgEl('filter', { id: 'b-glow', x: '-50%', y: '-50%', width: '200%', height: '200%' }, defs);
    svgEl('feGaussianBlur', { stdDeviation: '3' }, glow);
    // Faint grid for the "map" feel.
    const grid = svgEl('g', { opacity: .35 }, svg);
    for (let x = 0; x < 990; x += 27) svgEl('line', { x1: x, y1: 0, x2: x, y2: 610, stroke: 'var(--line)', 'stroke-width': .5 }, grid);
    for (let y = 0; y < 610; y += 27) svgEl('line', { x1: 0, y1: y, x2: 990, y2: y, stroke: 'var(--line)', 'stroke-width': .5 }, grid);
    linesG = svgEl('g', {}, svg); trainsG = svgEl('g', {}, svg); stationsG = svgEl('g', {}, svg);
    const sl = stationLines();

    LINES.forEach((line, i) => {
      const col = KG.colors.metro[i];
      const d = roundedPath(linePoints(line));
      const g = svgEl('g', { class: 'metro-line-g', 'data-line': line.id }, linesG);
      svgEl('path', { d, class: 'metro-line', stroke: 'var(--bg)', 'stroke-width': 11 }, g);
      const p = svgEl('path', { d, class: 'metro-line', stroke: `var(--m${i + 1})`, 'stroke-width': line.dashed ? 4 : 6, 'stroke-dasharray': line.dashed ? '1 9' : null }, g);
      if (line.dashed) p.setAttribute('stroke-dasharray', '10 6');
      lineEls[line.id] = g;
      // Line name tag + actor avatars.
      const [sk, ox, oy] = TAG_POS[line.id]; const [sx, sy] = px(...S[sk].g);
      const tg = svgEl('g', { class: 'metro-station', tabindex: 0, role: 'button', 'aria-label': line.name + ' process', 'data-ent': line.id, transform: `translate(${sx + ox},${sy + oy})` }, g);
      const w = line.name.length * 7 + 18;
      svgEl('rect', { x: 0, y: -11, width: w, height: 22, rx: 11, fill: `var(--m${i + 1})` }, tg);
      const t = svgEl('text', { x: 9, y: 4, class: 'metro-tag' }, tg); t.textContent = line.name;
      const actors = KG.factsBy[line.id].filter(f => f.p === 'actor' && f.s === line.id).map(f => f.o);
      actors.forEach((a, j) => {
        const ag = svgEl('g', { transform: `translate(${w + 12 + j * 17},0)` }, tg);
        svgEl('circle', { r: 9, fill: 'var(--surface-3)', stroke: 'var(--people)', 'stroke-width': 1.5 }, ag);
        const at = svgEl('text', { y: 3.5, 'text-anchor': 'middle', 'font-size': 8, 'font-weight': 700, fill: 'var(--ink)' }, ag); at.textContent = KG.initials(KG.byId[a].name);
      });
      line.pathD = d;
    });
    // Interchange connectors (trunk <-> hotfix track).
    CONNECTORS.forEach(([a, b]) => {
      const [x1, y1] = px(...S[a].g), [, y2] = px(...S[b].g);
      svgEl('rect', { x: x1 - 8, y: y1 - 8, width: 16, height: y2 - y1 + 16, rx: 8, fill: 'var(--surface)', stroke: 'var(--ink)', 'stroke-width': 2.5, class: 'connector' }, stationsG);
    });
    // Stations.
    Object.entries(S).forEach(([k, st]) => {
      const [x, y] = px(...st.g);
      const lines = sl[k] || [];
      const inter = lines.length > 1;
      const g = svgEl('g', { class: 'metro-station', tabindex: 0, role: 'button', 'data-ent': st.ref, 'data-st': k, 'aria-label': `${st.label.replace(/\n/g, ' ')} station${inter ? ', interchange' : ''}${st.gate ? ', gate' : ''}`, transform: `translate(${x},${y})` }, stationsG);
      if (st.gate) {
        svgEl('rect', { x: -9, y: -9, width: 18, height: 18, rx: 4, fill: 'var(--surface)', stroke: 'var(--ink)', 'stroke-width': 2.5 }, g);
        svgEl('path', { d: 'M-4,0h8M0,-4v8', stroke: 'var(--ink)', 'stroke-width': 2, 'stroke-linecap': 'round', transform: 'rotate(45)' }, g);
      } else if (inter) {
        svgEl('circle', { r: 9.5, fill: 'var(--surface)', stroke: 'var(--ink)', 'stroke-width': 3 }, g);
      } else if (st.label) {
        const li = lines[0] ?? 0;
        svgEl('circle', { r: 6, fill: 'var(--surface)', stroke: `var(--m${li + 1})`, 'stroke-width': 3 }, g);
      } else {
        svgEl('circle', { r: 5, fill: 'var(--surface)', stroke: 'var(--ink)', 'stroke-width': 2 }, g);
      }
      svgEl('circle', { r: 16, fill: 'transparent' }, g); // generous hit target
      if (st.label) {
        const lp = st.lp; const parts = st.label.split('\n');
        let tx = 0, ty = 24, anchor = 'middle';
        if (lp === 'a') ty = -16 - (parts.length - 1) * 13;
        if (lp === 'r') { tx = 14; ty = 4 - (parts.length - 1) * 6.5; anchor = 'start'; }
        if (lp === 'l') { tx = -14; ty = 4 - (parts.length - 1) * 6.5; anchor = 'end'; }
        if (lp === 'br') { tx = 10; ty = 24; anchor = 'start'; }
        if (lp === 'bl') { tx = -10; ty = 24; anchor = 'end'; }
        const t = svgEl('text', { class: 'metro-label' + (inter || st.gate ? ' strong' : ''), 'text-anchor': anchor, transform: `translate(${tx},${ty})` }, g);
        parts.forEach((p, i) => { const ts = svgEl('tspan', { x: 0, dy: i ? 13 : 0 }, t); ts.textContent = p; });
      }
      stationEls[k] = g;
    });
    // Legend.
    $('#b-leg').innerHTML = `
      <span><svg width="14" height="14" viewBox="-7 -7 14 14"><circle r="4.5" fill="var(--surface)" stroke="var(--m1)" stroke-width="2.5"/></svg>Step</span>
      <span><svg width="16" height="16" viewBox="-8 -8 16 16"><circle r="6" fill="var(--surface)" stroke="var(--ink)" stroke-width="2.5"/></svg>Interchange (shared)</span>
      <span><svg width="16" height="16" viewBox="-8 -8 16 16"><rect x="-6" y="-6" width="12" height="12" rx="3" fill="var(--surface)" stroke="var(--ink)" stroke-width="2"/><path d="M-3,0h6M0,-3v6" stroke="var(--ink)" stroke-width="1.6" transform="rotate(45)"/></svg>Gate</span>
      <span><svg width="22" height="10"><line x1="1" y1="5" x2="21" y2="5" stroke="var(--m7)" stroke-width="3" stroke-dasharray="6 4"/></svg>Express track</span>
      <span><svg width="14" height="14" viewBox="-7 -7 14 14"><circle r="6" fill="var(--surface-3)" stroke="var(--people)" stroke-width="1.5"/></svg>Actor</span>`;
    setupTrains();
  }

  // Animated "trains" along a few lines (decorative; off under reduced motion).
  function setupTrains() {
    trainsG.innerHTML = ''; trains = [];
    ['pr.release', 'pr.bug', 'pr.review', 'pr.launch'].forEach((id, j) => {
      const i = LINES.findIndex(l => l.id === id);
      const path = svgEl('path', { d: LINES[i].pathD, fill: 'none', stroke: 'none' }, trainsG);
      const dot = svgEl('circle', { r: 4.5, fill: KG.dark ? '#fff' : 'var(--ink)', filter: KG.dark ? 'url(#b-glow)' : null, opacity: .95 }, trainsG);
      const core = svgEl('circle', { r: 2.6, fill: KG.dark ? '#fff' : 'var(--surface)' }, trainsG);
      trains.push({ path, dot, core, len: path.getTotalLength(), speed: 0.045 + j * .008, off: j * 0.27 });
    });
  }
  function animate(now) {
    if (!running) return;
    const red = KG.reduced();
    trains.forEach(t => {
      t.dot.style.display = t.core.style.display = red ? 'none' : '';
      if (red) return;
      const u = ((now / 1000 * t.speed) + t.off) % 1;
      const p = t.path.getPointAtLength(u * t.len);
      t.dot.setAttribute('cx', p.x); t.dot.setAttribute('cy', p.y); t.core.setAttribute('cx', p.x); t.core.setAttribute('cy', p.y);
    });
    raf = requestAnimationFrame(animate);
  }

  // ---- Atlas: People bubbles packed by team --------------------------
  function relax(items, cx, cy, iters, pull, pad) {
    for (let it = 0; it < iters; it++) {
      for (const a of items) { a.x += (cx - a.x) * pull; a.y += (cy - a.y) * pull; }
      for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
        const a = items[i], b = items[j]; const dx = b.x - a.x, dy = b.y - a.y; const d = Math.hypot(dx, dy) || .01; const m = a.r + b.r + pad;
        if (d < m) { const p = (m - d) / d / 2; a.x -= dx * p; a.y -= dy * p; b.x += dx * p; b.y += dy * p; }
      }
    }
  }
  function drawPeople() {
    const s = $('#b-people'); s.innerHTML = ''; s.setAttribute('viewBox', '0 0 520 290'); s.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    const teams = KG.entities.filter(e => e.type === 'team');
    const groups = teams.map((t, ti) => {
      const members = KG.facts.filter(f => f.p === 'member_of' && f.o === t.id).map(f => KG.byId[f.s]);
      const rnd = mulberry32(ti + 11);
      const items = members.map((m, i) => ({ e: m, r: 13 + Math.sqrt(m.nFacts) * 3.2, x: Math.cos(i * 2.4) * (8 + i * 6) + rnd(), y: Math.sin(i * 2.4) * (8 + i * 6) + rnd() }));
      relax(items, 0, 0, 160, .04, 4);
      const R = Math.max(...items.map(m => Math.hypot(m.x, m.y) + m.r)) + 14;
      return { t, items, r: R, x: 0, y: 0 };
    });
    // Place teams on a row then relax inside the panel.
    let cx = 0; groups.forEach(g => { g.x = cx + g.r; g.y = 150; cx += g.r * 2 + 10; });
    relax(groups, 260, 150, 120, .03, 10);
    const x0 = Math.min(...groups.map(g => g.x - g.r)) - 8, x1 = Math.max(...groups.map(g => g.x + g.r)) + 8;
    const y0 = Math.min(...groups.map(g => g.y - g.r)) - 22, y1 = Math.max(...groups.map(g => g.y + g.r)) + 8;
    s.setAttribute('viewBox', `${x0.toFixed(0)} ${y0.toFixed(0)} ${(x1 - x0).toFixed(0)} ${(y1 - y0).toFixed(0)}`);
    groups.forEach(g => {
      const gg = svgEl('g', { transform: `translate(${g.x.toFixed(1)},${g.y.toFixed(1)})` }, s);
      const tg = svgEl('g', { class: 'atlas-node', tabindex: 0, role: 'button', 'data-ent': g.t.id, 'aria-label': g.t.name + ' team' }, gg);
      svgEl('circle', { r: g.r, fill: 'color-mix(in srgb, var(--people) 7%, transparent)', stroke: 'color-mix(in srgb, var(--people) 35%, transparent)', 'stroke-dasharray': '3 3' }, tg);
      const lbl = svgEl('text', { y: -g.r - 6, 'text-anchor': 'middle', class: 'tm-group' }, tg); lbl.textContent = g.t.name;
      personEls[g.t.id] = tg;
      g.items.forEach(m => {
        const conf = KG.factsBy[m.e.id].some(f => f.status === 'confirmed');
        const pg = svgEl('g', { class: 'atlas-node', tabindex: 0, role: 'button', 'data-ent': m.e.id, 'aria-label': `${m.e.name}, ${m.e.summary}`, transform: `translate(${m.x.toFixed(1)},${m.y.toFixed(1)})` }, gg);
        svgEl('circle', { r: m.r, fill: conf ? 'var(--people)' : 'color-mix(in srgb, var(--people) 30%, var(--surface))', stroke: m.e.id === 'p.you' ? 'var(--ink)' : 'var(--people)', 'stroke-width': m.e.id === 'p.you' ? 2.5 : 1.5 }, pg);
        const t = svgEl('text', { y: 4, 'text-anchor': 'middle', 'font-size': m.e.id === 'p.you' ? 10 : 11, 'font-weight': 700, fill: conf ? '#fff' : 'var(--ink)' }, pg);
        t.textContent = KG.initials(m.e.name);
        personEls[m.e.id] = pg;
      });
    });
  }

  // ---- Atlas: Product treemap (squarified) ---------------------------
  function squarify(items, x, y, w, h) {
    // items: [{value}] sorted desc; returns items with x,y,w,h.
    const out = []; let rest = items.slice(); const total = rest.reduce((s, d) => s + d.value, 0);
    const scale = (w * h) / total;
    rest.forEach(d => d.area = d.value * scale);
    const worst = (row, len) => { const s = row.reduce((a, d) => a + d.area, 0); const mx = Math.max(...row.map(d => d.area)), mn = Math.min(...row.map(d => d.area)); return Math.max(len * len * mx / (s * s), (s * s) / (len * len * mn)); };
    while (rest.length) {
      const len = Math.min(w, h); let row = [rest[0]]; let i = 1;
      while (i < rest.length && worst(row.concat(rest[i]), len) <= worst(row, len)) { row.push(rest[i]); i++; }
      const s = row.reduce((a, d) => a + d.area, 0), thick = s / len;
      let off = 0;
      row.forEach(d => {
        const l = d.area / thick;
        if (w >= h) out.push(Object.assign(d, { x, y: y + off, w: thick, h: l })); else out.push(Object.assign(d, { x: x + off, y, w: l, h: thick }));
        off += l;
      });
      if (w >= h) { x += thick; w -= thick; } else { y += thick; h -= thick; }
      rest = rest.slice(i);
    }
    return out;
  }
  function drawProduct() {
    const s = $('#b-product'); s.innerHTML = ''; const W = 520, H = 290;
    s.setAttribute('viewBox', `0 0 ${W} ${H}`); s.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    const defs = svgEl('defs', {}, s);
    const pat = svgEl('pattern', { id: 'b-hatch', width: 6, height: 6, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' }, defs);
    svgEl('line', { x1: 0, y1: 0, x2: 0, y2: 6, stroke: 'var(--ink-3)', 'stroke-width': 1.2, opacity: .5 }, pat);
    const types = ['feature', 'decision', 'repo', 'service', 'lesson', 'principle'];
    const groups = types.map(t => {
      const leaves = KG.entities.filter(e => e.type === t).map(e => ({ e, value: 1 + e.nFacts })).sort((a, b) => b.value - a.value);
      return { t, leaves, value: leaves.reduce((a, d) => a + d.value, 0) };
    }).sort((a, b) => b.value - a.value);
    squarify(groups, 2, 2, W - 4, H - 4).forEach(g => {
      svgEl('rect', { x: g.x + 1, y: g.y + 1, width: g.w - 2, height: g.h - 2, rx: 6, fill: 'none', stroke: 'var(--line-2)' }, s);
      const gl = svgEl('text', { x: g.x + 8, y: g.y + 13, class: 'tm-group' }, s); gl.textContent = KG.TYPE_PLURAL[g.t];
      squarify(g.leaves, g.x + 4, g.y + 18, g.w - 8, g.h - 22).forEach(d => {
        const sup = KG.factsBy[d.e.id].some(f => f.s === d.e.id && f.status === 'superseded');
        const conf = KG.factsBy[d.e.id].some(f => f.status === 'confirmed');
        const tg = svgEl('g', { class: 'atlas-node', tabindex: 0, role: 'button', 'data-ent': d.e.id, 'aria-label': `${d.e.name}${sup ? ', superseded' : ''}` }, s);
        svgEl('rect', { x: d.x + 1.5, y: d.y + 1.5, width: Math.max(0, d.w - 3), height: Math.max(0, d.h - 3), rx: 4,
          fill: sup ? 'url(#b-hatch)' : `color-mix(in srgb, var(--product) ${conf ? 55 : 22}%, var(--surface))`, stroke: sup ? 'var(--line-2)' : 'color-mix(in srgb, var(--product) 60%, transparent)' }, tg);
        if (d.w > 44 && d.h > 20) {
          const maxc = Math.floor((d.w - 10) / 5.8);
          const name = d.e.name.length > maxc ? d.e.name.slice(0, maxc - 1) + '…' : d.e.name;
          const t = svgEl('text', { x: d.x + 6, y: d.y + 15, class: 'tm-label', 'text-decoration': sup ? 'line-through' : null }, tg); t.textContent = name;
          if (d.h > 34) { const t2 = svgEl('text', { x: d.x + 6, y: d.y + 28, class: 'tm-sub' }, tg); t2.textContent = sup ? 'superseded' : `${d.e.nFacts} facts`; }
        }
        tileEls[d.e.id] = tg;
      });
    });
  }

  // ---- Cross-highlighting -------------------------------------------
  function relevantLines(id) {
    return LINES.filter(l => l.id === id || KG.nbrs[l.id].has(id) || l.stations.some(k => S[k].ref === id)).map(l => l.id);
  }
  function highlight(id) {
    const dimAll = !!id;
    const lines = id ? new Set(relevantLines(id)) : null;
    const rel = id ? new Set([id, ...KG.nbrs[id]]) : null;
    LINES.forEach(l => lineEls[l.id].classList.toggle('dim', dimAll && !lines.has(l.id)));
    Object.entries(stationEls).forEach(([k, el]) => {
      const st = S[k];
      const on = !dimAll || rel.has(st.ref) || LINES.some(l => lines.has(l.id) && l.stations.includes(k));
      el.classList.toggle('dim', !on);
    });
    Object.entries(personEls).forEach(([eid, el]) => el.classList.toggle('dim', dimAll && !(rel.has(eid) || LINES.some(l => lines.has(l.id) && KG.nbrs[l.id].has(eid)))));
    Object.entries(tileEls).forEach(([eid, el]) => el.classList.toggle('dim', dimAll && !rel.has(eid)));
  }
  function tipHtml(id) {
    const e = KG.byId[id]; const f = KG.factsBy[id];
    const top = f.filter(x => x.s === id && x.v).slice(0, 2).map(x => `<div class="t2">${esc(x.v)}</div>`).join('');
    return `<div><b>${esc(e.name)}</b> <span class="t2">${e.type}</span></div>${top}<div class="t2">${f.length} facts &middot; click for details</div>`;
  }
  function bind() {
    const root = $('#mock-B');
    root.addEventListener('pointerover', e => { const n = e.target.closest('[data-ent]'); if (!n) return; highlight(n.dataset.ent); });
    root.addEventListener('pointermove', e => { const n = e.target.closest('[data-ent]'); if (n) KG.tip.show(tipHtml(n.dataset.ent), e.clientX, e.clientY); else KG.tip.hide(); });
    root.addEventListener('pointerout', e => { const n = e.target.closest('[data-ent]'); if (n && !n.contains(e.relatedTarget)) { highlight(KG.drawer.id); KG.tip.hide(); } });
    root.addEventListener('click', e => { const n = e.target.closest('[data-ent]'); if (n && n.closest('svg')) { KG.drawer.open(n.dataset.ent); highlight(n.dataset.ent); } });
    root.addEventListener('focusin', e => { const n = e.target.closest('[data-ent]'); if (n) highlight(n.dataset.ent); });
    root.addEventListener('keydown', e => { const n = e.target.closest('[data-ent]'); if (n && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); KG.drawer.open(n.dataset.ent); } });
    KG.bus.on('drawerclose', () => { if (KG.active === 'B') highlight(null); });
  }
  function redraw() { drawMetro(); drawPeople(); drawProduct(); }

  return {
    init() { redraw(); bind(); KG.bus.on('theme', () => { if (KG.treatments.B.inited) redraw(); }); KG.bus.on('status', () => { drawPeople(); drawProduct(); }); },
    show() { running = true; raf = requestAnimationFrame(animate); },
    hide() { running = false; cancelAnimationFrame(raf); highlight(null); },
    focus(id) { KG.drawer.open(id); highlight(id); },
  };
})();
