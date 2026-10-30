# Prototype: four treatments of the Knowledge Graph page (2026-10-21)

A throwaway, self-contained prototype made to choose how the console's Knowledge Graph page should
look. It is kept for reference; the console does not load it. Every entity, fact, channel and
ticket in it is invented (the acme world of the rest of the repo).

## What it shows

Four treatments of the same invented knowledge base (75 entities, about 200 facts over a week),
switched with the tabs at the top (or `#A` to `#D` in the address), each in light and dark and with
a "Reduce motion" toggle:

- **A. Constellation:** every entity on a canvas, clustered by axis with a small hand-written force
  layout; hover highlights a node's neighbours, a click opens its facts in a drawer, search flies to
  an entity, axis chips hide clusters, and an "as of" slider (with replay) grows the graph day by
  day. Proposed links are dashed, proposed-only entities translucent.
- **B. Metro map + atlas:** the processes as metro lines through shared stations (steps, gates,
  systems, people), gates as interchange marks, a hotfix express track; beside it People as bubbles
  by team and Product as a treemap.
- **C. Briefing:** a magazine-style home: a headline (facts per day, rediscovery saved, coverage
  rings), "this week we learned" as a timeline with decisions shown before -> after, today's review
  as a stack of cards (drag, or Y / S / N, Z to undo, with stamps and progress) and the most
  connected entities with mini ego graphs.
- **D. Radial explorer:** axes, entity types and entities as a sunburst you zoom into, with
  breadcrumbs and a facts drawer.

## The decision (2026-10-21)

"All four are spectacular; keep the prototype for later reference. C (Briefing) is the mainstay
treatment. Keep A (Constellation) and B (Metro) as alternative visualizations the user can cycle
through. B is incredible but needs a lot of work on realistic data: roadmap item to prototype it
more."

So the console's Knowledge Graph page (docs/design/console.md, section 6) has the tabs Briefing
(the default, C on real data), Constellation (A on real data) and Raw data (the overview and entity
browser), and a disabled "Metro (prototype)" tab: B's metro layout is hand-tuned here
(`src/4-b-metro.js` places every station on a grid by hand), and laying out real processes
automatically is the open problem (ROADMAP.md, "Knowledge Graph: Metro map treatment"). D was not
taken forward.

What the real Briefing does differently: the "rediscovery saved" number here is invented (`rw` on
each fact); the console shows a placeholder until the learning run counts rework episodes. Coverage
here is "entities with 3+ facts"; the console's is "entities in at least one confirmed fact".

## How to open it

Open `index.html` in a browser (a file URL is fine; it loads nothing from the network: the links
in its research summary only open when clicked). It is one HTML file built from `src/`:

- `src/shell.html`: the page, its CSS (light and dark tokens) and the `{{SCRIPT}}` slot;
- `src/1-data.js`: the invented knowledge base; `src/2-core.js`: shared helpers (theme, drawer,
  tooltips, shapes);
- `src/3-a-constellation.js`, `src/4-b-metro.js`, `src/5-c-briefing.js`, `src/6-d-radial.js`: one
  file per treatment.

After editing `src/`, rebuild with `python3 build.py` (it inlines the scripts in order, after a
small error collector that records uncaught errors on `<html data-errors="...">`).

Unlike the console, the prototype builds markup with template strings and `innerHTML` and sets
inline styles: it only ever shows its own invented data, and none of it is served by the console,
whose rules (no HTML parsing of data, no style attributes, strict CSP) it does not follow.

## Test hook

`KG.treatments.A._screenPos(id)` returns a node's position on screen, so a headless browser can
hover and click nodes. It was used by a small Chrome DevTools Protocol script (not kept: it drove a
local Chrome with hover, search, slider, keyboard review and drag steps and took screenshots) and
changes nothing when unused.
