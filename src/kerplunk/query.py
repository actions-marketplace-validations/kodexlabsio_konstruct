"""Questions over a built graph: impact, trace, path, search, describe and free-text query.

Each function returns a typed result. ``str(result)`` is compact text written for coding agents
and people; ``result.to_dict()`` is the same content as plain data, for JSON and MCP.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

from kerplunk.graph import RELATION_KINDS, Graph, Node, Step


class QueryError(Exception):
    """A reference that matches nothing or several things, or a missing graph."""


def load_graph(out_dir: Path) -> Graph:
    path = out_dir / "graph.json"
    if not path.is_file():
        raise QueryError(f"no graph at {path}; run `kerplunk build` first")
    return Graph.read(path)


def resolve_one(graph: Graph, ref: str) -> Node:
    found = graph.resolve(ref)
    if not found:
        hints = [graph.label(n) for n in search_nodes(graph, ref, limit=5)]
        suffix = f"; did you mean {', '.join(hints)}?" if hints else ""
        raise QueryError(f"nothing in the graph matches {ref!r}{suffix}")
    if len(found) > 1:
        options = ", ".join(f"{n.id}" for n in found[:10])
        raise QueryError(f"{ref!r} is ambiguous; use one of: {options}")
    return found[0]


def _drop_empty(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


@dataclass(frozen=True)
class Item:
    """One node in a result, with how it was reached when it came from a walk."""

    id: str
    kind: str
    label: str
    name: str
    relation_kind: str | None = None
    path: str | None = None
    data_type: str | None = None
    description: str | None = None
    depth: int | None = None
    via: str | None = None
    transform: str | None = None
    role: str | None = None
    command: str | None = None
    through: str | None = None
    direction: str | None = None
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_empty({f.name: getattr(self, f.name) for f in fields(self)})

    def __str__(self) -> str:
        meta = [self.relation_kind or self.kind]
        meta += [str(v) for v in (self.data_type, self.transform, self.role, self.command) if v]
        if self.through:
            meta.append(f"via {self.through}")
        parts = [self.label, f"[{', '.join(meta)}]"]
        if self.path:
            parts.append(self.path)
        if self.depth is not None:
            parts.insert(0, f"{self.depth:>2}")
        return "  ".join(parts)


def item(graph: Graph, node: Node, step: Step | None = None, **extra: Any) -> Item:
    data: dict[str, Any] = {"id": node.id, "kind": node.kind, "label": graph.label(node), "name": node.name}
    if node.kind == "column":
        parent = graph.parent_of(node)
        if parent is not None:
            data["relation_kind"] = parent.kind
            data["path"] = parent.path
    else:
        data["path"] = node.path
    data["data_type"] = node.data_type
    data["description"] = node.description
    if step is not None:
        data["depth"] = step.depth
        if step.via is not None:
            data["via"] = step.via.kind
            for key in ("transform", "role", "command"):
                data[key] = step.via.attrs.get(key)
            through = step.via.attrs.get("via")
            if through:
                data["through"] = graph.label(graph.nodes[through]) if through in graph.nodes else through
    data.update(extra)
    return Item(**data)


def _shorten(text: str, width: int = 140) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 3] + "..."


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


# Impact and trace


@dataclass
class Walk:
    """Everything downstream (impact) or upstream (trace) of one node, grouped by what it is."""

    direction: Literal["downstream", "upstream"]
    target: Item
    columns: list[Item] = field(default_factory=list)
    relations: list[Item] = field(default_factory=list)
    joins_and_filters: list[Item] = field(default_factory=list)
    filtered_by: list[Item] = field(default_factory=list)
    tests: list[Item] = field(default_factory=list)
    exposures: list[Item] = field(default_factory=list)
    tasks: list[Item] = field(default_factory=list)

    SECTIONS = (
        ("columns", "Columns"),
        ("relations", "Relations"),
        ("joins_and_filters", "Relations that join, filter or group on it"),
        ("filtered_by", "Columns that filter or join its relation"),
        ("tests", "Tests"),
        ("exposures", "Exposures"),
        ("tasks", "Orchestration"),
    )

    @property
    def total(self) -> int:
        return sum(len(getattr(self, key)) for key, _ in self.SECTIONS)

    def summary(self) -> str:
        counts = [
            _plural(count, noun)
            for count, noun in (
                (len(self.columns), "column"),
                (len(self.relations) + len(self.joins_and_filters), "relation"),
                (len(self.tests), "test"),
                (len(self.exposures), "exposure"),
                (len(self.tasks), "task"),
            )
            if count
        ]
        where = "downstream" if self.direction == "downstream" else "upstream"
        if not counts:
            return f"Nothing {where}."
        listed = counts[0] if len(counts) == 1 else f"{', '.join(counts[:-1])} and {counts[-1]}"
        return f"{listed} {where}."

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"direction": self.direction, "target": self.target.to_dict()}
        for key, _ in self.SECTIONS:
            data[key] = [i.to_dict() for i in getattr(self, key)]
        data["total"] = self.total
        data["summary"] = self.summary()
        return data

    def __str__(self) -> str:
        title = "Downstream of" if self.direction == "downstream" else "Upstream of"
        lines = [f"{title} {self.target.label} ({self.target.relation_kind or self.target.kind})"]
        if self.target.description:
            lines.append(f"  {_shorten(self.target.description)}")
        lines.append(self.summary())
        for key, heading in self.SECTIONS:
            items: list[Item] = getattr(self, key)
            if items:
                lines += ["", f"{heading} ({len(items)})", *(f"  {i}" for i in items)]
        return "\n".join(lines)


def _walk(graph: Graph, start: Node, steps: list[Step], direction: Literal["downstream", "upstream"]) -> Walk:
    start_ids = set(graph.expand(start))
    walk = Walk(direction=direction, target=item(graph, start))
    relation_ids: set[str] = set()
    listed: set[str] = set()
    for step in sorted(steps, key=lambda s: (s.depth, s.node.id)):
        node = step.node
        if node.id in start_ids:
            continue
        entry = item(graph, node, step)
        if node.kind == "column":
            walk.columns.append(entry)
            if node.parent and node.parent not in start_ids:
                relation_ids.add(node.parent)
        elif node.kind == "test":
            walk.tests.append(entry)
        elif node.kind == "exposure":
            walk.exposures.append(entry)
        elif node.kind in ("task", "dag"):
            walk.tasks.append(entry)
        elif step.via is not None and step.via.attrs.get("role") == "reference":
            walk.joins_and_filters.append(entry)
            listed.add(node.id)
        else:
            walk.relations.append(entry)
            listed.add(node.id)
    for relation_id in sorted(relation_ids - listed):
        walk.relations.append(item(graph, graph.nodes[relation_id]))
    return walk


def impact(graph: Graph, ref: str, max_depth: int | None = None) -> Walk:
    """Everything downstream of a relation or column."""
    node = resolve_one(graph, ref)
    return _walk(graph, node, graph.impact(node, max_depth), "downstream")


def trace(graph: Graph, ref: str, max_depth: int | None = None) -> Walk:
    """Everything upstream of a relation or column."""
    node = resolve_one(graph, ref)
    walk = _walk(graph, node, graph.trace(node, max_depth), "upstream")
    if node.kind == "column" and node.parent:
        walk.filtered_by = [
            item(graph, graph.nodes[e.source])
            for e in graph.in_edges(node.parent, "feeds")
            if e.attrs.get("role") == "reference" and e.source in graph.nodes
        ]
    return walk


# Path


@dataclass
class PathResult:
    start: Item
    end: Item
    steps: list[Item]

    @property
    def found(self) -> bool:
        return bool(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.start.to_dict(),
            "to": self.end.to_dict(),
            "found": self.found,
            "steps": [s.to_dict() for s in self.steps],
        }

    def __str__(self) -> str:
        if not self.found:
            return f"No path between {self.start.label} and {self.end.label}."
        lines = [
            f"Path from {self.start.label} to {self.end.label} ({_plural(len(self.steps) - 1, 'step')}):"
        ]
        for step in self.steps:
            arrow = ""
            if step.via:
                arrow = f"-{step.via}-> " if step.direction == "forward" else f"<-{step.via}- "
            lines.append(f"  {arrow}{step.label} ({step.relation_kind or step.kind})")
        return "\n".join(lines)


def path(graph: Graph, start_ref: str, end_ref: str) -> PathResult:
    """The shortest connection between two nodes, following data flow where possible."""
    start = resolve_one(graph, start_ref)
    end = resolve_one(graph, end_ref)
    steps = []
    for step in graph.path(start, end):
        direction = None
        if step.via is not None:
            direction = "forward" if step.via.source == step.previous else "backward"
        steps.append(item(graph, step.node, step, direction=direction))
    return PathResult(item(graph, start), item(graph, end), steps)


# Search


_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by do does for from how i in is it of on or the to what which who why with "
    "where when this that these those me my we our there their any all if can could would should will "
    "show tell find list give get about into used use uses using".split()
)


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


def _variants(token: str) -> set[str]:
    forms = {token}
    if len(token) > 3 and token.endswith("s"):
        forms.add(token[:-1])
    if len(token) > 4 and token.endswith("es"):
        forms.add(token[:-2])
    return forms


def search_nodes(graph: Graph, text: str, limit: int = 20, kinds: list[str] | None = None) -> list[Node]:
    return [node for node, _ in _scored(graph, text, limit, kinds)]


def _scored(graph: Graph, text: str, limit: int, kinds: list[str] | None) -> list[tuple[Node, float]]:
    tokens = _tokens(text)
    if not tokens:
        return []
    needle = text.strip().lower()
    total = max(len(graph.nodes), 1)
    frequency: Counter[str] = Counter()
    names: dict[str, set[str]] = {}
    for node in graph.nodes.values():
        words = set(_TOKEN.findall(f"{graph.label(node)} {node.relation or ''}".lower()))
        names[node.id] = words
        frequency.update(words)
    scored: list[tuple[Node, float]] = []
    for node in graph.nodes.values():
        if kinds and node.kind not in kinds:
            continue
        label = graph.label(node).lower()
        name_words = names[node.id]
        description = set(_TOKEN.findall((node.description or "").lower()))
        score = 0.0
        for token in tokens:
            forms = _variants(token)
            weight = math.log(1 + total / (1 + max(frequency[f] for f in forms)))
            if forms & name_words:
                score += 3 * weight
            elif any(f in label for f in forms):
                score += 1.5 * weight
            if forms & description:
                score += 0.5 * weight
        if not score:
            continue
        if label == needle or node.name.lower() == needle:
            score += 10
        if node.kind in RELATION_KINDS:
            score *= 1.2
        elif node.kind == "test":
            score *= 0.5
        scored.append((node, score))
    scored.sort(key=lambda pair: (-pair[1], len(pair[0].id), pair[0].id))
    return scored[:limit]


@dataclass
class SearchResult:
    query: str
    results: list[Item]

    def to_dict(self) -> dict[str, Any]:
        return {"query": self.query, "results": [r.to_dict() for r in self.results]}

    def __str__(self) -> str:
        if not self.results:
            return f"Nothing matches {self.query!r}."
        lines = []
        for result in self.results:
            lines.append(str(result))
            if result.description:
                lines.append(f"      {_shorten(result.description)}")
        return "\n".join(lines)


def search(graph: Graph, text: str, limit: int = 20, kinds: list[str] | None = None) -> SearchResult:
    """Nodes whose names or descriptions match the words in ``text``, best first."""
    return SearchResult(
        text, [item(graph, n, score=round(s, 2)) for n, s in _scored(graph, text, limit, kinds)]
    )


# Describe


@dataclass
class Description:
    node: Item
    relation: str | None = None
    parent: str | None = None
    origins: list[str] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    columns: list[Item] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    upstream: list[str] = field(default_factory=list)
    downstream: list[str] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)
    runs: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    before: list[str] = field(default_factory=list)

    LISTS = (
        ("tests", "Tests"),
        ("upstream", "Upstream"),
        ("downstream", "Downstream"),
        ("tasks", "Tasks"),
        ("runs", "Runs"),
        ("after", "After"),
        ("before", "Before"),
    )
    HIDDEN_ATTRS = frozenset({"fqn", "dbt_name", "checksum", "view_sql"})

    def to_dict(self) -> dict[str, Any]:
        data = self.node.to_dict()
        data.update(_drop_empty({"relation": self.relation, "parent": self.parent}))
        data["origins"] = self.origins
        data["attrs"] = self.attrs
        if self.node.kind in RELATION_KINDS:
            data["columns"] = [c.to_dict() for c in self.columns]
        for key, _ in self.LISTS:
            data[key] = getattr(self, key)
        return data

    def __str__(self) -> str:
        node = self.node
        lines = [f"{node.label} ({node.relation_kind or node.kind})", f"  id: {node.id}"]
        for key, value in (
            ("relation", self.relation),
            ("path", node.path),
            ("data_type", node.data_type),
            ("description", node.description),
        ):
            if value:
                lines.append(f"  {key}: {value}")
        if self.origins:
            lines.append(f"  from: {', '.join(self.origins)}")
        for key, value in self.attrs.items():
            if key not in self.HIDDEN_ATTRS and value not in (None, "", [], {}):
                lines.append(f"  {key.replace('_', ' ')}: {value}")
        if self.columns:
            lines += ["", f"Columns ({len(self.columns)})"]
            width = max(len(c.name) for c in self.columns)
            for column in self.columns:
                text = f"  {column.name.ljust(width)}  {column.data_type or ''}".rstrip()
                if column.description:
                    text += f"  {_shorten(column.description, 100)}"
                lines.append(text)
        for key, heading in self.LISTS:
            values: list[str] = getattr(self, key)
            if values:
                lines += ["", f"{heading} ({len(values)})", *(f"  {v}" for v in values)]
        return "\n".join(lines)


def _neighbour(graph: Graph, node_id: str) -> str:
    node = graph.nodes[node_id]
    return f"{graph.label(node)} ({node.kind})"


def describe(graph: Graph, ref: str) -> Description:
    """One node in detail: columns, tests, neighbours and, for tasks, what they run."""
    node = resolve_one(graph, ref)
    result = Description(
        node=item(graph, node),
        relation=node.relation,
        parent=node.parent,
        origins=sorted(node.origins),
        attrs=dict(node.attrs),
    )
    if node.kind in RELATION_KINDS:
        result.columns = [item(graph, c) for c in graph.columns_of(node.id)]
    checked = [node.id, *(c.id for c in graph.columns_of(node.id))]
    result.tests = sorted({graph.nodes[e.source].name for t in checked for e in graph.in_edges(t, "tests")})
    result.upstream = sorted(
        {_neighbour(graph, o) for _, o in graph.upstream_edges(node.id) if graph.nodes[o].kind != "dag"}
    )
    result.downstream = sorted(
        {_neighbour(graph, o) for _, o in graph.downstream_edges(node.id) if graph.nodes[o].kind != "dag"}
    )
    if node.kind == "dag":
        result.tasks = [graph.nodes[e.target].name for e in graph.out_edges(node.id, "runs")]
    if node.kind == "task":
        result.runs = sorted(graph.label(graph.nodes[e.target]) for e in graph.out_edges(node.id, "runs"))
        result.after = sorted(graph.nodes[e.source].name for e in graph.in_edges(node.id, "precedes"))
        result.before = sorted(graph.nodes[e.target].name for e in graph.out_edges(node.id, "precedes"))
    return result


# List


@dataclass
class Listing:
    kinds: list[str]
    items: list[Item]

    def to_dict(self) -> dict[str, Any]:
        return {"kinds": self.kinds, "items": [i.to_dict() for i in self.items]}

    def __str__(self) -> str:
        if not self.items:
            return f"No {' or '.join(self.kinds)} nodes."
        width = max(len(i.label) for i in self.items)
        lines = []
        for entry in self.items:
            line = f"{entry.label.ljust(width)}  {entry.kind:<9} {entry.path or ''}".rstrip()
            lines.append(line)
        return "\n".join(lines)


def list_nodes(graph: Graph, kinds: list[str] | None = None) -> Listing:
    """Every node of the given kinds (relations by default), sorted by kind then label."""
    chosen = kinds or sorted(RELATION_KINDS)
    nodes = [n for n in graph.nodes.values() if n.kind in chosen]
    nodes.sort(key=lambda n: (chosen.index(n.kind) if n.kind in chosen else 0, graph.label(n).lower()))
    return Listing(list(chosen), [item(graph, n) for n in nodes])


# Free-text query


_DOWNSTREAM_WORDS = frozenset(
    {
        "impact",
        "break",
        "breaks",
        "affect",
        "affects",
        "affected",
        "downstream",
        "depend",
        "depends",
        "dependants",
        "dependents",
        "change",
        "rename",
        "drop",
        "remove",
    }
)
_UPSTREAM_WORDS = frozenset(
    {
        "upstream",
        "source",
        "sources",
        "lineage",
        "derived",
        "origin",
        "come",
        "comes",
        "trace",
        "built",
        "calculated",
        "computed",
    }
)


@dataclass(frozen=True)
class EdgeItem:
    source: str
    target: str
    kind: str
    source_label: str
    target_label: str
    transform: str | None = None
    role: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_empty({f.name: getattr(self, f.name) for f in fields(self)})

    def __str__(self) -> str:
        extra = self.transform or self.role
        return f"{self.source_label} -{self.kind}-> {self.target_label}" + (f" ({extra})" if extra else "")


@dataclass
class QueryResult:
    question: str
    matches: list[Item] = field(default_factory=list)
    edges: list[EdgeItem] = field(default_factory=list)
    neighbours: list[Item] = field(default_factory=list)
    impact: Walk | None = None
    lineage: Walk | None = None

    EDGE_LIMIT = 80

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "question": self.question,
            "matches": [m.to_dict() for m in self.matches],
            "edges": [e.to_dict() for e in self.edges],
            "neighbours": [n.to_dict() for n in self.neighbours],
        }
        if self.impact is not None:
            data["impact"] = self.impact.to_dict()
        if self.lineage is not None:
            data["lineage"] = self.lineage.to_dict()
        return data

    def __str__(self) -> str:
        if not self.matches:
            return (
                f"Nothing in the graph matches {self.question!r}. "
                "Try `kerplunk search` with a table or column name."
            )
        lines = ["Best matches"]
        for match in self.matches:
            lines.append(f"  {match}")
            if match.description:
                lines.append(f"      {_shorten(match.description)}")
        if self.edges:
            lines += ["", "Connections", *(f"  {e}" for e in self.edges[: self.EDGE_LIMIT])]
            if len(self.edges) > self.EDGE_LIMIT:
                lines.append(f"  and {len(self.edges) - self.EDGE_LIMIT} more")
        for walk in (self.impact, self.lineage):
            if walk is not None:
                lines += ["", str(walk)]
        return "\n".join(lines)


def query(graph: Graph, question: str, limit: int = 8, depth: int = 1) -> QueryResult:
    """Answer a free-text question with the most relevant nodes and their neighbourhood.

    There is no language model here: kerplunk ranks nodes by the question's words and returns
    the matching nodes, the edges around them, and impact or lineage when the question asks.
    """
    words = set(_TOKEN.findall(question.lower()))
    seeds = _scored(graph, question, limit, None)
    result = QueryResult(question)
    if not seeds:
        return result
    top_score = seeds[0][1]
    chosen = [node for node, score in seeds if score >= top_score * 0.35]
    seen = {n.id for n in chosen}
    edge_keys: set[tuple[str, str, str]] = set()
    result.matches = [item(graph, n) for n in chosen]
    for node in chosen:
        frontier = [node.id]
        for _ in range(depth):
            following = []
            for node_id in frontier:
                for edge in [*graph.out_edges(node_id), *graph.in_edges(node_id)]:
                    if edge.kind == "contains" or edge.key in edge_keys:
                        continue
                    edge_keys.add(edge.key)
                    result.edges.append(
                        EdgeItem(
                            source=edge.source,
                            target=edge.target,
                            kind=edge.kind,
                            source_label=graph.label(graph.nodes[edge.source]),
                            target_label=graph.label(graph.nodes[edge.target]),
                            transform=edge.attrs.get("transform"),
                            role=edge.attrs.get("role"),
                        )
                    )
                    for other in (edge.source, edge.target):
                        if other not in seen:
                            seen.add(other)
                            following.append(other)
            frontier = following
    chosen_ids = {n.id for n in chosen}
    result.neighbours = [item(graph, graph.nodes[i]) for i in sorted(seen - chosen_ids)][:60]
    top = chosen[0]
    if words & _DOWNSTREAM_WORDS:
        result.impact = impact(graph, top.id)
    elif words & _UPSTREAM_WORDS:
        result.lineage = trace(graph, top.id)
    return result
