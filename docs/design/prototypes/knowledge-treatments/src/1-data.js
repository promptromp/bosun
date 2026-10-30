'use strict';
/* =========================================================================
   DATA: an entirely fictional "Acme" knowledge base. The shape mirrors the
   real store (entities + sourced facts along People / Process / Product /
   Systems), but every name, channel, ticket and identifier is invented.
   ========================================================================= */
const KG = {};

KG.DAYS = ['Wed 14 Oct', 'Thu 15 Oct', 'Fri 16 Oct', 'Sat 17 Oct', 'Sun 18 Oct', 'Mon 19 Oct', 'Tue 20 Oct'];

KG.AXES = {
  people:  { label: 'People',  shape: 'circle'  },
  process: { label: 'Process', shape: 'diamond' },
  product: { label: 'Product', shape: 'square'  },
  systems: { label: 'Systems', shape: 'hex'     },
};
KG.AXIS_ORDER = ['people', 'process', 'product', 'systems'];
KG.TYPE_AXIS = {
  person: 'people', team: 'people',
  process: 'process', step: 'process', gate: 'process', channel: 'process',
  repo: 'product', service: 'product', feature: 'product', decision: 'product', principle: 'product', lesson: 'product',
  system: 'systems', environment: 'systems',
};
KG.TYPE_PLURAL = { person: 'People', team: 'Teams', process: 'Processes', step: 'Steps', gate: 'Gates', channel: 'Channels',
  repo: 'Repos', service: 'Services', feature: 'Features', decision: 'Decisions', principle: 'Principles', lesson: 'Lessons',
  system: 'Systems', environment: 'Environments' };

