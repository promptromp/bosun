/* =========================================================================
   CORE: utilities, theme + motion, event bus, shapes, fact rendering,
   shared detail drawer, tooltip, toast, treatment switcher.
   ========================================================================= */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const easeInOut = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const SVGNS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs = {}, parent) {
  const el = document.createElementNS(SVGNS, tag);
  for (const k in attrs) if (attrs[k] != null) el.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(el);
  return el;
}
// Deterministic PRNG so layouts are stable across loads.
function mulberry32(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

// --- Event bus ------------------------------------------------------------
KG.bus = { h: {}, on(ev, fn) { (this.h[ev] = this.h[ev] || []).push(fn); }, emit(ev, d) { (this.h[ev] || []).forEach(fn => fn(d)); } };

// --- Theme, colors, reduced motion ---------------------------------------
KG.colors = {};
KG.readColors = function () {
  const cs = getComputedStyle(document.documentElement);
  const g = n => cs.getPropertyValue(n).trim();
  ['people', 'process', 'product', 'systems'].forEach(a => KG.colors[a] = g('--' + a));
  ['bg', 'surface', 'surface-2', 'surface-3', 'line', 'line-2', 'ink', 'ink-2', 'ink-3', 'accent', 'good', 'bad', 'warn'].forEach(n => KG.colors[n] = g('--' + n));
  KG.colors.metro = [1, 2, 3, 4, 5, 6, 7, 8].map(i => g('--m' + i));
  KG.dark = document.documentElement.dataset.theme === 'dark';
};
KG.axisColor = id => KG.colors[KG.byId[id].axis];
function hexA(hex, a) {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
  return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`;
}
const osReduce = matchMedia('(prefers-reduced-motion: reduce)');
KG.reduced = () => osReduce.matches || document.documentElement.classList.contains('reduce-motion');

function setTheme(t) {
  document.documentElement.dataset.theme = t;
  const b = $('#themeBtn'); b.setAttribute('aria-pressed', String(t === 'dark')); $('span', b).textContent = t === 'dark' ? 'Dark' : 'Light';
  KG.readColors(); KG.bus.emit('theme');
}
$('#themeBtn').addEventListener('click', () => setTheme(KG.dark ? 'light' : 'dark'));
$('#motionBtn').addEventListener('click', e => {
  const on = !document.documentElement.classList.contains('reduce-motion');
  document.documentElement.classList.toggle('reduce-motion', on);
  e.currentTarget.setAttribute('aria-pressed', String(on));
  KG.bus.emit('motion');
});
osReduce.addEventListener('change', () => KG.bus.emit('motion'));

// --- Shapes (secondary encoding for axis) --------------------------------
// Returns an SVG path centered on 0,0 with "radius" r.
KG.shapePath = function (shape, r) {
  if (shape === 'circle') return `M${-r},0a${r},${r} 0 1,0 ${2 * r},0a${r},${r} 0 1,0 ${-2 * r},0`;
  if (shape === 'diamond') { const q = r * 1.18; return `M0,${-q}L${q},0L0,${q}L${-q},0Z`; }
  if (shape === 'square') { const q = r * .86; return `M${-q},${-q}H${q}V${q}H${-q}Z`; }
  const pts = []; for (let i = 0; i < 6; i++) { const a = Math.PI / 6 + i * Math.PI / 3; pts.push((r * 1.05 * Math.cos(a)).toFixed(2) + ',' + (r * 1.05 * Math.sin(a)).toFixed(2)); }
  return 'M' + pts.join('L') + 'Z';
};
KG.shapeCanvas = function (ctx, shape, x, y, r) {
  ctx.beginPath();
  if (shape === 'circle') ctx.arc(x, y, r, 0, Math.PI * 2);
  else if (shape === 'diamond') { const q = r * 1.18; ctx.moveTo(x, y - q); ctx.lineTo(x + q, y); ctx.lineTo(x, y + q); ctx.lineTo(x - q, y); ctx.closePath(); }
  else if (shape === 'square') { const q = r * .86; if (ctx.roundRect) ctx.roundRect(x - q, y - q, 2 * q, 2 * q, Math.min(3, q * .3)); else ctx.rect(x - q, y - q, 2 * q, 2 * q); }
  else { for (let i = 0; i < 6; i++) { const a = Math.PI / 6 + i * Math.PI / 3; const px = x + r * 1.05 * Math.cos(a), py = y + r * 1.05 * Math.sin(a); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); } ctx.closePath(); }
};
KG.shapeIcon = function (axis, size = 12) {
  const r = size / 2 - 1.5;
  return `<svg width="${size}" height="${size}" viewBox="${-size / 2} ${-size / 2} ${size} ${size}" aria-hidden="true"><path d="${KG.shapePath(KG.AXES[axis].shape, r)}" fill="var(--${axis})"/></svg>`;
};
KG.initials = name => name === 'You' ? 'You' : name.split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();

// --- Fact rendering ------------------------------------------------------
const PRED = { role: 'role', member_of: 'member of', owns: 'owns', reviews: 'reviews', approves: 'approves', prefers_channel: 'prefers channel', timezone: 'timezone',
  purpose: 'purpose', has_step: 'step', entry_point: 'entry point', actor: 'actor', caveat: 'caveat', uses: 'uses', rule: 'rule', states: 'states', gated_by: 'gated by',
  posts_to: 'posts to', depends_on: 'depends on', lives_in: 'lives in', about: 'about', part_of: 'part of', decided: 'decided', supersedes: 'supersedes', status: 'status', replicates_from: 'replicates from' };
KG.pred = p => PRED[p] || p.replace(/_/g, ' ');
KG.AUTH_LABEL = { user_said: 'user said', stakeholder_said: 'stakeholder', observed: 'observed', inferred: 'inferred' };
KG.AUTH_GLYPH = { user_said: '●', stakeholder_said: '◆', observed: '○', inferred: '◌' };
KG.authBadge = f => `<span class="badge" title="Authority">${KG.AUTH_GLYPH[f.auth]} ${KG.AUTH_LABEL[f.auth]}</span>`;
KG.statusBadge = f => `<span class="badge st-${f.status}">${f.status === 'confirmed' ? '✓ ' : ''}${f.status}</span>`;
KG.chip = id => { const e = KG.byId[id]; return `<button class="chip" data-goto="${id}">${KG.shapeIcon(e.axis)}${esc(e.name)}</button>`; };
// Plain-language sentence for a fact (used by the feed and review stack).
KG.sentence = function (f, withSubject = true) {
  const s = KG.byId[f.s];
  const subj = withSubject ? `<b>${esc(s.name)}</b> ` : '';
  const obj = f.o ? `<b>${esc(KG.byId[f.o].name)}</b>` : esc(f.v);
  switch (f.p) {
    case 'role': return `${subj}is ${obj}`;
    case 'member_of': return `${subj}is on ${obj}`;
    case 'has_step': return `${subj}step ${f.order}: ${obj}`;
    case 'purpose': case 'about': case 'states': case 'rule': case 'caveat': case 'decided': case 'status': return `${subj}<span class="muted">${KG.pred(f.p)}:</span> ${obj}`;
    case 'timezone': return `${subj}works in ${obj}`;
    default: return `${subj}${KG.pred(f.p)} ${obj}`;
  }
};

// Shared entity detail renderer (drawer in A/B/C, side panel in D).
KG.renderDetail = function (container, id) {
  const e = KG.byId[id];
  if (!e) { container.innerHTML = ''; return; }
  const facts = KG.factsBy[id].slice().sort((a, b) => (a.status === 'superseded') - (b.status === 'superseded') || (a.order || 99) - (b.order || 99) || b.day - a.day);
  const conf = facts.filter(f => f.status === 'confirmed').length;
  const prop = facts.filter(f => f.status === 'proposed').length;
  const rows = facts.map(f => {
    const outgoing = f.s === id;
    const other = outgoing ? f.o : f.s;
    const pred = outgoing ? KG.pred(f.p) : KG.pred(f.p) + ' (from)';
    const val = other ? KG.chip(other) : esc(f.v);
    const sup = f.supersededBy ? ` <span class="muted">replaced by</span> ${KG.chip(KG.facts.find(x => x.id === f.supersededBy).s)}` : '';
    return `<div class="fact ${f.status}">
      <div class="fp">${pred}${f.order ? ' ' + f.order : ''}</div>
      <div class="fv">${val}${sup}</div>
      <div class="fm">${KG.authBadge(f)}${KG.statusBadge(f)}<span class="src" title="Source">${esc(f.src.kind)}: ${esc(f.src.ref)}</span><span>${KG.DAYS[f.day - 1]}</span>
      ${f.status === 'proposed' ? `<button class="ok" data-confirm="${f.id}" aria-label="Confirm fact">Confirm</button>` : ''}</div></div>`;
  }).join('');
  container.innerHTML = `
    <div class="kicker">${KG.shapeIcon(e.axis)} ${KG.AXES[e.axis].label} <span aria-hidden="true">/</span> ${e.type}</div>
    <h2>${esc(e.name)}</h2>
    ${e.summary ? `<p class="summary">${esc(e.summary)}</p>` : ''}
    <div class="mini-stats"><div><b>${facts.length}</b>facts</div><div><b>${e.degree}</b>links</div><div><b>${conf}</b>confirmed</div><div><b>${prop}</b>proposed</div><div><b>${KG.DAYS[e.day - 1].slice(0, 3)}</b>first seen</div></div>
    <h4>Key facts</h4>${rows || '<p class="muted">No facts yet.</p>'}`;
};

// Delegated clicks inside any detail view: navigate + confirm.
document.addEventListener('click', ev => {
  const go = ev.target.closest('[data-goto]');
  if (go) { ev.preventDefault(); KG.focus(go.dataset.goto); return; }
  const ok = ev.target.closest('[data-confirm]');
  if (ok) { KG.setStatus(ok.dataset.confirm, 'confirmed'); return; }
  const raw = ev.target.closest('.raw-link');
  if (raw) { ev.preventDefault(); KG.toast('Opens the existing "Raw data" view (cards, entity pages, fact lists).'); }
});

KG.setStatus = function (factId, status) {
  const f = KG.facts.find(x => x.id === factId);
  if (!f) return;
  f.status = status;
  KG.bus.emit('status', f);
};

// --- Drawer --------------------------------------------------------------
KG.drawer = {
  el: $('#drawer'), body: $('#drawer-body'), id: null,
  open(id) {
    this.id = id; KG.renderDetail(this.body, id);
    this.el.classList.add('open'); this.el.setAttribute('aria-hidden', 'false');
    $('#stage').classList.add('drawer-open');
    this.body.scrollTop = 0;
  },
  close() {
    this.id = null; this.el.classList.remove('open'); this.el.setAttribute('aria-hidden', 'true');
    $('#stage').classList.remove('drawer-open');
    KG.bus.emit('drawerclose');
  },
};
$('#drawer-close').addEventListener('click', () => KG.drawer.close());
KG.bus.on('status', () => { if (KG.drawer.id) KG.renderDetail(KG.drawer.body, KG.drawer.id); });

// --- Tooltip + toast -----------------------------------------------------
KG.tip = {
  el: $('#tip'),
  show(html, x, y) {
    const st = $('#stage').getBoundingClientRect();
    this.el.innerHTML = html; this.el.classList.add('show');
    const w = this.el.offsetWidth, h = this.el.offsetHeight;
    let lx = x - st.left + 14, ly = y - st.top + 14;
    if (lx + w > st.width - 8) lx = x - st.left - w - 14;
    if (ly + h > st.height - 8) ly = y - st.top - h - 14;
    this.el.style.left = lx + 'px'; this.el.style.top = ly + 'px';
  },
  hide() { this.el.classList.remove('show'); },
};
let toastTimer;
KG.toast = function (msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
};

// Stat pills shared by every mock header.
KG.renderPills = function () {
  const c = KG.facts.filter(f => f.status === 'confirmed').length;
  const p = KG.facts.filter(f => f.status === 'proposed').length;
  const html = `<span class="pill"><b>${KG.entities.length}</b> entities</span><span class="pill"><b>${KG.facts.length}</b> facts</span><span class="pill"><b>${c}</b> confirmed</span><span class="pill"><b>${p}</b> to review</span>`;
  ['a', 'b', 'c', 'd'].forEach(k => { const el = $('#' + k + '-pills'); if (el) el.innerHTML = html; });
};
KG.bus.on('status', KG.renderPills);

// --- Treatments registry + switcher --------------------------------------
KG.treatments = {};   // key -> { init(), show(), hide(), focus(id) }
KG.active = null;
KG.focus = function (id) { const t = KG.treatments[KG.active]; if (t && t.focus) t.focus(id); };

function activate(key, pushHash = true) {
  if (!KG.treatments[key]) key = 'A';
  if (KG.active === key) return;
  const prev = KG.treatments[KG.active];
  if (prev && prev.hide) prev.hide();
  KG.drawer.close(); KG.tip.hide();
  $$('.seg [role=tab]').forEach(b => { const on = b.dataset.t === key; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; });
  $$('.mock').forEach(m => m.classList.toggle('active', m.id === 'mock-' + key));
  $$('.notes').forEach(m => m.classList.toggle('active', m.id === 'notes-' + key));
  KG.active = key;
  const t = KG.treatments[key];
  if (!t.inited) { t.init(); t.inited = true; }
  if (t.show) t.show();
  if (pushHash) history.replaceState(null, '', '#' + key);
}
$$('.seg [role=tab]').forEach(b => b.addEventListener('click', () => activate(b.dataset.t)));
$('.seg').addEventListener('keydown', e => {
  const keys = ['A', 'B', 'C', 'D']; const i = keys.indexOf(KG.active);
  if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
    const n = keys[(i + (e.key === 'ArrowRight' ? 1 : 3)) % 4]; activate(n); $('#tab-' + n).focus(); e.preventDefault();
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { if (KG.drawer.id) KG.drawer.close(); const d = $('#ideas'); if (d.open) d.open = false; }
});
// Close the research popover when clicking elsewhere.
document.addEventListener('click', e => { const d = $('#ideas'); if (d.open && !d.contains(e.target)) d.open = false; });
