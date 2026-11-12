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

# Agents write the code. Bosun runs the rest of the day. { .bx-hero__title }

<p class="bx-lead">Bosun hands each task to the right coding agent, follows up on reviews and replies,
and keeps a record of everything in flight. Nothing goes out under your name until you say yes.</p>

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

<p class="bx-fine" markdown>Want to read it first? [The script](../get.sh) is short. Already running bosun?
[Update it](setup.md#updating) or [take it off again](setup.md#uninstall).</p>

</div>
</section>

<section class="bx-section" markdown>

## How it works

<p class="bx-lead">Bosun runs on your Mac: one agent session for each repository, and an orchestrator
that watches Slack, your pull requests and your tickets. Every piece of work becomes an item in a
ledger, so nothing gets lost between days, agents or people.</p>

<ol class="bx-steps" markdown>
<li markdown>**Something comes in.** A mention, a review request, a reply you were waiting for.</li>
<li markdown>**The right agent takes it.** Bosun records the task, hands it to the agent working in
that repository, and checks in every hour.</li>
<li markdown>**You approve what goes out.** A Slack reply, a merge, a ticket change: one line to approve,
from your phone if you like.</li>
</ol>

??? note "See the whole picture"

    ```mermaid
    flowchart LR
      watchers["Watchers<br/>Slack, reviews, replies"] -->|events| orch
      you(("You<br/>phone or terminal")) <-->|"asks and approvals"| orch["Orchestrator"]
      orch <--> ledger[("Work ledger")]
      orch <--> kg[("Knowledge graph")]
      orch -->|tasks| repos["Repo sessions<br/>Claude Code, Codex or Antigravity"]
      repos -->|reports| ledger
      orch -->|"approved actions only"| world["Slack, GitHub, tracker"]
      ledger -.-> learn["Daily learning"]
      repos -.-> learn
      learn -.->|"proposed fixes"| you
    ```

    The orchestrator and the repo sessions run in tmux. Claude Code sessions can be picked up from
    your phone with Remote Control. A watchdog checks every five minutes that all of it is alive, and
    a local console shows the ledger, the sessions and what bosun did for you.
    [How a day runs](day.md) has the times.

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

## How does a change reach production here? { data-toc-label="What it knows" }

<p class="bx-lead">Bosun keeps a map of how work gets done in your team: processes, owners,
environments, lessons. Every fact says where it came from, and what bosun only inferred waits for
your review.</p>

<ol class="bx-steps" markdown>
<li markdown>**Ask in your own words:** "how do we release the exporter?"</li>
<li markdown>**It answers from the map:** merge, smoke tests, then a gradual rollout by the deploy
bot, and staging always comes first.</li>
<li markdown>**You confirm what it learned.** New facts wait in a short review batch, so agents work
from what you trust. Click a node to walk the map.</li>
</ol>

<div class="bx-demo bx-demo--wide" data-example="knowledge">
<p class="bx-demo__fallback">This example needs JavaScript. <a href="knowledge.md">Knowledge</a> explains the map.</p>
</div>

??? note "What it keeps about people"

    Only what helps the work: who reviews what, who owns what, where they prefer to be asked.
    A fact that bosun merely inferred about a person is refused, and you can make bosun forget a
    person entirely. [Knowledge](knowledge.md) explains how the map grows and stays trustworthy.

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

- [Knowledge](knowledge.md)
- [Learning](learning.md)
- [Roadmap](../ROADMAP.md)
- [Development](development.md)

</div>
</div>

</section>

<section class="bx-section" markdown>

## Where it is today { data-toc-label="Questions" }

<div class="bx-today" markdown>

- **macOS only, for now.** Linux is next.
- **Claude Code, Codex and Antigravity** run repo sessions; Antigravity is a preview. The orchestrator needs Claude Code.
- **One engineer, one Mac.** A ledger and knowledge base shared across a team is next on the
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