/* [id, type, name, summary] */
const ENTITY_ROWS = [
  // People
  ['p.you', 'person', 'You', 'Engineer; owns the invoice exporter'],
  ['p.priya', 'person', 'Priya Shah', 'PM, invoices and localization'],
  ['p.marco', 'person', 'Marco Rossi', 'Platform engineer, leads on-call'],
  ['p.lena', 'person', 'Lena Okafor', 'Finance operations lead'],
  ['p.tomas', 'person', 'Tomas Lind', 'Tech lead, reviews exporter changes'],
  ['p.aiko', 'person', 'Aiko Tanaka', 'Billing engineer'],
  ['p.dev', 'person', 'Dev Patel', 'Backend engineer'],
  ['p.sofia', 'person', 'Sofia Marin', 'QA lead'],
  ['p.noah', 'person', 'Noah Berg', 'Platform engineer'],
  ['p.grace', 'person', 'Grace Kim', 'Product designer'],
  ['p.omar', 'person', 'Omar Haddad', 'Data analyst'],
  ['p.ines', 'person', 'Ines Duarte', 'Localization PM'],
  ['p.ben', 'person', 'Ben Carver', 'Support engineer'],
  ['t.billing', 'team', 'Billing', 'Builds the invoice exporter'],
  ['t.platform', 'team', 'Platform', 'CI, environments, on-call'],
  ['t.product', 'team', 'Product', 'PM and design'],
  ['t.finance', 'team', 'Finance ops', 'Close, dunning, billing questions'],
  // Process
  ['pr.release', 'process', 'Release to prod', 'Merge, staging, canary checks, gradual rollout'],
  ['pr.bug', 'process', 'Customer bug report', 'From #billing-support to a fix'],
  ['pr.review', 'process', 'Code review', 'Every change reviewed before merge'],
  ['pr.launch', 'process', 'Feature launch', 'A flag turned on in stages'],
  ['pr.hotfix', 'process', 'Hotfix', 'Urgent fix outside the normal cadence'],
  ['pr.incident', 'process', 'Incident response', 'Declare, mitigate, follow up'],
  ['pr.l10n', 'process', 'Localization update', 'Freeze, translate, roll out behind a flag'],
  ['st.merge', 'step', 'Merge to main', 'Step 1 of release'],
  ['st.staging', 'step', 'Deploy to staging', 'Step 2 of release'],
  ['st.canary', 'step', 'Canary checks', 'Step 3 of release'],
  ['st.rollout', 'step', 'Gradual rollout', 'Step 4 of release'],
  ['st.announce', 'step', 'Announce in #deploys', 'Step 5 of release'],
  ['g.smoke', 'gate', 'Smoke tests', 'Canary check'],
  ['g.errors', 'gate', 'Error rate', 'Canary check'],
  ['g.checks', 'gate', 'PR checks', 'Review gate'],
  ['ch.support', 'channel', '#billing-support', 'Customer billing questions'],
  ['ch.deploys', 'channel', '#deploys', 'Deploy announcements'],
  ['ch.incidents', 'channel', '#incidents', 'Incident coordination'],
  ['ch.reviews', 'channel', '#code-reviews', 'Review requests'],
  ['ch.team', 'channel', '#acme-billing', 'Billing team channel'],
  // Product
  ['r.invoices', 'repo', 'invoice-api', 'Invoice API and exports'],
  ['r.web', 'repo', 'web-app', 'Customer-facing web app'],
  ['r.pdf', 'repo', 'pdf-templates', 'Invoice and credit-note templates'],
  ['r.tax', 'repo', 'tax-rules', 'Tax rates and rounding per country'],
  ['sv.gen', 'service', 'invoice-renderer', 'Renders invoices as PDF'],
  ['sv.trans', 'service', 'translator', 'Translation service'],
  ['sv.notify', 'service', 'notifier', 'Emails invoices and dunning reminders'],
  ['ft.invoice', 'feature', 'Invoice export', 'Invoices as PDF and JSON'],
  ['ft.l10n', 'feature', 'Localization', 'Invoices and UI in 12 languages'],
  ['ft.batch', 'feature', 'Batch exports', 'Many invoices in one download'],
  ['ft.credit', 'feature', 'Credit notes', 'Corrections to issued invoices'],
  ['dc.limit20', 'decision', '20 MB batch limit', 'Superseded'],
  ['dc.limit50', 'decision', '50 MB batch limit', 'Raised from 20 MB'],
  ['dc.firstday', 'decision', 'Invoice on the 1st', 'Superseded'],
  ['dc.billday', 'decision', 'Invoice on the billing day', 'Each customer on their own day'],
  ['dc.pdf', 'decision', 'PDF-only export', 'Superseded'],
  ['dc.pdfjson', 'decision', 'PDF and JSON export', 'Adds JSON for the ERP'],
  ['dc.locale', 'decision', 'Invoice language per customer', 'Account default otherwise'],
  ['dc.credit', 'decision', 'Credit notes, never edits', 'Issued invoices stay as issued'],
  ['dc.retire', 'decision', 'Retire staging-2', 'One staging environment is enough'],
  ['pp.evidence', 'principle', 'Evidence before assertions', ''],
  ['pp.small', 'principle', 'Small PRs', ''],
  ['pp.latest', 'principle', 'Latest decision wins', ''],
  ['ls.rounding', 'lesson', 'Round tax per line', ''],
  ['ls.canary', 'lesson', 'Watch the canary first', ''],
  ['ls.retired', 'lesson', 'Test on staging, not staging-2', ''],
  ['ls.credit', 'lesson', 'Never edit an issued invoice', ''],
  // Systems
  ['sy.ci', 'system', 'CI pipeline', 'Build, test, deploy'],
  ['sy.tickets', 'system', 'Ticket tracker', 'Bugs, incidents, follow-ups'],
  ['sy.queue', 'system', 'Message queue', 'Async jobs: rendering, emails'],
  ['sy.erp', 'system', 'ERP connector', 'Syncs invoices to the ERP'],
  ['sy.obs', 'system', 'Observability', 'Dashboards, logs, alerts'],
  ['sy.oncall', 'system', 'Paging', 'Pages the on-call engineer'],
  ['sy.flags', 'system', 'Feature flags', 'Gradual rollout'],
  ['en.dev', 'environment', 'dev', 'Shared development'],
  ['en.staging', 'environment', 'staging', 'Pre-prod; every merge lands here first'],
  ['en.prod', 'environment', 'prod', 'Production'],
  ['en.staging2', 'environment', 'staging-2', 'Retired'],
];

KG.entities = ENTITY_ROWS.map(([id, type, name, summary]) => ({ id, type, name, summary, axis: KG.TYPE_AXIS[type] }));
KG.byId = Object.fromEntries(KG.entities.map(e => [e.id, e]));

/* Fact helper. x is an entity id (relation) or a literal value.
   auth: U user said, S stakeholder said, O observed, I inferred.
   src: "kind:ref" (all refs invented). opts: st c|s (confirmed / superseded),
   ord (step order), rw (times the fact was reused instead of re-asking). */
