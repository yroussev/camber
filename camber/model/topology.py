"""Served-by topology: a directed graph over equipment ids (who feeds/serves whom).

``model.entities`` says *what* equipment exists and what points roll onto it; this module says
how equipment is **connected** — a plant serves an air handler serves a zone. An edge ``(a, b)``
means "``a`` serves / feeds ``b``" (upstream → downstream). The layers (plant / AHU / zone) are
**not** hard-coded: they emerge from edge direction — roots are the most-upstream sources, leaves
the terminals — so the same structure models plant→AHU→zone, AHU→zone→reheat, or any depth.

The point of having it: fleet analytics that today pool every zone building-wide (e.g. the
rogue-zone census) can instead scope per air handler once they know which zones an AHU serves.
This module is deliberately **id-only** (bare equipment-id strings, no :class:`Equip` dependency, so
no import cycle with ``entities``); class-aware questions ("the *AHU* above this zone") are
expressed by passing a predicate, never by baking equipment classes in here.

Honesty is built into the type: a partial graph simply has no edges for the ids it doesn't know
(queries return empty, so grouping *degrades* rather than guessing), a cyclic input is broken into a
best-effort DAG with the removed edges recorded in ``dropped_cycle_edges`` (never a hang or crash),
and ``provenance`` records whether the graph came from an authoritative source or a heuristic guess
so downstream can caveat accordingly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Topology:
    """A directed served-by graph over equipment ids; edge ``(parent, child)`` = parent feeds child.

    Construct via :meth:`from_edges` / :meth:`from_parent_map` (or directly). Input is normalized on
    construction: duplicate edges and self-loops are dropped, and any edges that would form a cycle
    are removed to leave a DAG (the removed edges are kept in ``dropped_cycle_edges`` for honesty).
    All queries are cycle-safe and return empty results for unknown ids, so a partial graph degrades
    to "no grouping" rather than a wrong one.
    """

    edges: tuple = ()  # tuple[tuple[str, str]] — accepted DAG edges (parent, child)
    provenance: str = "explicit"  # "explicit" | "semantic" | "heuristic"
    dropped_cycle_edges: tuple = field(default=())  # edges removed to keep the graph acyclic
    # precomputed adjacency — derived in __post_init__, excluded from init/eq/repr (private):
    _children: dict = field(default_factory=dict, init=False, compare=False, repr=False)
    _parents: dict = field(default_factory=dict, init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        # normalize: coerce to (str, str), drop self-loops and duplicates, keep a stable order
        seen: set = set()
        norm: list = []
        for e in self.edges:
            a, b = str(e[0]), str(e[1])
            if a == b or (a, b) in seen:
                continue
            seen.add((a, b))
            norm.append((a, b))
        # cycle guard: DFS in deterministic order, drop back-edges into a DAG
        children: dict = {}
        for a, b in norm:
            children.setdefault(a, []).append(b)
        nodes = sorted({n for e in norm for n in e})
        color: dict = {n: 0 for n in nodes}  # 0 white, 1 gray (on stack), 2 black
        kept: set = set()
        dropped: list = []

        def _visit(u: str) -> None:
            color[u] = 1
            for v in sorted(children.get(u, ())):
                if color.get(v, 0) == 1:
                    dropped.append((u, v))  # back-edge -> would close a cycle
                    continue
                kept.add((u, v))
                if color.get(v, 0) == 0:
                    _visit(v)
            color[u] = 2

        for n in nodes:
            if color[n] == 0:
                _visit(n)

        dag = tuple(e for e in norm if e in kept)
        ch: dict = {}
        pa: dict = {}
        for a, b in dag:
            ch.setdefault(a, []).append(b)
            pa.setdefault(b, []).append(a)
        object.__setattr__(self, "edges", dag)
        object.__setattr__(self, "dropped_cycle_edges", tuple(dropped))
        object.__setattr__(self, "_children", {k: tuple(v) for k, v in ch.items()})
        object.__setattr__(self, "_parents", {k: tuple(v) for k, v in pa.items()})

    # ---- constructors ----

    @classmethod
    def from_edges(cls, edges: Iterable, *, provenance: str = "explicit") -> Topology:
        """Build from ``(parent, child)`` served-by pairs."""
        return cls(edges=tuple((str(a), str(b)) for a, b in edges), provenance=provenance)

    @classmethod
    def from_parent_map(cls, child_to_parent: dict, *, provenance: str = "explicit") -> Topology:
        """Build from a ``{child: parent}`` map (e.g. ``{zone: ahu}`` and/or ``{ahu: plant}``)."""
        return cls.from_edges(
            ((parent, child) for child, parent in child_to_parent.items()), provenance=provenance
        )

    @classmethod
    def from_site(cls, site) -> Topology:
        """The topology carried by a :class:`camber.model.entities.Site`, or the empty graph."""
        return getattr(site, "topology", None) or cls()

    # ---- direct-neighbour queries ----

    def children_of(self, equip_id: str) -> tuple:
        """Ids this equipment directly serves; ``()`` if unknown or a leaf."""
        return self._children.get(equip_id, ())

    def parents_of(self, equip_id: str) -> tuple:
        """Ids that directly serve this equipment; ``()`` if unknown or a root."""
        return self._parents.get(equip_id, ())

    # ---- transitive queries (all cycle-safe: the graph is already a DAG) ----

    def descendants(self, equip_id: str) -> frozenset:
        """Everything served transitively below ``equip_id``."""
        out: set = set()
        stack = list(self.children_of(equip_id))
        while stack:
            n = stack.pop()
            if n in out:
                continue
            out.add(n)
            stack.extend(self.children_of(n))
        return frozenset(out)

    def ancestors(self, equip_id: str) -> frozenset:
        """Everything that serves ``equip_id`` transitively upstream."""
        out: set = set()
        stack = list(self.parents_of(equip_id))
        while stack:
            n = stack.pop()
            if n in out:
                continue
            out.add(n)
            stack.extend(self.parents_of(n))
        return frozenset(out)

    def roots(self) -> tuple:
        """Ids with no parent (the most-upstream sources — plants), sorted."""
        return tuple(sorted(n for n in self._nodes() if n not in self._parents))

    def leaves(self) -> tuple:
        """Ids with no child (the terminals — zones), sorted."""
        return tuple(sorted(n for n in self._nodes() if n not in self._children))

    def _nodes(self) -> frozenset:
        return frozenset(n for e in self.edges for n in e)

    # ---- grouping (the fleet-analytics adapter) ----

    def nearest_ancestor(self, equip_id: str, pred: Callable) -> str | None:
        """The closest upstream id satisfying ``pred`` (breadth-first), or ``None``.

        ``pred`` takes an equipment id; class-aware callers pass a predicate that knows classes
        (e.g. ``lambda eid: site.equip(eid).equip_class == "AHU"``) — the graph stays class-free.
        """
        frontier = list(self.parents_of(equip_id))
        visited: set = set()
        while frontier:
            nxt: list = []
            for n in sorted(frontier):
                if n in visited:
                    continue
                visited.add(n)
                if pred(n):
                    return n
                nxt.extend(self.parents_of(n))
            frontier = nxt
        return None

    def zones_of(self, equip_id: str) -> tuple:
        """The terminal (leaf) descendants served by ``equip_id``, sorted — e.g. an AHU's zones."""
        leaves = set(self.leaves())
        return tuple(sorted(d for d in self.descendants(equip_id) if d in leaves))

    def group_of(self, equip_id: str, *, pred: Callable | None = None) -> str | None:
        """The grouping key for one terminal: its nearest ancestor matching ``pred`` (default: its
        single direct parent), or ``None`` when there is no unambiguous group."""
        if pred is not None:
            return self.nearest_ancestor(equip_id, pred)
        parents = self.parents_of(equip_id)
        return parents[0] if len(parents) == 1 else None

    def group_map(self, equip_ids: Iterable, *, pred: Callable | None = None) -> dict:
        """``{equip_id: group}`` for the given ids, **omitting** any id with no resolvable group.

        Directly consumable as the ``groups`` argument of a grouping-aware fleet analytic. Omitting
        ungrouped ids lets the caller measure coverage and keep the honest building-wide fallback
        for the remainder rather than inventing edges.
        """
        out: dict = {}
        for eid in equip_ids:
            g = self.group_of(eid, pred=pred)
            if g is not None:
                out[eid] = g
        return out


__all__ = ["Topology"]
