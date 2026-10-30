/* =========================================================================
   TREATMENT C: Briefing / insight feed
   Magazine-style home: headline + metrics, "this week we learned" timeline
   with before -> after decisions, review batch as a card stack, and a
   "most connected" strip with mini ego graphs. Plain HTML + tiny SVG.
   ========================================================================= */
KG.treatments.C = (function () {
  let wrap, batch = [], idx = 0, history = [], feedAxis = 'all', expanded = new Set();
  const GROUP = { people: 'People and roles', process: 'How work moves', product: 'Product decisions', systems: 'Systems and environments' };

  // ---- Small SVG builders ------------------------------------------------
  function sparkline(values, w = 150, h = 34, color = 'var(--accent)') {
    const mx = Math.max(...values, 1), step = w / (values.length - 1);
    const pts = values.map((v, i) => [i * step, h - 3 - (v / mx) * (h - 8)]);
    const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join('');
    const last = pts[pts.length - 1];
    return `<svg class="spark" width="${w}" height="${h}" viewBox="-2 0 ${w + 6} ${h}" aria-hidden="true">
      <path class="area" d="${line}L${w},${h}L0,${h}Z" fill="${color}"/>
      <path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${last[0]}" cy="${last[1]}" r="3.5" fill="${color}" stroke="var(--surface)" stroke-width="2"/></svg>`;
  }
  function ring(pct, color, size = 56) {
    const r = size / 2 - 5, c = 2 * Math.PI * r;
    return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="6"/>
      <circle class="ring-v" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="6" stroke-linecap="round"
        stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${c.toFixed(1)}" data-to="${(c * (1 - pct)).toFixed(1)}" transform="rotate(-90 ${size / 2} ${size / 2})" style="transition: stroke-dashoffset 1.1s var(--ease-out)"/>
      <text x="50%" y="54%" text-anchor="middle" font-size="12" font-weight="650" fill="var(--ink)">${Math.round(pct * 100)}%</text></svg>`;
  }
  function ego(id, size = 74) {
    const c = size / 2, nb = [...KG.nbrs[id]].slice(0, 12), R = size / 2 - 9;
    let s = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">`;
    nb.forEach((n, i) => {
      const a = i / nb.length * Math.PI * 2 - Math.PI / 2, x = c + Math.cos(a) * R, y = c + Math.sin(a) * R;
      s += `<line x1="${c}" y1="${c}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="var(--line-2)" stroke-width="1"/>`;
      s += `<path transform="translate(${x.toFixed(1)},${y.toFixed(1)})" d="${KG.shapePath(KG.AXES[KG.byId[n].axis].shape, 3.6)}" fill="var(--${KG.byId[n].axis})"/>`;
    });
    const e = KG.byId[id];
    s += `<path transform="translate(${c},${c})" d="${KG.shapePath(KG.AXES[e.axis].shape, 8)}" fill="var(--${e.axis})" stroke="var(--surface)" stroke-width="2"/></svg>`;
    return s;
  }
  function countUp(el, to) {
    if (KG.reduced()) { el.textContent = to; return; }
    const t0 = performance.now();
    const step = now => { const t = clamp((now - t0) / 900, 0, 1); el.textContent = Math.round(to * easeInOut(t)); if (t < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }

  // ---- Sections ----------------------------------------------------------
  function heroHtml() {
    const perDay = [1, 2, 3, 4, 5, 6, 7].map(d => KG.facts.filter(f => f.day === d).length);
    const reusePerDay = [1, 2, 3, 4, 5, 6, 7].map(d => KG.facts.filter(f => f.day <= d).reduce((a, f) => a + f.reuse, 0));
    const saved = KG.facts.reduce((a, f) => a + f.reuse, 0);
    const changed = KG.facts.filter(f => f.p === 'supersedes').length;
    const proposed = KG.facts.filter(f => f.status === 'proposed').length;
    const cov = KG.AXIS_ORDER.map(ax => { const es = KG.entities.filter(e => e.axis === ax); return [ax, es.filter(e => e.nFacts >= 3).length / es.length]; });
    const topReuse = KG.facts.filter(f => f.reuse).sort((a, b) => b.reuse - a.reuse)[0];
    return `
      <div class="c-tile c-greet">
        <h3>Weekly briefing &middot; ${KG.DAYS[6]}</h3>
        <h2>This week the agent learned <span class="c-count" data-to="${KG.facts.length}">0</span> facts about how Acme works.</h2>
        <p>${KG.entities.length} entities across four axes, ${changed} decisions changed, ${proposed} facts still waiting for a human.</p>
        <div style="margin-top:8px; display:flex; align-items:end; gap:10px">${sparkline(perDay, 220, 34)}<span class="muted" style="font-size:11.5px">facts per day</span></div>
      </div>
      <div class="c-tile">
        <h3>Rediscovery saved</h3>
        <div class="c-big"><span class="c-count" data-to="${saved}">0</span><small>re-asks avoided</small></div>
        <div class="c-delta">Most reused: <b>${esc(KG.byId[topReuse.s].name)}</b>, ${topReuse.reuse}x</div>
        <div style="margin-top:6px">${sparkline(reusePerDay, 200, 30, 'var(--good)')}</div>
      </div>
      <div class="c-tile">
        <h3>Coverage <span class="muted" style="text-transform:none; letter-spacing:0; font-weight:500">(entities with 3+ facts)</span></h3>
        <div class="c-rings">${cov.map(([ax, p]) => `<div class="c-ring">${ring(p, `var(--${ax})`)}${KG.shapeIcon(ax, 10)} ${KG.AXES[ax].label}</div>`).join('')}</div>
      </div>
      <div class="c-tile">
        <h3>Today's review</h3>
        <div class="c-big"><span id="c-left">${batch.length}</span><small>facts in 3 groups</small></div>
        <div class="c-delta">About 2 minutes. Keys: <span class="mono">Y</span> confirm, <span class="mono">S</span> skip, <span class="mono">N</span> reject.</div>
        <button class="btn primary" id="c-start" style="margin-top:10px">Start review</button>
      </div>`;
  }

  function feedHtml() {
    const chips = ['all', ...KG.AXIS_ORDER].map(a => `<button class="axis-chip" aria-pressed="${feedAxis === a}" data-fax="${a}">${a === 'all' ? '' : KG.shapeIcon(a)}${a === 'all' ? 'All' : KG.AXES[a].label}</button>`).join('');
    let html = `<div class="panel-h"><h3>This week we learned</h3><span class="muted">newest first</span><span style="flex:1"></span><div style="display:flex; gap:5px">${chips}</div></div><div class="scroll" id="c-scroll">`;
    for (let d = 7; d >= 1; d--) {
      const facts = KG.facts.filter(f => f.day === d && f.p !== 'supersedes' && (feedAxis === 'all' || KG.byId[f.s].axis === feedAxis));
      const changes = KG.facts.filter(f => f.day === d && f.p === 'supersedes' && (feedAxis === 'all' || KG.byId[f.s].axis === feedAxis));
      if (!facts.length && !changes.length) continue;
      const newEnts = KG.entities.filter(e => e.day === d && (feedAxis === 'all' || e.axis === feedAxis)).length;
      html += `<div class="c-day"><h4>${KG.DAYS[d - 1]} <span class="muted" style="font-weight:500">${facts.length + changes.length} facts &middot; ${newEnts} new entities</span></h4>`;
      changes.forEach(f => {
        const before = KG.facts.find(x => x.s === f.o && x.p === 'decided'), after = KG.facts.find(x => x.s === f.s && x.p === 'decided');
        html += `<div class="c-change" role="button" tabindex="0" data-open="${f.s}" aria-label="Decision changed: ${esc(after.v)}">
          <div class="before"><span class="lab">Before</span>${esc(before.v)}</div>
          <div class="arrow" aria-hidden="true"><svg width="14" height="14" viewBox="0 0 14 14"><path d="M2 7h9M8 3.5L11.5 7 8 10.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
          <div class="after"><span class="lab">Now &middot; ${KG.AUTH_LABEL[after.auth]}</span>${esc(after.v)}</div></div>`;
      });
      // Highlights first: lessons, rules and decisions read best.
      const rank = f => (/lesson|decision|principle/.test(KG.byId[f.s].type) ? 0 : /rule|caveat|states/.test(f.p) ? 1 : 2);
      const sorted = facts.slice().sort((a, b) => rank(a) - rank(b));
      const limit = expanded.has(d) ? sorted.length : 6;
      sorted.slice(0, limit).forEach(f => {
        html += `<div class="c-item" role="button" tabindex="0" data-open="${f.s}">${KG.shapeIcon(KG.byId[f.s].axis, 12)}<div class="txt">${KG.sentence(f)}</div>${KG.statusBadge(f)}</div>`;
      });
      if (sorted.length > limit) html += `<button class="btn ghost" data-more="${d}" style="margin:2px 0 4px 10px">Show ${sorted.length - limit} more</button>`;
      html += `</div>`;
    }
    return html + `</div>`;
  }

  function stripHtml() {
    const top = KG.entities.slice().sort((a, b) => b.degree - a.degree).slice(0, 6);
    return top.map(e => `<button class="c-ego" data-open="${e.id}" aria-label="${esc(e.name)}, ${e.degree} links">${ego(e.id)}<div><div class="nm">${esc(e.name)}</div><div class="ds">${KG.AXES[e.axis].label} / ${e.type}</div><div class="ds">${e.degree} links &middot; ${e.nFacts} facts</div></div></button>`).join('');
  }

  // ---- Review stack ------------------------------------------------------
  function buildBatch() {
    const prop = KG.facts.filter(f => f.status === 'proposed' && f.p !== 'has_step');
    const pick = ax => prop.filter(f => KG.byId[f.s].axis === ax).sort((a, b) => b.day - a.day || b.reuse - a.reuse);
    batch = [...pick('process').slice(0, 4), ...pick('product').slice(0, 3), ...pick('systems').slice(0, 2)].map(f => ({ f, result: null }));
    idx = 0; history = [];
  }
  function reviewHtml() {
    return `<div class="panel-h"><h3>Daily review batch</h3><span class="muted" id="c-rv-sub"></span></div>
      <div class="c-prog" id="c-prog" style="margin-top:10px"></div>
      <div class="c-stack" id="c-stack" aria-live="polite"></div>
      <div class="c-actions">
        <button class="c-act reject" data-act="reject" aria-label="Reject (N or left arrow)"><svg width="12" height="12" viewBox="0 0 12 12"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>Reject <kbd>N</kbd></button>
        <button class="c-act skip" data-act="skip" aria-label="Skip (S or down arrow)">Skip <kbd>S</kbd></button>
        <button class="c-act confirm" data-act="confirm" aria-label="Confirm (Y or right arrow)"><svg width="12" height="12" viewBox="0 0 12 12"><path d="M2 6.5l2.5 2.5L10 3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>Confirm <kbd>Y</kbd></button>
        <button class="c-act" data-act="undo" aria-label="Undo (Z)" title="Undo (Z)">Undo <kbd>Z</kbd></button>
      </div>`;
  }
  function renderStack() {
    const stack = $('#c-stack');
    $('#c-prog').innerHTML = batch.map((b, i) => `<i class="${b.result ? b.result + (b.result === 'skip' ? 'ped' : 'ed') : i === idx ? 'cur' : ''}"></i>`).join('');
    const left = batch.length - idx;
    $('#c-rv-sub').textContent = left ? `${idx + 1} of ${batch.length} · ${GROUP[KG.byId[batch[idx].f.s].axis]}` : 'Done';
    const cl = $('#c-left'); if (cl) cl.textContent = left;
    if (!left) {
      const n = r => batch.filter(b => b.result === r).length;
      stack.innerHTML = `<div class="c-done"><div><svg width="56" height="56" viewBox="0 0 56 56" aria-hidden="true"><circle cx="28" cy="28" r="26" fill="none" stroke="var(--good)" stroke-width="2.5" opacity=".35"/><path d="M17 29l7 7 15-16" fill="none" stroke="var(--good)" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <p style="font-size:16px; color:var(--ink); margin:8px 0 2px; font-weight:600">Batch done</p><p style="margin:0">${n('confirm')} confirmed, ${n('skip')} skipped, ${n('reject')} rejected.</p>
        <button class="btn" id="c-again" style="margin-top:10px">Review another batch</button></div></div>`;
      return;
    }
    let html = '';
    for (let i = Math.min(batch.length - 1, idx + 2); i >= idx; i--) {
      const f = batch[i].f, depth = i - idx, e = KG.byId[f.s];
      html += `<div class="c-card ${depth === 0 ? 'top' : ''}" data-i="${i}" style="transform: translateY(${depth * 12}px) scale(${1 - depth * .045}); opacity:${1 - depth * .25}; z-index:${10 - depth}">
        <span class="stamp confirm">CONFIRM</span><span class="stamp reject">REJECT</span><span class="stamp skip">SKIP</span>
        <div class="group">${GROUP[e.axis]}</div>
        <div class="sentence">${KG.sentence(f)}</div>
        <div><button class="chip" data-goto="${e.id}">${KG.shapeIcon(e.axis)}${esc(e.name)}</button>${f.o ? ' <span class="muted">to</span> ' + KG.chip(f.o) : ''}</div>
        ${context(f)}
        <div class="meta">${KG.authBadge(f)}${KG.statusBadge(f)}<span class="mono">${esc(f.src.kind)}: ${esc(f.src.ref)}</span><span>${KG.DAYS[f.day - 1]}</span></div>
      </div>`;
    }
    stack.innerHTML = html;
    bindDrag(stack.querySelector('.c-card.top'));
  }
  // Other things we already know about the same entity, to judge the fact in context.
  function context(f) {
    const rel = KG.factsBy[f.s].filter(x => x !== f && x.s === f.s && x.status !== 'superseded').sort((a, b) => (b.status === 'confirmed') - (a.status === 'confirmed')).slice(0, 3);
    if (!rel.length) return '';
    return `<div style="margin-top:4px; border-top:1px solid var(--line); padding-top:8px"><div class="group" style="margin-bottom:4px">Already known about ${esc(KG.byId[f.s].name)}</div>${rel.map(x => `<div style="font-size:12.5px; color:var(--ink-2); margin:3px 0; display:flex; gap:6px; align-items:baseline">${KG.statusBadge(x)}<span>${KG.sentence(x, false)}</span></div>`).join('')}</div>`;
  }
  function decide(action) {
    if (idx >= batch.length) return;
    const card = $('#c-stack .c-card.top');
    const b = batch[idx]; b.result = action;
    history.push({ i: idx, prev: b.f.status });
    if (action === 'confirm') KG.setStatus(b.f.id, 'confirmed');
    if (action === 'reject') KG.setStatus(b.f.id, 'rejected');
    const out = { confirm: 'translate(140%, -10px) rotate(14deg)', reject: 'translate(-140%, -10px) rotate(-14deg)', skip: 'translate(0, 120%) scale(.9)' }[action];
    idx++;
    if (card && !KG.reduced()) {
      $('.stamp.' + action, card).style.opacity = 1;
      card.style.transition = 'transform .38s cubic-bezier(.3,.7,.3,1), opacity .38s';
      card.style.transform = out; card.style.opacity = 0;
      setTimeout(renderStack, 260);
    } else renderStack();
  }
  function undo() {
    const h = history.pop(); if (!h) return;
    const b = batch[h.i]; b.result = null; KG.setStatus(b.f.id, h.prev); idx = h.i; renderStack();
  }
  function bindDrag(card) {
    if (!card) return;
    let sx = 0, sy = 0, dx = 0, dy = 0, on = false;
    card.addEventListener('pointerdown', e => { if (e.target.closest('button')) return; on = true; sx = e.clientX; sy = e.clientY; card.setPointerCapture(e.pointerId); card.style.transition = 'none'; card.style.cursor = 'grabbing'; });
    card.addEventListener('pointermove', e => {
      if (!on) return; dx = e.clientX - sx; dy = e.clientY - sy;
      card.style.transform = `translate(${dx}px, ${Math.max(dy, -20)}px) rotate(${dx / 18}deg)`;
      $('.stamp.confirm', card).style.opacity = clamp(dx / 110, 0, 1);
      $('.stamp.reject', card).style.opacity = clamp(-dx / 110, 0, 1);
      $('.stamp.skip', card).style.opacity = Math.abs(dx) < 50 ? clamp(dy / 90, 0, 1) : 0;
    });
    const end = () => {
      if (!on) return; on = false; card.style.cursor = '';
      if (dx > 110) decide('confirm'); else if (dx < -110) decide('reject'); else if (dy > 90) decide('skip');
      else { card.style.transition = 'transform .45s var(--ease-spring)'; card.style.transform = ''; $$('.stamp', card).forEach(s => s.style.opacity = 0); }
      dx = dy = 0;
    };
    card.addEventListener('pointerup', end); card.addEventListener('pointercancel', end);
  }

  // ---- Render + bind -----------------------------------------------------
  function render() {
    wrap.innerHTML = `<div class="c-hero">${heroHtml()}</div>
      <div class="panel c-feed" id="c-feed">${feedHtml()}</div>
      <div class="panel c-review">${reviewHtml()}</div>
      <div class="c-strip">${stripHtml()}</div>`;
    renderStack(); animateIn();
  }
  function animateIn() {
    $$('.c-count', wrap).forEach(el => countUp(el, +el.dataset.to));
    requestAnimationFrame(() => requestAnimationFrame(() => $$('.ring-v', wrap).forEach(r => r.setAttribute('stroke-dashoffset', r.dataset.to))));
  }
  function bind() {
    wrap.addEventListener('click', e => {
      const a = e.target.closest('[data-act]');
      if (a) { a.dataset.act === 'undo' ? undo() : decide(a.dataset.act); return; }
      if (e.target.closest('#c-again')) { buildBatch(); renderStack(); return; }
      if (e.target.closest('#c-start')) { $('#c-stack .c-card.top')?.scrollIntoView({ block: 'nearest' }); $('.c-act.confirm', wrap).focus(); return; }
      const fx = e.target.closest('[data-fax]');
      if (fx) { feedAxis = fx.dataset.fax; $('#c-feed').innerHTML = feedHtml(); return; }
      const more = e.target.closest('[data-more]');
      if (more) { expanded.add(+more.dataset.more); const st = $('#c-scroll').scrollTop; $('#c-feed').innerHTML = feedHtml(); $('#c-scroll').scrollTop = st; return; }
      const o = e.target.closest('[data-open]');
      if (o && !e.target.closest('[data-goto]')) KG.drawer.open(o.dataset.open);
    });
    wrap.addEventListener('keydown', e => { const o = e.target.closest('[data-open]'); if (o && (e.key === 'Enter' || e.key === ' ') && o.tagName !== 'BUTTON') { e.preventDefault(); KG.drawer.open(o.dataset.open); } });
    document.addEventListener('keydown', e => {
      if (KG.active !== 'C' || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === 'y' || e.key === 'ArrowRight') { decide('confirm'); e.preventDefault(); }
      else if (k === 'n' || e.key === 'ArrowLeft') { decide('reject'); e.preventDefault(); }
      else if (k === 's' || e.key === 'ArrowDown') { decide('skip'); e.preventDefault(); }
      else if (k === 'z') { undo(); e.preventDefault(); }
    });
    KG.bus.on('status', () => { if (KG.active === 'C') { const st = $('#c-scroll')?.scrollTop || 0; $('#c-feed').innerHTML = feedHtml(); $('#c-scroll').scrollTop = st; } });
  }
  return {
    init() { wrap = $('#c-wrap'); buildBatch(); render(); bind(); KG.bus.on('theme', () => { if (KG.active === 'C') { $('.c-strip', wrap).innerHTML = stripHtml(); } }); },
    show() { if (this.inited && wrap.children.length) animateIn(); },
    hide() {},
    focus(id) { KG.drawer.open(id); },
  };
})();