KG.facts = [];
const AUTH = { U: 'user_said', S: 'stakeholder_said', O: 'observed', I: 'inferred' };
function F(s, p, x, a, day, src, opts = {}) {
  const isRel = Object.prototype.hasOwnProperty.call(KG.byId, x);
  const [kind, ...ref] = src.split(':');
  KG.facts.push({
    id: 'f' + (KG.facts.length + 1), s, p,
    o: isRel ? x : null, v: isRel ? null : x,
    auth: AUTH[a], day, src: { kind, ref: ref.join(':') },
    status: opts.st === 'c' ? 'confirmed' : opts.st === 's' ? 'superseded' : 'proposed',
    order: opts.ord || null, reuse: opts.rw || 0, supersededBy: null,
  });
}

// --- People (day 1 survey, then filled in) -------------------------------
F('p.you', 'role', 'Engineer; owns the invoice exporter end to end', 'U', 1, 'chat:session 1a', { st: 'c', rw: 2 });
F('p.you', 'member_of', 't.billing', 'O', 1, 'slack:#acme-billing', { st: 'c' });
F('p.you', 'owns', 'r.invoices', 'U', 1, 'chat:session 1a', { st: 'c' });
F('p.you', 'prefers_channel', 'ch.team', 'O', 1, 'slack:#acme-billing');
F('p.priya', 'role', 'Product manager for invoices and localization', 'O', 1, 'slack:#acme-billing', { rw: 1 });
F('p.priya', 'member_of', 't.product', 'O', 1, 'slack:#acme-billing');
F('p.priya', 'owns', 'ft.invoice', 'O', 2, 'ticket:ACME-1042');
F('p.priya', 'timezone', 'Europe/London', 'O', 2, 'slack:profile');
F('p.priya', 'prefers_channel', 'ch.team', 'O', 2, 'slack:#acme-billing');
F('p.marco', 'role', 'Platform engineer; leads the on-call rotation', 'S', 1, 'slack:#incidents', { rw: 3 });
F('p.marco', 'member_of', 't.platform', 'O', 1, 'slack:#incidents');
F('p.marco', 'prefers_channel', 'ch.incidents', 'S', 1, 'slack:#incidents');
F('p.marco', 'timezone', 'Europe/Rome', 'O', 3, 'slack:profile');
F('p.marco', 'owns', 'en.prod', 'O', 3, 'doc:on-call rota');
F('p.lena', 'role', 'Finance operations lead; runs the month-end close', 'O', 1, 'slack:#acme-billing');
F('p.lena', 'member_of', 't.finance', 'O', 1, 'slack:#acme-billing');
F('p.lena', 'reviews', 'ft.credit', 'S', 4, 'ticket:ACME-1101');
F('p.tomas', 'role', 'Tech lead: reviews exporter changes and owns its test suite', 'U', 1, 'chat:session 1a', { st: 'c', rw: 4 });
F('p.tomas', 'member_of', 't.billing', 'O', 1, 'slack:#acme-billing');
F('p.tomas', 'reviews', 'r.invoices', 'U', 1, 'chat:session 1a', { st: 'c' });
F('p.aiko', 'role', 'Billing engineer; tax rules and currencies', 'U', 1, 'chat:session 1a', { st: 'c', rw: 3 });
F('p.aiko', 'member_of', 't.billing', 'O', 1, 'slack:#acme-billing');
F('p.aiko', 'owns', 'r.tax', 'O', 2, 'pr:tax-rules#88');
F('p.dev', 'role', 'Backend engineer on the invoice renderer', 'O', 2, 'pr:invoice-api#412');
F('p.dev', 'member_of', 't.billing', 'O', 2, 'slack:#acme-billing');
F('p.dev', 'owns', 'sv.gen', 'O', 2, 'pr:invoice-api#412');
F('p.sofia', 'role', 'QA lead; maintains the smoke suite', 'O', 2, 'slack:#deploys');
F('p.sofia', 'member_of', 't.platform', 'O', 2, 'slack:#deploys');
F('p.sofia', 'owns', 'g.smoke', 'O', 2, 'doc:release runbook');
F('p.noah', 'role', 'Platform engineer; CI and environments', 'O', 3, 'doc:service catalog');
F('p.noah', 'member_of', 't.platform', 'O', 3, 'doc:service catalog');
F('p.noah', 'owns', 'sy.ci', 'O', 3, 'doc:service catalog');
F('p.grace', 'role', 'Product designer', 'O', 4, 'ticket:ACME-1101');
F('p.grace', 'member_of', 't.product', 'O', 4, 'ticket:ACME-1101');
F('p.grace', 'reviews', 'r.web', 'O', 4, 'pr:web-app#977');
F('p.omar', 'role', 'Data analyst; revenue and aging reports', 'O', 3, 'slack:#acme-billing');
F('p.omar', 'member_of', 't.finance', 'O', 3, 'slack:#acme-billing');
F('p.omar', 'reviews', 'r.tax', 'O', 4, 'pr:tax-rules#91');
F('p.ines', 'role', 'Localization PM', 'S', 5, 'slack:#acme-billing');
F('p.ines', 'member_of', 't.product', 'S', 5, 'slack:#acme-billing');
F('p.ines', 'owns', 'ft.l10n', 'S', 5, 'ticket:ACME-1130');
F('p.ben', 'role', 'Support engineer; answers customer billing questions', 'O', 3, 'slack:#billing-support');
F('p.ben', 'member_of', 't.finance', 'O', 3, 'slack:#billing-support');
F('p.ben', 'owns', 'ch.support', 'O', 3, 'slack:#billing-support');
F('t.billing', 'purpose', 'Builds the exporter: rendering, taxes, exports', 'O', 1, 'slack:#acme-billing');
F('t.platform', 'purpose', 'CI, environments, release checks and the on-call rotation', 'O', 3, 'doc:service catalog');
F('t.platform', 'owns', 'pr.incident', 'S', 3, 'slack:#incidents', { rw: 2 });
F('t.finance', 'purpose', 'Month-end close, dunning and customer billing questions', 'O', 3, 'slack:#billing-support');

