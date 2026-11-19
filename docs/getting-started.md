# Getting started

This page takes you from the install line to a working day with bosun: what to run, what you will see, and how you
work with bosun once it runs. [Setup](setup.md) has every detail of the install, and [How a day runs](day.md) the
times.

## What using bosun looks like

Once bosun runs, you mostly talk to one session. **The orchestrator** is a Claude Code session that bosun keeps open
on your Mac. You tell it what you want in your own words, at your desk or from your phone, and it:

- reads Slack and your pull requests, and records each piece of work in the [ledger](ledger.md);
- hands the work to **a repo session**, an agent that bosun keeps open in each of your repositories;
- follows up with the people you are waiting on;
- asks you before anything goes out under your name: a Slack reply, a merge, a ticket change.

You don't start it each morning, and you don't have to remember to check on it. bosun wakes the Mac at your start
time, starts or resumes every session, and has the orchestrator look at what is new every hour. A watchdog checks
every five minutes that all of it is alive.

## What you need

- **A Mac you leave logged in,** on its power adapter if it is a laptop.
- **[Homebrew](https://brew.sh).**
- **[Claude Code](https://claude.com/claude-code),** logged in and opened once by hand: run `claude`, go through
  its first screens, then `/exit`.
- **The [GitHub CLI](https://cli.github.com),** logged in: `gh auth login`.

You don't have to check these first: the installer lists whatever is missing, with the lines to run.
[Requirements](setup.md#requirements) has the full list, and [A typical setup](setup.md#a-typical-setup) what the
Mac needs so that bosun can wake it on schedule.

## Install

1. **Run the install line** in Terminal:

    ```bash
    curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | sh
    ```

    It puts the newest release of bosun in `~/.local/share/bosun` and checks your Mac. If anything is missing, it
    lists all of it with the lines to run, and stops. Install those, then run the same line again.

2. **Answer its questions.** Most take one key, and Return takes the answer it recommends, which it works out from
   how you already use your agents. The ones worth a moment:

   - **Which repos should get a window?** Each repo you tick gets a repo session.
   - **Which connectors may the orchestrator use?** Asked when Claude Code has some. Nothing is ticked: tick your
     work tools, such as Slack and your tracker. Without Slack, it cannot read your mentions.
   - **When should bosun run?** Your days, your start time, and until when the Mac stays awake.
   - **How proactive should it be?** Take what it recommends: approvals in chat, reactions as things happen, a
     look back every evening, the [ontology](knowledge.md) in the agents' hands and, with Slack's plugin installed,
     a watcher for new Slack mentions.
   - **Add bosun to the menu bar, and start it when you log in?** Yes: its icon shows at a glance how bosun is, and
     bosun starts when you log in during your hours.

   [The questions](setup.md#the-questions) lists them all.

3. **Read the plan, and say yes.** It lists every file it will write and every command it will run before it
   changes anything. If you let it wake the Mac on schedule, it asks for your password once, for that.

4. **Start it now.** The last question is "Start the windows now?". Say yes.

bosun is now running. With the menu bar app, the first line of its menu says "bosun is running", with a green dot.
In a terminal, `bosun status` lists the windows and how each is doing.

## Your first morning

On its first start, the orchestrator runs its morning chores straight away. To watch, open bosun's windows in your
terminal:

```bash
bosun attach
```

They are the windows of a tmux session:

| Window | What it is |
|---|---|
| `orchestrator` | The session you talk to. It starts a fresh conversation each day; what carries over lives in the ledger |
| One per repo, named after it | The repo session working in that repository. It takes tasks from the orchestrator, and you can talk to it too |
| `checkin` | The loop that asks the orchestrator to look at what is new, every hour. Nothing to do here |

To move between them, press Ctrl-b and then the window's number, or Ctrl-b `w` for a list. Ctrl-b `d` leaves, and the
windows run on. Closing the terminal does the same. (Ctrl-b is tmux's own key; yours if you changed it.)

The morning chores end with a report in three parts:

- **Needs your approval:** numbered, each with the exact action, such as the full text of a Slack reply and where it
  would go.
- **Handled automatically:** what went out on its own under a [standing approval](permissions.md#standing-approvals)
  you turned on. Empty until you turn one on.
- **FYI:** new mentions with a line each, and who you are still waiting on.

Answer in your own words: "1 and 3 yes, 2 skip, 4: change the wording to ...". It does exactly what you said yes to,
and nothing it listed goes out without it.

## Talk to the orchestrator

Type into the `orchestrator` window as you would into any Claude Code session. Some things to try:

| You say | What it does |
|---|---|
| "what's waiting on me?" | Answers from the ledger: what needs your yes, what is in flight, who you are waiting on |
| "ask Dana whether we can drop the shared password" | Drafts the message and lists it for your yes. Once it is sent, it watches for her reply and proposes a nudge if none comes |
| "go ahead and add the VAT breakdown in the exporter window" | Accepts the task on your word, and hands it to that repo session with the rules for that kind of task |
| "draft my weekly update for the invoice export work" | Writes it from the ledger, as a draft for you to change or post |
| "who should review a change to the pdf renderer, and where do I ask?" | Looks it up in the ontology: who reviews it and where they like to be asked, citing the facts it used |
| "from now on, CSV exports never include draft invoices" | Records it in the ontology as a standing rule, in your words. Tasks it hands out about CSV exports then carry it |

The last two need the ontology on, which `bosun init` recommends, and the first of them needs who reviews what to be
in it already ([Seeding it](knowledge.md#seeding-it)).

You can talk to a repo session directly too, to steer a task or ask about its code. With events on, what you tell it
about its task is recorded, and the orchestrator checks that before it asks you, so it doesn't ask you again.

## From your phone

Every Claude Code window bosun starts has Remote Control on. In the Claude app on your phone (under Code), or at
claude.ai/code, each window shows up in the list of sessions, under a name that starts with its window's name: open
the orchestrator's and type, as you would at your desk. It is the easiest place to answer a report: read it, say
"1 and 3 yes", and the orchestrator carries on.

For it to work:

- **Claude Code is logged in with a Claude subscription.** With an API key, it has no Remote Control. On a Team or
  Enterprise plan, an Owner has to turn Remote Control on first.
- **The Mac is awake.** Through your hours bosun keeps it awake. After your "awake until" time it may sleep, and its
  sessions are out of reach until it wakes.
- **The window runs Claude Code.** Only Claude Code sessions show up in the Claude app; Codex and Antigravity
  windows do not.

To hear about trouble when you are away from the Mac, set `BOSUN_ALERT_SLACK_USER` in `bosun.env` to your Slack
member id: every alert inside your hours then also comes as a Slack DM to yourself ([Watchdog](watchdog.md)).

## What wakes it

You don't have to prompt the orchestrator for it to work:

| When | What happens |
|---|---|
| At your start time, on your days | bosun wakes the Mac (with the wake on), checks your logins, starts or resumes every session, and the orchestrator runs its morning chores |
| When you log in during your hours | With start at login on, bosun starts, unless it is running already, you stopped it earlier that day, or you stopped it with the morning start paused |
| Every hour | A check-in: the orchestrator looks at what is new, when something is |
| Within minutes | With events on, a repo session's report reaches the orchestrator once it is idle, without waiting for the check-in. With the Slack watcher on too, new Slack mentions are looked for every 15 minutes |
| Every five minutes | The watchdog checks that everything is alive, and alerts you when a window is down or has waited at a question too long |
| In the evening | With learning on, learning looks back over the day and proposes fixes, which the next report lists for your yes |
| At your "awake until" time | bosun stops keeping the Mac awake, and the Mac may sleep |

## Keep an eye on it

- **The menu bar app:** a small dot on its icon when something needs a look (yellow or red) or an update is ready
  (blue). The menu's first line says how bosun is, with a coloured dot: green while it runs, grey while it rests
  outside your hours or after you stopped it, yellow or red with a warning or an error, which Show What's Wrong
  lists. The menu also has Open Console, Settings and Check for Updates.
- **The console:** Open Console in the menu, or `bosun console --open`. A page in your browser with the ledger, the
  windows, what needs you, what learning proposed, and the ontology.
- **`bosun status`:** how bosun is, in the terminal.
- **`bosun doctor`:** checks everything bosun depends on, and says how to fix what is not right.

## Stop and start

| You want to | In a terminal | In the menu bar app |
|---|---|---|
| Stop for the rest of today, and let the Mac sleep | `bosun stop` | |
| Stop until you start it again | `bosun quit --stop-now --pause-morning` | Stop bosun... |
| Start it now, at any hour | `bosun start` | Start bosun |

The menu's Stop asks about windows still at work. Its "Pause the morning start" box, on by default, keeps bosun
stopped until you start it again; untick it, or leave out `--pause-morning`, and tomorrow's morning start brings it
back. `--let-finish` instead of `--stop-now` waits for busy windows first. Quit bosun.app asks first, and its default,
Leave bosun Running, only closes the icon. [Stopping and starting again](day.md#stopping-and-starting-again) has
every option.

## If something is wrong

Run `bosun doctor`. [If you get stuck](setup.md#if-you-get-stuck) lists the usual problems; most are a login, or a
first-run screen that nobody answered. When an alert says a window is waiting at a screen, `bosun attach <window>`
takes you there.

## What next

- [Configuration](configuration.md): what each window may reach, models, and repo sessions on Codex or Antigravity.
- [Permissions](permissions.md): what needs your yes, and standing approvals for routine actions.
- [Orchestrator](orchestrator.md): its chores step by step, and how to steer it.
- [The ledger](ledger.md) and [Ontology](knowledge.md): what bosun records, and how.
- [The menu bar app](menu-bar-app.md): every item of its menu.
