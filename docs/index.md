---
template: home.html
title: bosun
hide:
  - navigation
  - toc
---

<!-- The docs site's home page (theme/home.html lays it out; GitHub shows README.md instead). The
live examples are drawn by docs/assets/home/examples.js from bosun's own tables and its fictional
acme data (tools/docs_examples.py); each one says so, and each says where the guide is. -->

<section class="bx-hero" markdown>
<div class="bx-hero__pitch" markdown>

# Turn your coding agents into proactive teammates. { .bx-hero__title }

<p class="bx-define"><dfn>bosun</dfn> <span class="bx-define__say">/ˈboʊsən/ BOH-sən</span> <i>noun</i><br>
The senior hand who runs a ship's deck crew: the go-between for the officers who set the course and the
hands who do the work.</p>

<p class="bx-lead">Bosun is that go-between for you and your coding agents. It notices new work before you ask,
hands each task to the right agent and follows it to done, and keeps a reviewed ontology of your team's
people, process and product that every agent starts from (once switched on; `bosun init` recommends it). Nothing goes out under your name until you say yes.</p>

<p class="bx-hero__actions" markdown>[Get started](setup.md){ .md-button .md-button--primary }
[See it work](#how-it-works){ .md-button }</p>

<p class="bx-fine">For macOS, with Claude Code, Codex and Antigravity.</p>

</div>
<div class="bx-install" markdown>

<h2 class="bx-install__title">Set it up on your Mac</h2>

Paste this into a terminal:

```bash title="Terminal"
curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | sh
```

It checks what's missing, asks a few questions, and shows you its plan before it changes anything.
It can add bosun to your menu bar and start it when you log in.

<p class="bx-fine" markdown>Want to read it first? [The script](../get.sh) is short. Already running bosun?
[Update it](setup.md#updating) or [take it off again](setup.md#uninstall).</p>

</div>
</section>

<section class="bx-section" markdown>

## How it works

<p class="bx-lead">A bosun keeps the ship's log and works from the chart. Bosun does the same for your coding
agents: <strong>a ledger</strong> records every piece of work by rules it cannot skip, <strong>an
ontology</strong> charts the people, process and product of your organization, and <strong>every
evening</strong> it learns from what went wrong that day and proposes the fix.</p>

<div class="bx-chart" data-picture="chart" data-log=".bx-voyage">
<svg role="img" aria-labelledby="bx-chart-title bx-chart-desc"><title id="bx-chart-title">The ontology drawn as a sea chart</title><desc id="bx-chart-desc">Three islands: people, process and product. Their landmarks are entities: Dana and Lee, the billing team, the release process and its smoke tests, the #acme-prs channel, the invoice exporter and pdf renderer repositories, and the retired staging 2 environment. Dotted leading lines between them are facts, such as Dana reviews the invoice exporter, each with its source. In the channel, the orchestrator leads three repo sessions, Claude Code, Codex and Antigravity, and takes bearings on the chart before each task. At the chart's edge, a teammate's chart is drawn dashed: sharing it is next, not built yet.</desc></svg>
<div class="bx-chart__tip" hidden></div>
<p class="bx-demo__fallback">This picture needs JavaScript. "The chart in words" below tells the same.</p>
</div>

<p class="bx-chart__hint">Hover or tab to a landmark for its fact and where it came from. Pick a log entry to see
where it sits on the chart.</p>

<div class="bx-voyage">
<div class="bx-log">
<h3>The ship's log</h3>
<p class="bx-log__sub">The work ledger on 20 October: each item typed, each move checked.</p>
<ol id="bx-log">
<li><button type="button" aria-pressed="false" data-at="orchestrator"><time>09:12</time><span class="what"><span class="id">slack:C0000000001/1700000001.000001</span>Priya asks for credit notes in the export</span><span class="st">new</span></button></li>
<li><button type="button" aria-pressed="false" data-at="orchestrator"><time>09:14</time><span class="what"><span class="id">task:2026-10-20/veembc</span>A task, <code>spawned_from</code> her message</span><span class="st">proposed</span></button></li>
<li><button type="button" aria-pressed="false" data-at="orchestrator"><time>09:20</time><span class="what"><span class="id">task:2026-10-20/veembc</span>Accepted on your word: "yes, do the credit notes"</span><span class="st work">accepted</span></button></li>
<li><button type="button" aria-pressed="false" data-at="b-exporter"><time>09:21</time><span class="what"><span class="id">task:2026-10-20/veembc</span>Handed to the acme/exporter session</span><span class="st work">delegated</span></button></li>
<li><button type="button" aria-pressed="false" data-at="person/dana"><time>10:02</time><span class="what"><span class="id">out:task:2026-10-20/veembc/q0kge7</span>Asked Dana: do credit notes reuse the invoice number sequence?</span><span class="st wait">sent</span></button></li>
<li><button type="button" aria-pressed="false" data-at="repo/invoice-exporter"><time>11:05</time><span class="what"><span class="id">mypr:acme/exporter#104</span>The pull request it <code>produces</code></span><span class="st wait">awaiting-review</span></button></li>
<li><button type="button" aria-pressed="false" data-at="process/release"><time>11:06</time><span class="what"><span class="id">jira:EXP-12</span>The ticket it <code>tracks</code> says Done: bosun flags it</span><span class="st">done</span></button></li>
</ol>
<p class="bx-log__foot">A move that matters needs evidence: your words, a link to the reply. Nothing is marked
done on a guess.</p>
</div>
<div class="bx-notice">
<h3>Tonight, for your review</h3>
<div class="bx-notice__item">
<p><strong>A correction.</strong> The exporter session had to stop and ask Dana whether credit notes reuse the
invoice number sequence. Proposed for your policy: "ask the finance owner before reusing a number
sequence."</p>
<p class="bx-notice__ev">Its evidence: the task, and the question to Dana.</p>
<span class="bx-notice__yes">Approve</span>
</div>
<div class="bx-notice__item">
<p><strong>A fact for the chart.</strong> staging 2 is retired: never point new test runs at it.</p>
<p class="bx-notice__ev">From a memory note and a task. Until you confirm it, no agent relies on it.</p>
<span class="bx-notice__yes">Confirm</span> <button type="button" class="bx-notice__at" aria-pressed="false" data-at="environment/staging-2">Show it on the chart</button>
</div>
</div>
</div>

<ul class="bx-points bx-points--two" markdown>
<li markdown>**Every piece of work is in the log.** Asks, tasks, pull requests, tickets and the questions you wait
on are typed items with a state, rules for how they move, and links to where they came from and what came of
them.</li>
<li markdown>**The ontology is people, process and product.** Who owns and reviews what, how a change reaches
production, what was decided and learned. Every fact names its source, and what bosun only inferred waits for
your review.</li>
<li markdown>**Every evening, corrections.** Bosun reads the day's sessions and the ledger, finds a stalled task, a
message sent twice or a missing rule, and proposes the fix with its evidence. You approve what it learns.</li>
<li class="is-next" markdown><span class="bx-next">Next</span>**One ontology for the whole organization.** The
ledger and the ontology are journals that Macs can merge. Sync for one person comes first, then a team board and
a shared ontology: what one team's agents learn, every team's agents start from.</li>
</ul>

??? note "The chart in words"

    - The ontology holds how your organization works, as people (Dana and Lee, who review the invoice
      exporter; the billing team), process (the release, with its smoke tests and the rule that staging comes
      first; the channel review requests go to) and product (the repositories, and staging 2, which is retired).
      Every fact names its source.
    - The orchestrator leads one agent session per repository: acme/exporter in Claude Code, acme/web in Codex,
      acme/infra in Antigravity. Each task starts from what the chart says about it.
    - The ledger logs the day: Priya's message, the task spawned from it, your "yes", the hand-over, the question
      to Dana, the pull request the task produces and the ticket it tracks.
    - Each evening, learning proposes corrections with their evidence, and new facts wait for your review.
    - Next, not built yet: a teammate's chart, merged with yours.

</section>

<section class="bx-section bx-example" markdown>

## What became of Priya's ask? { data-toc-label="The ledger" }

<p class="bx-lead">Every ask, task, pull request and ticket is an item in the ledger. Each one has a
state, rules for how it may move, and links that say where it came from and what came of it.</p>

<div class="bx-example__body" markdown>
<div class="bx-example__story" markdown>

<ol class="bx-steps bx-steps--stacked" markdown>
<li markdown>**Priya asks in Slack:** "could you also export credit notes?"</li>
<li markdown>**Bosun records it as a task,** linked to her message. Only your word accepts work. You said
"go ahead", so it went to the agent working on the exporter.</li>
<li markdown>**The ledger keeps the rest together:** the pull request the task produced, the Jira
ticket that tracks it, and a question still open with Dana.</li>
<li markdown>**Try it.** Pick an item and move it on. Some moves need proof, and a task can't finish
while it waits on someone.</li>
</ol>

</div>
<div class="bx-demo" data-example="ledger">
<p class="bx-demo__fallback">This example needs JavaScript. <a href="ledger.md">The ledger</a> tells the same story.</p>
</div>
</div>

??? note "What else the ledger keeps"

    Links between items say where work came from (`spawned_from`), what it produced (`produces`),
    which ticket tracks it (`tracks`), and what holds it up (`blocked_by`). A request made twice is
    recorded once (`duplicate_of`), and a small ask done inside a bigger one says so (`folded_into`).

    Jira issues and GitHub issues are mirrored read-only. Bosun never moves a ticket itself; it
    tells you where a ticket and the work disagree. Here, EXP-12 says Done while its pull request
    is still waiting for review. [The ledger](ledger.md) has the whole picture.

</section>

<section class="bx-section bx-example" markdown>

## What needs you this morning? { data-toc-label="Your approvals" }

<p class="bx-lead">Anything that would go out under your name waits for you, word for word. The
console lists it, next to what bosun proposes to do.</p>

<div class="bx-example__body" markdown>
<div class="bx-example__story" markdown>

<ol class="bx-steps bx-steps--stacked" markdown>
<li markdown>**Open the console** with `bosun console`, or answer from your phone.</li>
<li markdown>**Approve the exact action.** A message is approved as written, to that person, in that
place. Change a word and it asks again.</li>
<li markdown>**It goes on its way,** and the item's history records what you approved, and when.</li>
</ol>

</div>
<div class="bx-demo" data-example="needs-you">
<p class="bx-demo__fallback">This example needs JavaScript. <a href="permissions.md">Permissions</a> explains approvals.</p>
</div>
</div>

??? note "How approvals stay exact"

    An approval is bound to the exact call: the message, the recipient and the place. The
    orchestrator's guard checks each outward call against what you approved, so an edited message
    or a different thread needs your yes again. Standing approvals for routine classes of action
    are yours to turn on. [Permissions](permissions.md) has the details.

</section>

<section class="bx-section bx-example" markdown>

## How does a change reach production here? { data-toc-label="The ontology" }

<p class="bx-lead">Bosun keeps an ontology of how work gets done in your team: processes, owners,
environments, lessons. Every fact says where it came from, and what bosun only inferred waits for
your review.</p>

<ol class="bx-steps" markdown>
<li markdown>**Ask in your own words:** "how do we release the exporter?"</li>
<li markdown>**It answers from the ontology:** merge, smoke tests, then a gradual rollout by the deploy
bot, and staging always comes first.</li>
<li markdown>**You confirm what it learned.** New facts wait in a short review batch, so agents work
from what you trust. Click a node to walk the ontology.</li>
</ol>

<div class="bx-demo bx-demo--wide" data-example="knowledge">
<p class="bx-demo__fallback">This example needs JavaScript. <a href="knowledge.md">The ontology guide</a> explains it.</p>
</div>

??? note "What it keeps about people"

    Only what helps the work: who reviews what, who owns what, where they prefer to be asked.
    A fact that bosun merely inferred about a person is refused, and you can make bosun forget a
    person entirely. [Ontology](knowledge.md) explains how it grows and stays trustworthy.

</section>

<section class="bx-section bx-example" markdown>

## What happens while you're in meetings? { data-toc-label="A day with bosun" }

<p class="bx-lead">Drag through a day. Bosun does the routine work on its own, and stops at the
moments that need you.</p>

<div class="bx-demo bx-demo--wide" data-example="day">
<p class="bx-demo__fallback">This example needs JavaScript. <a href="day.md">How a day runs</a> has the same day in words.</p>
</div>

</section>

<section class="bx-section" markdown>

## How does it all fit together? { data-toc-label="The whole picture" }

<p class="bx-lead">Here is the same day drawn the way you'd sketch it for a new teammate: what comes in, the
orchestrator that records it, one agent per repository, and the loop that learns from it every evening.</p>

<div class="bx-board" data-picture="whiteboard">
<svg role="img" aria-labelledby="bx-board-title bx-board-desc"><title id="bx-board-title">bosun, sketched on a whiteboard</title><desc id="bx-board-desc">Slack, pull requests, replies and tickets come in to the orchestrator. It records every item in the ledger (1), a stack of typed cards: a task moves from proposed to accepted on your word, to delegated, to done with evidence, and is linked to the mention it came from, the pull request it produces and the ticket that tracks it. It hands tasks to one agent per repository, and they report back. The ontology (2) is a triangle of people, process and product, every fact with its source. Every evening a loop (3) reads the day's sessions and the ledger and proposes fixes with evidence, on a sticky note that says you approve what it learns. Another sticky note says nothing goes out under your name until you approve the exact action. At the bottom, drawn dashed and marked next (4): the same ledger and ontology for a whole organization, not built yet.</desc></svg>
<button type="button" class="bx-board__replay" hidden>Draw it again</button>
<p class="bx-demo__fallback">This picture needs JavaScript. <a href="day.md">How a day runs</a> has the same in words.</p>
</div>

<p class="bx-fine" markdown>The orchestrator and the repo sessions run in tmux, and you can pick a Claude Code
session up from your phone with Remote Control. A watchdog checks every five minutes that all of it is alive, the
console shows the ledger, the sessions and what bosun did for you, and the menu bar app says how bosun is at a
glance. [How a day runs](day.md) has the times.</p>

</section>

<section class="bx-section bx-example" markdown>

## Works natively with macOS { data-toc-label="On your Mac" }

<p class="bx-lead">bosun.app sits in your menu bar from the moment you log in. It says how bosun
is, starts and stops it, updates it, opens the console, and holds its settings.</p>

<div class="bx-example__body" markdown>
<div class="bx-example__story" markdown>

<ul class="bx-points" markdown>
<li markdown>**A glance tells you.** Green while bosun runs, yellow or red when something needs a look,
and grey while it rests outside your hours.</li>
<li markdown>**It starts on its own.** At the start of your hours, or when you log in during them.
Start and Stop are there for any other time.</li>
<li markdown>**Change your hours in Settings.** The days, the start time, when the Mac may sleep, and
whether bosun follows the dated releases or every change.</li>
<li markdown>**Updates come to you.** Check for Updates says what is new, and your agents keep working
while bosun updates.</li>
</ul>

</div>
<div class="bx-demo" data-example="menubar">
<p class="bx-demo__fallback">This example needs JavaScript. <a href="setup.md#the-menu-bar-app">The menu bar app</a> shows every state of its menu.</p>
</div>
</div>

<p class="bx-fine" markdown>Pick a state to see its menu, drawn from what bosun reports. [The menu bar
app](setup.md#the-menu-bar-app) has every state and what each item does.</p>

</section>

<section class="bx-section" markdown>

## Find your guide { data-toc-label="Guides" }

<div class="bx-guides" markdown>
<div class="bx-guide" markdown>

### Set it up

Install bosun, connect your tools, and choose what needs your approval.

- [Setup](setup.md)
- [Permissions](permissions.md)
- [Commands](commands.md)

</div>
<div class="bx-guide" markdown>

### Day to day

What runs when, what the orchestrator does, and how work is tracked.

- [How a day runs](day.md)
- [Orchestrator](orchestrator.md)
- [The ledger](ledger.md)
- [Console](console.md)

</div>
<div class="bx-guide" markdown>

### Under the hood

What bosun learns, how it is built, and what comes next.

- [Ontology](knowledge.md)
- [Learning](learning.md)
- [Roadmap](../ROADMAP.md)
- [Development](development.md)

</div>
</div>

</section>

<section class="bx-section" markdown>

## Where it is today { data-toc-label="Questions" }

<div class="bx-today" markdown>

- **macOS only, for now,** with a menu bar app. Linux is next.
- **Claude Code, Codex and Antigravity** run repo sessions; Antigravity is a preview. The orchestrator needs Claude Code.
- **One engineer, one Mac.** A ledger and ontology shared across a team is next on the
  [roadmap](../ROADMAP.md).
- **The proactive parts are yours to turn on:** events, approvals, learning and the Slack watcher.
  `bosun init` recommends them.

</div>

<ul class="bx-links" markdown>
<li markdown>[Known limits](limits.md)</li>
<li markdown>[Security](../SECURITY.md)</li>
<li markdown>[Report a bug or ask for a feature](https://github.com/promptromp/bosun/issues)</li>
<li markdown>[Contributing](../CONTRIBUTING.md)</li>
</ul>

</section>