// --- Release process (day 2, 5 ordered steps) ----------------------------
F('pr.release', 'purpose', 'Ship merged changes to production in small, checked stages', 'U', 2, 'chat:session 2c', { st: 'c', rw: 3 });
F('pr.release', 'has_step', 'st.merge', 'U', 2, 'chat:session 2c', { ord: 1 });
F('pr.release', 'has_step', 'st.staging', 'U', 2, 'chat:session 2c', { ord: 2 });
F('pr.release', 'has_step', 'st.canary', 'U', 2, 'chat:session 2c', { ord: 3 });
F('pr.release', 'has_step', 'st.rollout', 'U', 2, 'chat:session 2c', { ord: 4 });
F('pr.release', 'has_step', 'st.announce', 'O', 2, 'slack:#deploys', { ord: 5 });
F('pr.release', 'entry_point', 'sy.ci', 'O', 2, 'doc:release runbook');
F('pr.release', 'actor', 'p.you', 'O', 2, 'slack:#deploys');
F('pr.release', 'actor', 'p.sofia', 'O', 2, 'slack:#deploys');
F('pr.release', 'actor', 'p.noah', 'O', 3, 'slack:#deploys');
F('pr.release', 'caveat', 'Hotfixes skip the gradual rollout but still wait for smoke tests', 'S', 4, 'slack:#deploys');
F('st.merge', 'uses', 'sy.ci', 'O', 2, 'doc:release runbook');
F('st.staging', 'uses', 'en.staging', 'O', 2, 'doc:release runbook');
F('st.staging', 'rule', 'Every merge deploys to staging first; nothing goes straight to production', 'U', 2, 'chat:session 2c', { rw: 2 });
F('st.canary', 'gated_by', 'g.smoke', 'O', 2, 'doc:release runbook');
F('st.canary', 'gated_by', 'g.errors', 'O', 3, 'doc:release runbook');
F('st.canary', 'states', 'Canary checks run on a small share of production traffic for about 30 minutes', 'O', 2, 'slack:#deploys', { rw: 1 });
F('g.smoke', 'uses', 'sy.ci', 'O', 2, 'doc:release runbook');
F('g.smoke', 'rule', 'Smoke tests export one invoice of each kind and compare the totals', 'O', 2, 'doc:release runbook');
F('g.errors', 'uses', 'sy.obs', 'O', 3, 'doc:release runbook');
F('g.errors', 'rule', 'The error rate must stay below 1% during the canary', 'S', 3, 'slack:#deploys');
F('g.checks', 'uses', 'sy.ci', 'O', 2, 'doc:contributing guide');
F('g.checks', 'rule', 'Lint and unit tests pass before anyone is asked to review', 'O', 2, 'doc:contributing guide');
F('st.rollout', 'uses', 'sy.flags', 'O', 2, 'doc:release runbook');
F('st.rollout', 'uses', 'en.prod', 'O', 2, 'doc:release runbook');
F('st.rollout', 'rule', 'Roll out to 10%, then 50%, then all customers', 'U', 2, 'chat:session 2c', { st: 'c', rw: 3 });
F('st.announce', 'posts_to', 'ch.deploys', 'O', 2, 'slack:#deploys');

