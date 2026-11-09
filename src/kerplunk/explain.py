"""What a column is, as a formula: ``kerplunk explain orders.credit_card_amount``.

The build keeps each column's expression as SQL over physical columns, with CTEs, subqueries and
aliases inlined (``attrs["expression"]`` on column nodes), and each relation's join, filter and
grouping conditions in the same form (``attrs["conditions"]``). This module reads those back and:

* renders them with short names (``stg_payments.amount`` rather than the fully qualified name);
* composes the formula down to sources, by substituting each input column with that column's own
  expression, recursively, until the inputs are columns nothing in the graph derives (seeds,
  sources, warehouse tables);
* lists, step by step, the expression of every column on the way and the conditions of every
  relation it passes through.

The composed formula describes values, not rows. Substitution is exact for the expression, but
the joins, filters and grouping of each step decide which rows feed it, so they are listed beside
it rather than folded in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from kerplunk.graph import Graph, Node

MAX_COMPOSED = 4000
"""Stop substituting once the composed formula is longer than this many characters."""

MAX_DEPTH = 25


@dataclass
class Step:
    """One column on the way down: its formula and the conditions on the rows of its relation."""

    column: str
    label: str
    relation_kind: str
    expression: str | None
    conditions: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    """Labels of the columns the expression reads."""
    verified: list[str] = field(default_factory=list)
    """For each input, ``verify``'s result for the edge, when it has been run."""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [])}


