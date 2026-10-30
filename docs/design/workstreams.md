# Design note: workstreams

Status: built (2026-10-21): the `workstream` kind, the `part_of` relation, resolution, `ws`,
`assign`, `summary`, `lint`, `list --ws`, and the console's list filter and stats rollup. Tracked
in [ROADMAP.md](../../ROADMAP.md#3-ledger-as-the-protocol-between-sessions).

## Why

The ledger groups work by where it came from (reviews, my PRs, mentions, outreach, tasks) and by
lineage (what came from what). Neither answers "what did I do on X this week?". A weekly stand-up,
stats per effort, the knowledge graph and learning all need to know which effort an item belongs
to, without guessing and without counting an item twice.

A **workstream** is a time-bound effort I drive, such as "Widget search" in the acme world. It is
not:

- **a Jira epic.** An epic is an external container that someone else may own and that outlives or
  undercuts my effort. A workstream may *name* its epic, and the epic then assigns work to it.
- **a knowledge-graph product area or feature.** Those are long-lived capabilities. A workstream is
  *part of* an area for a while and then ends.
- **lineage.** Lineage says how items arose (`spawned_from`, `produces`). A workstream says which
  effort they serve. Lineage is how a workstream spreads from a tagged root to everything that came
  from it.

## Research: how trackers group work

| Tool | Containment | Membership | Depth | Rollups | Status reporting |
|---|---|---|---|---|---|
| Jira | Epic > story > sub-task; Plans add levels above epic (e.g. initiative) | one `parent` per issue (it replaced Epic Link and Parent Link); labels and components for the rest | fixed levels; custom levels only above epic | Plans roll up dates and estimates for planning only, never written back | none built in (Atlassian Goals and Projects carry updates) |
| Linear | Initiatives > projects > issues; project milestones; sub-issues | an issue is in one project at a time; labels for the rest | sub-initiatives up to five levels (Enterprise) | a parent initiative's progress includes its sub-initiatives' projects | project and initiative updates (on track, at risk, off track) on a reminder schedule, with automatic diffs |
| GitHub | Projects, milestones, sub-issues | an issue can be in several Projects, but has one parent and one milestone | sub-issues up to 8 levels, 100 per parent | sub-issue progress shown in Projects; a milestone's percent complete | Project status updates (on track, at risk) |
| Asana | Goals > portfolios > projects > tasks | tasks and projects multi-home in several places | portfolios nest | goal progress from sub-goals or from projects and portfolios | status updates on goals, portfolios and projects |
| Azure DevOps | Epic > feature > story > task | "a work item can have only one Parent"; the parent links form a tree without cycles | fixed portfolio levels | rollup columns computed in the view, per user | none built in |
| Shape Up | Bets on pitches, for a six-week cycle and a fixed appetite | one bet per team per cycle | none: nothing nests | none | the hill chart |
| Atlassian Goals | Goals with sub-goals | a sub-goal works like a goal | nesting | status does not roll up from sub-goals automatically (2024) | monthly updates |

Sources:
- Jira: [custom hierarchy levels](https://support.atlassian.com/jira-software-cloud/docs/configure-custom-hierarchy-levels-in-advanced-roadmaps/),
  [roll-up values](https://support.atlassian.com/jira-software-cloud/docs/roll-up-values-in-advanced-roadmaps/),
  [the parent field replacing Epic Link and Parent Link](https://community.developer.atlassian.com/t/deprecation-of-the-epic-link-parent-link-and-other-related-fields-in-rest-apis-and-webhooks/54048).
- Linear: [projects](https://linear.app/docs/projects), [sub-initiatives](https://linear.app/docs/sub-initiatives),
  [initiative and project updates](https://linear.app/docs/initiative-and-project-updates).
- GitHub: [sub-issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues),
  [sub-issues REST API](https://docs.github.com/en/rest/issues/sub-issues),
  [tasklists retired](https://github.blog/changelog/2025-02-18-github-issues-projects-february-18th-update/),
  [Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects),
  [milestones](https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/about-milestones).
- Asana: [hierarchy](https://help.asana.com/s/article/asana-hierarchy),
  [object hierarchy](https://developers.asana.com/docs/object-hierarchy),
  [goals](https://help.asana.com/s/article/plan-and-manage-company-goals).
- Azure DevOps: [link types](https://learn.microsoft.com/en-us/azure/devops/boards/queries/link-type-reference?view=azure-devops),
  [organizing the backlog](https://learn.microsoft.com/en-us/azure/devops/boards/backlogs/organize-backlog?view=azure-devops),
  [rollup](https://learn.microsoft.com/en-us/azure/devops/boards/backlogs/display-rollup?view=azure-devops).
- Shape Up: [bets](https://basecamp.com/shapeup/2.2-chapter-08), [appetite](https://basecamp.com/shapeup/1.2-chapter-03).
- Atlassian Goals: [what a goal is](https://support.atlassian.com/platform-experiences/docs/what-is-a-goal/),
  [no automatic status rollup](https://community.atlassian.com/forums/Atlassian-Home-questions/Link-sub-goals-to-goal-to-automatically-update-status/qaq-p/2622473).

Not verified from official pages: Asana's rollup through nested portfolios, and whether a Linear
project can sit under more than one initiative.

What this means here:

1. **One primary container per item, plus loose tags.** Jira, Linear, GitHub sub-issues and Azure
   DevOps all give an item exactly one parent and put cross-cutting membership in labels,
   components or Projects. Asana's multi-homing is the exception, and it is what makes rollups
   ambiguous. Here: one `part_of` per item, and `relates_to` for any second tie, which counts
   nowhere.
2. **Nesting is allowed but shallow.** Every tool that nests caps the depth, and Shape Up does not
   nest at all. One person's handful of efforts needs at most an umbrella over a few workstreams.
3. **Rollups are computed, never stored.** Jira Plans does not write rolled-up values back, and
   Azure DevOps computes rollups in the view. Here, membership, inheritance and counts are all
   computed on read.
4. **Status reporting is its own artefact.** Linear, GitHub, Asana and Atlassian all post a dated
   update with a health rating. Here the summary is a *draft* made from data; I write the update
   (see the open questions).
5. **Time-bound by design.** A Shape Up bet ends by default. Here a workstream has `started`,
   `ended` and an optional `target`, and lint flags an active one past its target.

## Hierarchy decision

**A workstream may be `part_of` one other workstream, two levels at most
(`MAX_WORKSTREAM_DEPTH = 2` in `lib/lineage.py`).** Cycles are refused like any family of links. An
item belongs to exactly one workstream, the inner one or the outer one. A summary or count of the
outer workstream includes its children's items, tagged with the child's id.

Why two levels: it covers the common case, an umbrella effort such as "Widget platform" over
"Widget search" and "Widget UI", with no ambiguity about where an item counts. A third level
would mirror a tracker's portfolio hierarchy, which belongs in the tracker; the epic link already
connects to it. The limit is one constant, so it can be raised if a real need appears. When an
item's lineage parents are in an outer and an inner workstream, the inner one wins: it is the more
precise answer, and it lies within the other.

## Data model

- **Kind `workstream`**, id `ws:<slug>` (lower case letters, digits and dashes). States: `active`
  (initial), `paused`, `done` (terminal). `done` is reachable from every state and can be reopened.
  `ws set-state` records `ended` when it finishes one. Workstreams are never pruned.
- **Fields:**
  - `epic`: a ticket id, e.g. `jira:WID-100` (a bare key means Jira);
  - `channels`: Slack channel ids;
  - `repos`;
  - `started`, `ended`, `target`: dates;
  - `area`: a knowledge-graph entity id, e.g. `feature/widget-search`;
  - `aliases`: other names it answers to.

  The title is free to change, and the id never does. Every `<ws>` argument takes the id, the slug
  or an alias in any case. Aliases are unique across workstreams. A rules file may use any of
  these names, so a rename never breaks it.
- **Relation `part_of`** (inverse `has_part`, family `workstream`). For a work item, it names its
  one workstream. For a workstream, it names its parent. A second `part_of` is refused unless
  `link --move` is given, which records leaving the old one. A workstream takes only `part_of` and
  `relates_to`: it is a container, not work with a lineage. Tickets take none: their epic speaks
  for them.
- **Ticket parents.** `ticket-sync <id> --parent <key>` (alias `--epic`) records Jira's `parent`
  field (the epic of a story, the story of a sub-task) as `fields.parent_ticket`.

## Resolution (computed on read, never copied)

An item's workstream is the first of these that gives one:

1. **explicit**: its own `part_of` link;
2. **inherited**: the workstream of where it came from. Lineage parents come first: `spawned_from`,
   `produced_by` and an outreach's `parent`. Then what carries it on: `folded_into` and
   `duplicate_of`. The parent's own answer counts, however it was found, and `show` names the
   ancestor and that answer's basis;
3. **epic**: a ticket it `tracks` that is the `epic` of a workstream, or whose recorded parent
   chain reaches one, up to three hops (sub-task, story, epic);
4. **rule**: a rule in `$BOSUN_CONFIG_DIR/workstreams.json`, or a workstream's own `channels`.
   Rules apply only to items created within the workstream's `started`..`ended` dates;
5. **unassigned**: shown everywhere (`list --ws unassigned`, the summary's last section, the
   console filter, stats), never hidden.

Workstreams and tickets themselves resolve to none (they are not work). `ledger show <id>` prints
`derived.workstream`: the id, the basis and how it was found, for example `inherited from
mypr:acme/widgets#12 (explicit)` or `epic: tracks jira:WID-102 -> jira:WID-101 -> jira:WID-100
(the epic of ws:widget-search)`.

**Rules file** (site-specific, in the config dir; a generic example is
`examples/generic/workstreams.json`):

```json
{"rules": [
  {"ws": "widgets", "repo": ["acme/widgets", "acme/widget-*"], "title": ["widget", "search"]},
  {"ws": "ws:widget-search", "channel": "C0000000042", "kind": ["mention", "outreach"]}
]}
```

Every criterion given must hold; within one, any value will do. `repo` takes globs and is read
from the item's id, its `repo` field or a GitHub URL. `title` takes whole words or phrases, in any
case, matched against the title (and an outreach's subject). `channel` is the origin Slack
channel: a mention's own, a review request's permalink, an outreach's messages and reply, a task's
`via`. `kind` narrows the rule and is refused on its own. A rule that cannot be used (an unknown
workstream, an unknown key, a malformed value) is left out and reported by `lint`. A file that is
not JSON is an error.

## Ambiguity guards

- **Disagreement at one level leaves the item unassigned, with the candidates named.** Parents in
  different, unrelated workstreams; tickets pointing at different workstreams; two rules matching
  different ones; an epic claimed by two workstreams. `show` says "unassigned: where it came from
  (a, b) points at ws:x, ws:y (pick one with part_of)". The item's descendants inherit the
  ambiguity, but lint reports it once, where it arises.
- **Only roots are tagged.** `assign --apply` writes `part_of` only from epic and rule resolutions.
  Such a resolution exists only when nothing above the item has a workstream (inherited comes
  first), so these are always the topmost items of their lineage. Everything below inherits the
  tag.
- **Uncertain means proposed.** `assign` prints conflicts as `propose:` lines with the exact
  `link` commands and never applies them. The orchestrator asks me, as the policy says.
- **Lint** (`ledger lint [--json] [--strict]`):
  - `rule-vs-inherited`: the item inherits one workstream, but its own epic or rule names another
    one that is not nested with it;
  - `overrides-parent`: an explicit tag differs from what its parents give, which is fine when
    meant;
  - `redundant`: an explicit tag equal to what it would inherit anyway;
  - `ambiguous`: a conflict, proposed;
  - `unassigned-growing`: more open, unassigned roots of my own work (PRs, tasks, outreach) created
    this week than last week, and at least three;
  - `done-with-open-items`, `past-target`, `epic-claimed-twice`, and `rules` (unusable rules).

## Summary

`ledger summary --ws <ws> [--since <date|ISO time>] [--markdown]` is a stand-up draft made from
the ledger. `--all` gives one per top-level workstream, then **Unassigned**. The default since is
this Monday, 00:00 local. Sections:

| Section | What | When |
|---|---|---|
| Shipped | PRs merged, tasks done, reviews given, design reviews commented | entered since |
| In review | my open PRs, with whom they wait on (reviewers, me, the merge) and open outreach under them | now |
| Waiting on others | outreach that went out, by recipient | now |
| Decisions | replies to decision, approval and clarification outreach | replied since |
| Blocked | items with open `blocked_by` items; outreach given up on while its work is open | now |
| Next | proposed, accepted and delegated tasks, reviews asked of me, mentions to answer, drafts to send | now |

Each open item lands in one section only (blocked, then in review, then waiting, then next).
Items of a child workstream carry its id. In markdown, items link to their URL.

Decided (2026-10-21): the chores' Monday `morning` run offers a draft for each active workstream
unprompted, since the previous Monday, as a draft under *Needs your approval* (a proposal with the
exact Slack call if I want it posted); see `orchestrator/plugin/commands/chores.md`.

## Example (acme)

```
ledger ws add widget-platform --title "Widget platform"
ledger ws add widget-search --title "Widget search" --parent widget-platform \
  --epic WID-100 --channel C0000000042 --repo acme/widgets --started 2026-10-01 --alias widgets
ledger link mypr:acme/widgets#12 part_of widgets       # the root; its outreach and tasks inherit
ledger ticket-sync jira:WID-101 --status "In Progress" --parent WID-100   # its PRs now resolve by epic
ledger assign --dry-run                                 # rule and epic matches, and proposals
ledger summary --ws widget-platform --since 2026-10-19 --markdown
```

## Knowledge graph

The knowledge graph holds long-lived facts, and a workstream is a ledger item with a lifetime. So
the knowledge graph does **not** mirror workstreams as entities: two sources of truth would drift,
and a finished workstream would leave a stale entity behind. Instead:

- a workstream names its product area in the `area` field, as a knowledge entity id
  (`feature/widget-search`). It is stored now and not validated against the knowledge base;
- later, `kg context --for ws:<slug>` can build a context pack from the area's facts plus the
  workstream's repos, so delegated tasks in a workstream get the right pack;
- decisions recorded during a workstream (its Decisions section) are candidates for `decision`
  facts `about` the area. Learning can propose them with the ledger item as the source.

Implementing these needs the knowledge pass of the learning run (MVP 3+), so it is design only
for now.

## Console

`/api/items` rows carry `workstream` (the resolved id; a workstream row carries its own id; "" for
none). `?ws=<id|alias|unassigned>` filters, including the workstreams under the one given, and an
unknown name is a 400. The item page model carries `workstream_how`. `/api/stats` adds
`workstreams`: items opened and closed in the period per top-level workstream, and the
unassigned ones. The console reads the same `workstreams.json` from the config dir. The page has
no UI for these yet; that is a later step.

## Open questions

- Should `summary` be saved as a dated status update with a health rating (on track, at risk, off
  track), so the next one can show what changed, as Linear and GitHub do?
- Should a spawned task with no evidence of its own take its source's workstream into its
  delegation brief (with the area's context pack), once context packs exist?
- Reviews of colleagues' PRs are mostly unassigned. Should a review count toward a workstream when
  the PR's repo and title match its rules (today it does; a rule can narrow `kind`)?