// --- Customer bug report -------------------------------------------------
F('pr.bug', 'purpose', 'Turn a customer billing problem into a fix', 'O', 3, 'slack:#billing-support');
F('pr.bug', 'entry_point', 'ch.support', 'S', 3, 'slack:#billing-support', { rw: 2 });
F('pr.bug', 'rule', 'Reproduce on staging with a copy of the invoice before fixing anything', 'S', 3, 'slack:#billing-support', { rw: 2 });
F('pr.bug', 'uses', 'sy.tickets', 'O', 3, 'slack:#billing-support');
F('pr.bug', 'uses', 'en.staging', 'O', 3, 'slack:#billing-support');
F('pr.bug', 'actor', 'p.ben', 'S', 3, 'slack:#billing-support');
F('pr.bug', 'actor', 'p.you', 'O', 3, 'slack:#billing-support');
F('pr.bug', 'caveat', 'Anything that stops invoices going out also pages the on-call engineer', 'O', 6, 'doc:on-call rota');
F('sy.oncall', 'states', 'Pages the on-call engineer within 5 minutes, day or night', 'O', 3, 'doc:on-call rota');

// --- Code review ---------------------------------------------------------
F('pr.review', 'purpose', 'Every change is reviewed before merge', 'U', 1, 'chat:session 1a', { st: 'c' });
F('pr.review', 'rule', 'The tech lead reviews every change to the exporter', 'U', 1, 'chat:session 1a', { st: 'c', rw: 3 });
F('pr.review', 'rule', 'Changes to tax rules need a second reviewer from billing', 'U', 1, 'chat:session 1a', { st: 'c', rw: 3 });
F('pr.review', 'actor', 'p.tomas', 'U', 1, 'chat:session 1a');
F('pr.review', 'actor', 'p.aiko', 'U', 1, 'chat:session 1a');
F('pr.review', 'uses', 'r.invoices', 'O', 1, 'pr:invoice-api#403');
F('pr.review', 'gated_by', 'g.checks', 'O', 1, 'pr:invoice-api#403');
F('pr.review', 'posts_to', 'ch.reviews', 'O', 2, 'slack:#code-reviews');

// --- Feature launch, hotfix, incident, localization ----------------------
F('pr.launch', 'purpose', 'Turn a finished feature on for customers in stages', 'O', 3, 'doc:launch checklist');
F('pr.launch', 'uses', 'sy.flags', 'O', 3, 'doc:launch checklist');
F('pr.launch', 'depends_on', 'pr.release', 'I', 3, 'doc:launch checklist');
F('pr.launch', 'actor', 'p.priya', 'O', 3, 'slack:#acme-billing');
F('pr.launch', 'actor', 'p.grace', 'O', 3, 'slack:#acme-billing');
F('pr.launch', 'caveat', 'Beta customers hear a week before a flag turns on', 'O', 5, 'slack:#acme-billing');
F('pr.hotfix', 'purpose', 'Ship an urgent fix outside the normal cadence', 'O', 4, 'slack:#deploys');
F('pr.hotfix', 'rule', 'Hotfix branches are cut from the prod tag, not main', 'O', 4, 'doc:release runbook', { rw: 1 });
F('pr.hotfix', 'uses', 'sy.ci', 'O', 4, 'doc:release runbook');
F('pr.hotfix', 'gated_by', 'g.smoke', 'S', 4, 'slack:#deploys');
F('pr.hotfix', 'actor', 'p.marco', 'O', 4, 'slack:#deploys');
F('p.tomas', 'approves', 'pr.hotfix', 'S', 4, 'slack:#deploys');
F('pr.incident', 'purpose', 'Coordinate the response when prod degrades', 'O', 5, 'doc:incident guide');
F('pr.incident', 'entry_point', 'ch.incidents', 'O', 5, 'doc:incident guide');
F('pr.incident', 'uses', 'sy.obs', 'O', 5, 'doc:incident guide');
F('pr.incident', 'uses', 'sy.oncall', 'I', 6, 'slack:#incidents');
F('pr.incident', 'actor', 'p.marco', 'O', 5, 'slack:#incidents');
F('pr.incident', 'rule', 'Declare in #incidents first, then open the ticket', 'S', 6, 'slack:#incidents');
F('pr.incident', 'uses', 'sy.tickets', 'O', 6, 'slack:#incidents');
F('pr.l10n', 'purpose', 'Translate the UI and invoices into supported languages', 'S', 5, 'ticket:ACME-1130');
F('pr.l10n', 'uses', 'sv.trans', 'O', 5, 'ticket:ACME-1130');
F('pr.l10n', 'actor', 'p.ines', 'S', 5, 'ticket:ACME-1130');
F('pr.l10n', 'uses', 'sy.flags', 'O', 6, 'ticket:ACME-1131');
F('pr.l10n', 'depends_on', 'pr.release', 'I', 6, 'ticket:ACME-1131');
F('pr.l10n', 'rule', 'New strings freeze three days before a language ships', 'S', 6, 'slack:#acme-billing');