@dataclass
class Explanation:
    column: str
    label: str
    data_type: str | None
    description: str | None
    expression: str | None
    composed: str | None
    """The formula in terms of source columns, or None when nothing could be substituted."""
    sources: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    complete: bool = True
    """False when some column on the way has no recorded expression (Python, unparsed SQL)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "label": self.label,
            "data_type": self.data_type,
            "description": self.description,
            "expression": self.expression,
            "composed": self.composed,
            "sources": self.sources,
            "complete": self.complete,
            "steps": [s.to_dict() for s in self.steps],
        }

    def __str__(self) -> str:
        head = self.label + (f"  ({self.data_type})" if self.data_type else "")
        lines = [head]
        if self.description:
            lines.append(f"  {self.description}")
        if self.expression is None:
            lines += ["", "  No expression recorded: this column is read from a source, or the SQL that"]
            lines.append("  builds it could not be analysed.")
            return "\n".join(lines)
        lines += ["", f"  = {self.expression}"]
        if self.composed and self.composed != self.expression:
            lines += ["", "In terms of sources", f"  = {self.composed}"]
        if not self.complete:
            lines.append("  (partial: some columns on the way have no recorded expression)")
        if self.sources:
            lines += ["", f"Sources ({len(self.sources)})", *(f"  {s}" for s in self.sources)]
        derived = [s for s in self.steps if s.expression is not None]
        if derived:
            lines += ["", "Step by step"]
            for step in derived:
                checked = step.verified and all(v == "confirmed" for v in step.verified)
                lines.append(f"  {step.label} = {step.expression}" + ("  [verified]" if checked else ""))
                for condition in step.conditions:
                    lines.append(f"      {condition}")
        return "\n".join(lines)


class _Namer:
    """Short names for the fully qualified columns in an expression."""

    def __init__(self, graph: Graph, dialect: str | None) -> None:
        self.graph = graph
        self.dialect = dialect
        self.cache: dict[str, Node | None] = {}

    def relation(self, key: str) -> Node | None:
        if key not in self.cache:
            self.cache[key] = self.graph.node_for_relation(key)
        return self.cache[key]

    def resolve(self, column: exp.Column) -> tuple[Node, str] | None:
        """The relation node and column name a qualified column refers to: the longest prefix of
        its parts that names a relation, so nested fields (``orders.customer.country``) work."""
        parts = [p.name for p in column.parts]
        for split in range(len(parts) - 1, 0, -1):
            node = self.relation(".".join(parts[:split]))
            if node is not None:
                return node, ".".join(parts[split:])
        return None

    def parse(self, sql: str) -> exp.Expr | None:
        try:
            tree: exp.Expr = sqlglot.parse_one(sql, read=self.dialect)
            return tree
        except (sqlglot.errors.ParseError, sqlglot.errors.TokenError, ValueError):
            return None

    def short(self, tree: exp.Expr) -> str:
        def rename(node: exp.Expr) -> exp.Expr:
            if isinstance(node, exp.Column):
                found = self.resolve(node)
                if found is not None:
                    relation, name = found
                    return exp.column(name, table=self.graph.label(relation), quoted=False)
            return node

        return _unparen(tree.copy().transform(rename)).sql(dialect=self.dialect)

    def text(self, sql: str) -> str:
        tree = self.parse(sql)
        return self.short(tree) if tree is not None else sql

    def condition(self, text: str) -> str:
        prefix, sep, rest = text.partition(": ")
        if not sep:
            return self.text(text)
        return f"{prefix}: {self.text(rest)}"


def short_sql(graph: Graph, sql: str, *, condition: bool = False) -> str:
    """An expression or condition from the graph with short relation names."""
    dialect = graph.meta.get("dialect")
    namer = _Namer(graph, dialect if isinstance(dialect, str) else None)
    return namer.condition(sql) if condition else namer.text(sql)


def _unparen(tree: exp.Expr) -> exp.Expr:
    """Drop parentheses that substitution added where they change nothing: around a whole
    function argument, a CASE branch, another parenthesis or the whole formula."""

    def drop(node: exp.Expr) -> exp.Expr:
        if isinstance(node, exp.Paren):
            inner: exp.Expr = node.this
            parent = node.parent
            if (
                parent is None
                or isinstance(inner, (exp.Column, exp.Literal, exp.Func, exp.Paren))
                or isinstance(parent, (exp.Func, exp.Paren, exp.If, exp.Case, exp.Alias))
            ):
                return inner
        return node

    result: exp.Expr = tree.transform(drop)
    return result


@dataclass
class _Composed:
    """A column's formula with its inputs substituted, the source columns it reached, and whether
    every column on the way had an expression."""

    tree: exp.Expr | None
    sources: frozenset[str]
    complete: bool


class Explainer:
    """Explains columns of one graph, composing each column once however many ask for it."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        dialect = graph.meta.get("dialect")
        self.namer = _Namer(graph, dialect if isinstance(dialect, str) else None)
        self.memo: dict[str, _Composed] = {}
        self.active: set[str] = set()

    def compose(self, node: Node, depth: int = 0) -> _Composed:
        """``node``'s expression with every input column replaced by that column's own composed
        expression; a column without an expression is a source (or, if it has inputs, a gap)."""
        if node.id in self.memo:
            return self.memo[node.id]
        if node.id in self.active or depth > MAX_DEPTH:
            return _Composed(None, frozenset(), False)
        graph = self.graph
        expression = node.attrs.get("expression")
        upstream = [graph.nodes[e.target] for e in graph.out_edges(node.id, "derived_from")]
        if not isinstance(expression, str):
            leaf = _Composed(None, frozenset() if upstream else frozenset({node.id}), not upstream)
            self.memo[node.id] = leaf
            return leaf
        tree = self.namer.parse(expression)
        if tree is None:
            failed = _Composed(None, frozenset(), False)
            self.memo[node.id] = failed
            return failed
        self.active.add(node.id)
        by_name = {(u.parent or "", u.name.lower()): u for u in upstream}
        by_relation = {}
        for u in upstream:
            owner = graph.parent_of(u)
            if owner is not None and owner.relation:
                by_relation[(owner.relation.lower(), u.name.lower())] = u
        sources: set[str] = set()
        complete = True

        def substitute(item: exp.Expr) -> exp.Expr:
            nonlocal complete
            if not isinstance(item, exp.Column):
                return item
            found = self.namer.resolve(item)
            if found is None:
                return item
            relation, name = found
            target = (
                by_name.get((relation.id, name.lower()))
                or (by_relation.get((relation.relation.lower(), name.lower())) if relation.relation else None)
                or graph.nodes.get(f"{relation.id}#{name}")
            )
            if target is None:
                return item
            inner = self.compose(target, depth + 1)
            sources.update(inner.sources)
            complete = complete and inner.complete
            if inner.tree is None:
                return item
            if isinstance(inner.tree, (exp.Column, exp.Literal, exp.Func, exp.Paren)):
                return inner.tree.copy()
            return exp.Paren(this=inner.tree.copy())

        composed = tree.transform(substitute)
        if len(composed.sql()) > MAX_COMPOSED:
            composed, complete = tree, False
        self.active.discard(node.id)
        result = _Composed(composed, frozenset(sources), complete)
        self.memo[node.id] = result
        return result

    def steps(self, column: Node) -> list[Step]:
        """Every column from ``column`` upstream, nearest first, with its formula and conditions."""
        graph = self.graph
        seen = {column.id}
        queue = [column]
        steps = []
        while queue:
            node = queue.pop(0)
            expression = node.attrs.get("expression")
            owner = graph.parent_of(node)
            edges = graph.out_edges(node.id, "derived_from")
            steps.append(
                Step(
                    column=node.id,
                    label=graph.label(node),
                    relation_kind=owner.kind if owner else "column",
                    expression=self.namer.text(expression) if isinstance(expression, str) else None,
                    conditions=[self.namer.condition(c) for c in owner.attrs.get("conditions", [])]
                    if owner
                    else [],
                    inputs=[graph.label(graph.nodes[e.target]) for e in edges],
                    verified=[str(e.attrs["verified"]) for e in edges if e.attrs.get("verified")],
                )
            )
            for edge in edges:
                if edge.target not in seen:
                    seen.add(edge.target)
                    queue.append(graph.nodes[edge.target])
        return steps

    def explain(self, column: Node) -> Explanation:
        raw = column.attrs.get("expression")
        result = Explanation(
            column=column.id,
            label=self.graph.label(column),
            data_type=column.data_type,
            description=column.description,
            expression=self.namer.text(raw) if isinstance(raw, str) else None,
            composed=None,
        )
        if not isinstance(raw, str):
            return result
        composed = self.compose(column)
        result.composed = self.namer.short(composed.tree) if composed.tree is not None else None
        result.sources = sorted(self.graph.label(self.graph.nodes[s]) for s in composed.sources)
        result.complete = composed.complete
        result.steps = self.steps(column)
        return result

    def summaries(self, limit: int = 50_000) -> dict[str, Any]:
        """Formulas for every column that has one, and conditions for every relation, compactly:
        ``{"columns": {id: {"e": expression, "c": composed, "s": sources, "p": partial}},
        "conditions": {relation id: [condition, ...]}}``. Used by graph.html."""
        columns: dict[str, dict[str, Any]] = {}
        conditions: dict[str, list[str]] = {}
        for node in self.graph.nodes.values():
            if node.kind == "column" and isinstance(node.attrs.get("expression"), str):
                if len(columns) >= limit:
                    continue
                composed = self.compose(node)
                entry: dict[str, Any] = {"e": self.namer.text(node.attrs["expression"])}
                if composed.tree is not None:
                    text = self.namer.short(composed.tree)
                    if text != entry["e"]:
                        entry["c"] = text
                        entry["s"] = sorted(self.graph.label(self.graph.nodes[s]) for s in composed.sources)
                if not composed.complete:
                    entry["p"] = 1
                columns[node.id] = entry
            elif node.attrs.get("conditions"):
                conditions[node.id] = [self.namer.condition(c) for c in node.attrs["conditions"]]
        return {"columns": columns, "conditions": conditions}


def explain(graph: Graph, column: Node) -> Explanation:
    """The formula behind ``column`` and how it composes down to sources."""
    return Explainer(graph).explain(column)
