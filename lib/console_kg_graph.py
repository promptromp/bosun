"""The Knowledge view's neighbourhood graph (design: docs/design/console.md, section 6): the entities
within one or two hops of one entity, and a small deterministic radial layout for the page's
inline SVG. Never the whole graph.

Pure, and knows nothing of facts beyond the links it is given (lib/console_knowledge.py builds
them from the knowledge base): a breadth-first walk from the centre in the order of the links (fact
ids), at most MAX_NODES nodes (the rest counted as "more"); the first ring evenly spaced around the
centre, each second-ring node near its parent (alternate ones a little further out), then a few
rounds of pushing apart nodes that sit too close. The same links always give the same picture.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field


WIDTH = 800
HEIGHT = 520
MAX_NODES = 30
HOPS = 2
SECOND_RING = 2
RADII = (0.0, 140.0, 205.0)  # by depth, before the horizontal stretch
STAGGER = 35.0  # every other second-ring node sits this much further out
STRETCH = 1.5  # the picture is wider than tall
SPREAD = 0.85  # the share of its parent's slice the second ring may use
MIN_GAP = 58.0  # node centres closer than this are pushed apart
RELAX_ROUNDS = 60
MARGIN_X = 80.0  # room for labels at the sides
MARGIN_Y = 24.0
LABEL_LIMIT = 22
START_ANGLE = -math.pi / 2  # the first neighbour sits straight above the centre


@dataclass(frozen=True)
class Link:
    """A fact between two entities: its id, ends, predicate and whether it is confirmed."""

    id: str
    subject: str
    object: str
    predicate: str
    confirmed: bool


@dataclass
class Node:
    id: str
    depth: int
    parent: str | None = None
    x: float = 0.0
    y: float = 0.0


@dataclass(frozen=True)
class Edge:
    source: int  # index into the nodes
    target: int
    predicates: tuple[str, ...]
    confirmed: bool  # every fact behind it is confirmed


@dataclass(frozen=True)
class Ego:
    nodes: list[Node]
    edges: list[Edge] = field(default_factory=list)
    more: int = 0  # entities within reach that were left out


def short_label(text: str, limit: int = LABEL_LIMIT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def adjacency(links: Iterable[Link]) -> dict[str, list[str]]:
    """entity -> its neighbours, in link order, each once."""
    found: dict[str, list[str]] = {}
    for link in links:
        for one, other in ((link.subject, link.object), (link.object, link.subject)):
            neighbours = found.setdefault(one, [])
            if other != one and other not in neighbours:
                neighbours.append(other)
    return found


def walk(center: str, links: list[Link], hops: int = HOPS) -> list[Node]:
    """Breadth-first from the centre: every entity within <hops>, nearest first."""
    neighbours = adjacency(links)
    nodes = {center: Node(center, 0)}
    queue = deque([center])
    while queue:
        current = nodes[queue.popleft()]
        if current.depth >= hops:
            continue
        for other in neighbours.get(current.id, []):
            if other not in nodes:
                nodes[other] = Node(other, current.depth + 1, current.id)
                queue.append(other)
    return list(nodes.values())


def edges_between(nodes: list[Node], links: list[Link]) -> list[Edge]:
    """One edge per pair of shown nodes, naming every predicate between them."""
    index = {node.id: i for i, node in enumerate(nodes)}
    pairs: dict[tuple[int, int], list[Link]] = {}
    for link in links:
        if link.subject in index and link.object in index and link.subject != link.object:
            pairs.setdefault((index[link.subject], index[link.object]), []).append(link)
    return [
        Edge(
            a,
            b,
            tuple(dict.fromkeys(link.predicate for link in found)),
            all(link.confirmed for link in found),
        )
        for (a, b), found in pairs.items()
    ]


def angles(nodes: list[Node]) -> dict[str, float]:
    """The first ring evenly around the centre; each parent's children spread over its slice."""
    first = [n for n in nodes if n.depth == 1]
    slice_ = 2 * math.pi / max(1, len(first))
    found = {n.id: START_ANGLE + i * slice_ for i, n in enumerate(first)}
    children: dict[str, list[Node]] = {}
    for node in nodes:
        if node.depth == SECOND_RING and node.parent is not None:
            children.setdefault(node.parent, []).append(node)
    for parent, kids in children.items():
        for j, kid in enumerate(kids):
            found[kid.id] = found.get(parent, START_ANGLE) + slice_ * SPREAD * ((j + 0.5) / len(kids) - 0.5)
    return found


def place(nodes: list[Node]) -> None:
    """Radial positions around the middle of the picture."""
    around = angles(nodes)
    second = 0
    for node in nodes:
        radius = RADII[min(node.depth, len(RADII) - 1)]
        if node.depth >= SECOND_RING:
            radius += STAGGER * (second % 2)
            second += 1
        angle = around.get(node.id, START_ANGLE)
        node.x = WIDTH / 2 + radius * math.cos(angle) * STRETCH
        node.y = HEIGHT / 2 + radius * math.sin(angle)


def separate(a: Node, b: Node, index: int) -> bool:
    """Push two nodes apart if they are too close (the centre never moves); whether they were."""
    dx, dy = b.x - a.x, b.y - a.y
    distance = math.hypot(dx, dy)
    if distance >= MIN_GAP:
        return False
    if distance == 0:  # the same spot: a fixed direction per pair, so the result stays the same
        dx, dy, distance = math.cos(index), math.sin(index), 1.0
    push = MIN_GAP - distance
    ux, uy = dx / distance, dy / distance
    share_a = 0.0 if a.depth == 0 else (1.0 if b.depth == 0 else 0.5)
    a.x, a.y = a.x - ux * push * share_a, a.y - uy * push * share_a
    b.x, b.y = b.x + ux * push * (1 - share_a), b.y + uy * push * (1 - share_a)
    return True


def relax(nodes: list[Node]) -> None:
    for _ in range(RELAX_ROUNDS):
        moved = False
        for i, a in enumerate(nodes):
            for j, b in enumerate(nodes[i + 1 :], start=i + 1):
                moved = separate(a, b, i * len(nodes) + j) or moved
        if not moved:
            return


def fit(nodes: list[Node]) -> None:
    """Keep every node inside the picture (labels need room at the sides), rounded."""
    for node in nodes:
        node.x = round(min(max(node.x, MARGIN_X), WIDTH - MARGIN_X), 1)
        node.y = round(min(max(node.y, MARGIN_Y), HEIGHT - MARGIN_Y), 1)


def ego(center: str, links: list[Link], limit: int = MAX_NODES, hops: int = HOPS) -> Ego:
    """The centre and its neighbourhood (at most <limit> nodes), laid out, with the edges among them."""
    reached = walk(center, links, hops)
    kept = reached[:limit]
    place(kept)
    relax(kept)
    fit(kept)
    return Ego(kept, edges_between(kept, links), len(reached) - len(kept))