// --- Product: repos, services, features ----------------------------------
F('r.invoices', 'purpose', 'API that issues and exports invoices', 'O', 1, 'pr:invoice-api#403');
F('r.web', 'purpose', 'Customer-facing web app', 'O', 2, 'pr:web-app#977');
F('r.pdf', 'purpose', 'Invoice and credit-note PDF templates', 'O', 1, 'pr:pdf-templates#203');
F('r.tax', 'purpose', 'Tax rates and rounding rules per country', 'O', 2, 'pr:tax-rules#88');
F('sv.gen', 'lives_in', 'r.invoices', 'O', 2, 'pr:invoice-api#412');
F('sv.gen', 'depends_on', 'sy.queue', 'O', 3, 'pr:invoice-api#415');
F('sv.gen', 'depends_on', 'sv.trans', 'O', 5, 'pr:invoice-api#420');
F('sv.trans', 'lives_in', 'r.web', 'O', 5, 'pr:web-app#981');
F('sv.notify', 'lives_in', 'r.invoices', 'O', 3, 'pr:invoice-api#416');
F('sv.notify', 'depends_on', 'sy.queue', 'O', 3, 'pr:invoice-api#416');
F('ft.invoice', 'about', 'Issues invoices with line items, taxes, currencies and payment terms', 'O', 1, 'ticket:ACME-1042');
F('ft.invoice', 'lives_in', 'r.invoices', 'O', 1, 'ticket:ACME-1042');
F('ft.invoice', 'uses', 'r.pdf', 'O', 1, 'pr:pdf-templates#203');
F('ft.invoice', 'uses', 'sv.gen', 'O', 2, 'pr:invoice-api#412');
F('ft.l10n', 'about', 'Invoices and UI in 12 languages', 'S', 5, 'ticket:ACME-1130');
F('ft.l10n', 'uses', 'sv.trans', 'O', 5, 'ticket:ACME-1130');
F('ft.l10n', 'uses', 'sy.flags', 'I', 6, 'ticket:ACME-1131');
F('ft.batch', 'about', 'Download many invoices as one archive', 'O', 3, 'ticket:ACME-1077');
F('ft.batch', 'part_of', 'ft.invoice', 'O', 3, 'ticket:ACME-1077');
F('ft.credit', 'about', 'Credit notes that correct issued invoices', 'S', 4, 'ticket:ACME-1101');
F('ft.credit', 'part_of', 'ft.invoice', 'S', 4, 'ticket:ACME-1101');
F('ft.credit', 'caveat', "A credit note keeps the original invoice's currency and tax rates", 'S', 4, 'ticket:ACME-1101', { rw: 1 });
F('ft.credit', 'uses', 'r.tax', 'O', 4, 'pr:tax-rules#90');

// --- Decisions (three supersede earlier ones) ----------------------------
F('dc.limit20', 'decided', 'Batch exports are limited to 20 MB', 'O', 1, 'ticket:ACME-1077', { st: 's' });
F('dc.limit20', 'about', 'ft.batch', 'O', 1, 'ticket:ACME-1077');
F('dc.limit50', 'decided', 'Raise the batch export limit to 50 MB', 'S', 4, 'slack:#acme-billing');
F('dc.limit50', 'supersedes', 'dc.limit20', 'S', 4, 'slack:#acme-billing');
F('dc.limit50', 'about', 'ft.batch', 'O', 4, 'slack:#acme-billing');
F('dc.firstday', 'decided', 'All invoices go out on the 1st of the month', 'O', 1, 'doc:billing handbook', { st: 's' });
F('dc.billday', 'decided', 'Each customer is invoiced on their own billing day', 'U', 2, 'chat:session 2c', { st: 'c', rw: 3 });
F('dc.billday', 'supersedes', 'dc.firstday', 'U', 2, 'chat:session 2c', { st: 'c' });
F('dc.billday', 'about', 'ft.invoice', 'O', 2, 'chat:session 2c');
F('p.lena', 'approves', 'dc.billday', 'S', 2, 'slack:#acme-billing');
F('dc.pdf', 'decided', 'Export invoices as PDF only', 'O', 1, 'ticket:ACME-1042', { st: 's' });
F('dc.pdf', 'about', 'ft.invoice', 'O', 1, 'ticket:ACME-1042');
F('dc.pdfjson', 'decided', 'Export as PDF and JSON, so the ERP can import invoices', 'S', 5, 'ticket:ACME-1120');
F('dc.pdfjson', 'supersedes', 'dc.pdf', 'S', 5, 'ticket:ACME-1120');
F('dc.pdfjson', 'about', 'ft.invoice', 'O', 5, 'ticket:ACME-1120');
F('dc.locale', 'decided', 'Each customer picks an invoice language; the account default otherwise', 'S', 5, 'ticket:ACME-1130');
F('dc.locale', 'about', 'ft.l10n', 'O', 5, 'ticket:ACME-1130');
F('dc.credit', 'decided', 'An issued invoice is never edited; a credit note corrects it', 'S', 4, 'ticket:ACME-1101');
F('dc.credit', 'about', 'ft.credit', 'O', 4, 'ticket:ACME-1101');
F('dc.retire', 'decided', 'Retire staging-2; one staging environment is enough', 'O', 3, 'doc:environments page');
F('dc.retire', 'about', 'en.staging2', 'O', 3, 'doc:environments page');

// --- Principles and lessons ----------------------------------------------
F('pp.evidence', 'states', 'Show evidence before claiming something works', 'U', 1, 'chat:session 1a', { st: 'c', rw: 2 });
F('pp.small', 'states', 'Small PRs, each reviewable in one sitting', 'U', 2, 'chat:session 2c', { rw: 2 });
F('pp.small', 'about', 'pr.review', 'I', 2, 'chat:session 2c');
F('pp.latest', 'states', 'When stakeholders disagree, the latest decision wins', 'U', 3, 'chat:session 3b', { rw: 1 });
F('ls.rounding', 'states', 'Round tax per line, then add up; rounding only the total is off by a cent', 'U', 3, 'chat:session 3b', { rw: 3 });
F('ls.rounding', 'about', 'r.tax', 'O', 3, 'chat:session 3b');
F('ls.rounding', 'about', 'ft.invoice', 'I', 3, 'chat:session 3b');
F('ls.canary', 'states', 'Watch the error rate through the first rollout stage before calling a deploy healthy', 'O', 4, 'slack:#deploys', { rw: 2 });
F('ls.canary', 'about', 'g.errors', 'O', 4, 'slack:#deploys');
F('ls.retired', 'states', 'staging-2 is retired; point test runs at staging', 'O', 5, 'chat:session 5d', { rw: 1 });
F('ls.retired', 'about', 'en.staging2', 'O', 5, 'chat:session 5d');
F('ls.credit', 'states', 'Editing an issued invoice broke the ERP sync; issue a credit note instead', 'S', 6, 'slack:#billing-support', { rw: 1 });
F('ls.credit', 'about', 'dc.credit', 'O', 6, 'slack:#billing-support');
F('ls.credit', 'about', 'sy.erp', 'I', 6, 'slack:#billing-support');

// --- Systems and environments --------------------------------------------
F('sy.ci', 'purpose', 'Builds, tests and deploys every merge', 'O', 2, 'doc:service catalog');
F('sy.ci', 'uses', 'en.staging', 'O', 2, 'doc:release runbook');
F('sy.tickets', 'purpose', 'Tracks bugs, incidents and follow-ups', 'O', 2, 'doc:service catalog');
F('sy.queue', 'purpose', 'Async jobs: PDF rendering and emails', 'O', 3, 'doc:service catalog');
F('sy.erp', 'purpose', 'Syncs issued invoices and credit notes to the ERP', 'O', 3, 'doc:service catalog');
F('sy.erp', 'uses', 'r.invoices', 'O', 3, 'doc:service catalog');
F('sy.erp', 'caveat', 'A failed sync retries three times, then opens a ticket', 'O', 4, 'doc:service catalog');
F('sy.obs', 'purpose', 'Dashboards, logs and alerts', 'O', 5, 'doc:incident guide');
F('sy.obs', 'about', 'en.prod', 'O', 5, 'doc:incident guide');
F('sy.oncall', 'purpose', 'Paging for the on-call rotation', 'O', 3, 'doc:on-call rota');
F('sy.oncall', 'posts_to', 'ch.incidents', 'O', 3, 'doc:on-call rota');
F('sy.flags', 'purpose', 'Feature flags for gradual rollout', 'O', 4, 'doc:service catalog');
F('en.dev', 'purpose', 'Shared development environment', 'O', 2, 'doc:service catalog');
F('en.staging', 'purpose', 'Pre-production; every merge deploys here first', 'O', 2, 'doc:release runbook');
F('en.prod', 'purpose', 'Production', 'O', 2, 'doc:service catalog');
F('en.staging2', 'status', 'Retired; kept only until its config is removed', 'O', 5, 'chat:session 5d');
F('en.staging2', 'caveat', 'Do not deploy or run tests there', 'S', 6, 'doc:environments page', { rw: 1 });
F('ch.support', 'purpose', 'Customer billing questions from support', 'S', 3, 'slack:#billing-support');
F('ch.deploys', 'purpose', 'Deploy announcements', 'O', 2, 'slack:#deploys');
F('ch.incidents', 'purpose', 'Incident declaration and updates', 'O', 5, 'doc:incident guide');
F('ch.reviews', 'purpose', 'Review requests', 'O', 2, 'slack:#code-reviews');
F('ch.team', 'purpose', 'Billing team day-to-day', 'O', 1, 'slack:#acme-billing');
F('p.you', 'prefers_channel', 'ch.reviews', 'O', 7, 'slack:#code-reviews');
F('pr.release', 'caveat', 'No deploys after 15:00 on Fridays', 'O', 7, 'slack:#deploys');
F('sy.obs', 'states', 'The error-rate alert fires above 2% for 5 minutes', 'O', 7, 'slack:#incidents');
F('dc.limit50', 'caveat', 'Larger batches are split into several downloads', 'S', 7, 'slack:#acme-billing');
F('p.dev', 'reviews', 'r.invoices', 'O', 7, 'pr:invoice-api#423');

// Wire supersession: the old decision's "decided" fact points at the new one.
(function linkSupersession() {
  KG.facts.filter(f => f.p === 'supersedes').forEach(f => {
    const oldFact = KG.facts.find(x => x.s === f.o && x.p === 'decided');
    const newFact = KG.facts.find(x => x.s === f.s && x.p === 'decided');
    if (oldFact && newFact) { oldFact.supersededBy = newFact.id; oldFact.status = 'superseded'; }
  });
})();

// Derived indexes: facts per entity, neighbors, degree, first-seen day.
KG.reindex = function reindex() {
  KG.factsBy = {}; KG.nbrs = {};
  KG.entities.forEach(e => { KG.factsBy[e.id] = []; KG.nbrs[e.id] = new Set(); e.day = 8; });
  KG.facts.forEach(f => {
    KG.factsBy[f.s].push(f);
    KG.byId[f.s].day = Math.min(KG.byId[f.s].day, f.day);
    if (f.o) {
      KG.factsBy[f.o].push(f);
      KG.nbrs[f.s].add(f.o); KG.nbrs[f.o].add(f.s);
      KG.byId[f.o].day = Math.min(KG.byId[f.o].day, f.day);
    }
  });
  KG.entities.forEach(e => { e.degree = KG.nbrs[e.id].size; e.nFacts = KG.factsBy[e.id].length; if (e.day > 7) e.day = 7; });
};
KG.reindex();

// Relation links (deduplicated pairs), used by A and D.
KG.links = (function buildLinks() {
  const m = new Map();
  KG.facts.filter(f => f.o).forEach(f => {
    const key = f.s < f.o ? f.s + '|' + f.o : f.o + '|' + f.s;
    const cur = m.get(key);
    if (!cur) m.set(key, { s: f.s, t: f.o, facts: [f], day: f.day });
    else { cur.facts.push(f); cur.day = Math.min(cur.day, f.day); }
  });
  return [...m.values()];
})();
KG.linkStatus = l => l.facts.some(f => f.status === 'confirmed') ? 'confirmed' : l.facts.every(f => f.status === 'superseded') ? 'superseded' : 'proposed';
