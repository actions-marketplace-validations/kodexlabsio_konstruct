"""Questions over a built graph: impact, trace, path, search, describe and free-text query.

Every function returns plain data (dicts and lists) so the CLI can print it as text or JSON
and the MCP server can return it as is. `render_*` functions turn results into compact text
written for coding agents to read.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from kerplunk.graph import RELATION_KINDS, Graph, Node, Step


class QueryError(Exception):
    pass


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


def summary(graph: Graph, node: Node) -> dict[str, Any]:
    data: dict[str, Any] = {"id": node.id, "kind": node.kind, "label": graph.label(node)}
    if node.kind == "column":
        parent = graph.parent_of(node)
        if parent is not None:
            data["relation_kind"] = parent.kind
            if parent.path:
                data["path"] = parent.path
    elif node.path:
        data["path"] = node.path
    if node.data_type:
        data["data_type"] = node.data_type
    if node.description:
        data["description"] = node.description
    return data


def _step(graph: Graph, step: Step) -> dict[str, Any]:
    data = summary(graph, step.node)
    data["depth"] = step.depth
    if step.via is not None:
        data["via"] = step.via.kind
        for key in ("transform", "role", "command"):
            if key in step.via.attrs:
                data[key] = step.via.attrs[key]
        if "via" in step.via.attrs:
            through = step.via.attrs["via"]
            data["through"] = graph.label(graph.nodes[through]) if through in graph.nodes else through
    return data


def _categorise(graph: Graph, start: Node, steps: list[Step]) -> dict[str, Any]:
    start_ids = set(graph.expand(start))
    result: dict[str, Any] = {
        "target": summary(graph, start),
        "columns": [],
        "relations": [],
        "joins_and_filters": [],
        "tests": [],
        "exposures": [],
        "tasks": [],
    }
    relation_ids: set[str] = set()
    for step in sorted(steps, key=lambda s: (s.depth, s.node.id)):
        node = step.node
        if node.id in start_ids:
            continue
        entry = _step(graph, step)
        if node.kind == "column":
            result["columns"].append(entry)
            if node.parent and node.parent not in start_ids:
                relation_ids.add(node.parent)
        elif node.kind == "test":
            result["tests"].append(entry)
        elif node.kind == "exposure":
            result["exposures"].append(entry)
        elif node.kind in ("task", "dag"):
            result["tasks"].append(entry)
        elif step.via is not None and step.via.attrs.get("role") == "reference":
            result["joins_and_filters"].append(entry)
            relation_ids.discard(node.id)
        else:
            result["relations"].append(entry)
            relation_ids.discard(node.id)
    listed = {r["id"] for r in result["relations"]} | {r["id"] for r in result["joins_and_filters"]}
    for relation_id in sorted(relation_ids - listed):
        result["relations"].append(summary(graph, graph.nodes[relation_id]))
    result["total"] = sum(len(v) for k, v in result.items() if isinstance(v, list))
    return result


def impact(graph: Graph, ref: str, max_depth: int | None = None) -> dict[str, Any]:
    """Everything downstream of a relation or column."""
    node = resolve_one(graph, ref)
    return {"direction": "downstream", **_categorise(graph, node, graph.impact(node, max_depth))}


def trace(graph: Graph, ref: str, max_depth: int | None = None) -> dict[str, Any]:
    """Everything upstream of a relation or column."""
    node = resolve_one(graph, ref)
    result = {"direction": "upstream", **_categorise(graph, node, graph.trace(node, max_depth))}
    if node.kind == "column" and node.parent:
        result["filtered_by"] = [
            summary(graph, graph.nodes[e.source])
            for e in graph.in_edges(node.parent, "feeds")
            if e.attrs.get("role") == "reference" and e.source in graph.nodes
        ]
        result["total"] += len(result["filtered_by"])
    return result


def path(graph: Graph, start_ref: str, end_ref: str) -> dict[str, Any]:
    start = resolve_one(graph, start_ref)
    end = resolve_one(graph, end_ref)
    steps = graph.path(start, end)
    return {
        "from": summary(graph, start),
        "to": summary(graph, end),
        "found": bool(steps),
        "steps": [
            {**_step(graph, s), "direction": "forward" if s.via.source == s.previous else "backward"}
            if s.via is not None
            else _step(graph, s)
            for s in steps
        ],
    }


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
    scored.sort(key=lambda item: (-item[1], len(item[0].id), item[0].id))
    return scored[:limit]


def search(graph: Graph, text: str, limit: int = 20, kinds: list[str] | None = None) -> dict[str, Any]:
    return {
        "query": text,
        "results": [
            {**summary(graph, n), "score": round(s, 2)} for n, s in _scored(graph, text, limit, kinds)
        ],
    }


def describe(graph: Graph, ref: str) -> dict[str, Any]:
    node = resolve_one(graph, ref)
    data = summary(graph, node)
    for key in ("relation", "path", "parent"):
        value = getattr(node, key)
        if value:
            data[key] = value
    if node.origins:
        data["origins"] = sorted(node.origins)
    if node.attrs:
        data["attrs"] = node.attrs
    if node.kind in RELATION_KINDS:
        data["columns"] = [
            {k: v for k, v in summary(graph, c).items() if k not in ("kind", "relation_kind", "path")}
            for c in graph.columns_of(node.id)
        ]
    checked = [node.id, *(c.id for c in graph.columns_of(node.id))]
    data["tests"] = sorted(
        {graph.nodes[e.source].name for target in checked for e in graph.in_edges(target, "tests")}
    )
    data["upstream"] = sorted(
        {
            _neighbour(graph, other)
            for _, other in graph.upstream_edges(node.id)
            if graph.nodes[other].kind != "dag"
        }
    )
    data["downstream"] = sorted(
        {
            _neighbour(graph, other)
            for _, other in graph.downstream_edges(node.id)
            if graph.nodes[other].kind != "dag"
        }
    )
    if node.kind == "dag":
        data["tasks"] = [graph.nodes[e.target].name for e in graph.out_edges(node.id, "runs")]
    if node.kind == "task":
        data["runs"] = sorted(graph.label(graph.nodes[e.target]) for e in graph.out_edges(node.id, "runs"))
        data["after"] = sorted(graph.nodes[e.source].name for e in graph.in_edges(node.id, "precedes"))
        data["before"] = sorted(graph.nodes[e.target].name for e in graph.out_edges(node.id, "precedes"))
    return data


def _neighbour(graph: Graph, node_id: str) -> str:
    node = graph.nodes[node_id]
    return f"{graph.label(node)} ({node.kind})"


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


def query(graph: Graph, question: str, limit: int = 8, depth: int = 1) -> dict[str, Any]:
    """Answer a free-text question with the most relevant nodes and their neighbourhood.

    There is no language model here: kerplunk ranks nodes by the question's words and returns
    the matching nodes, the edges around them, and impact or lineage when the question asks.
    """
    words = set(_TOKEN.findall(question.lower()))
    seeds = _scored(graph, question, limit, None)
    result: dict[str, Any] = {"question": question, "matches": [], "edges": []}
    if not seeds:
        return result
    top_score = seeds[0][1]
    chosen = [node for node, score in seeds if score >= top_score * 0.35]
    seen = {n.id for n in chosen}
    result["matches"] = [summary(graph, n) for n in chosen]
    for node in chosen:
        frontier = [node.id]
        for _ in range(depth):
            following = []
            for node_id in frontier:
                for edge in [*graph.out_edges(node_id), *graph.in_edges(node_id)]:
                    if edge.kind == "contains":
                        continue
                    record = {"source": edge.source, "target": edge.target, "kind": edge.kind}
                    for key in ("transform", "role"):
                        if key in edge.attrs:
                            record[key] = edge.attrs[key]
                    if record not in result["edges"]:
                        result["edges"].append(record)
                    for other in (edge.source, edge.target):
                        if other not in seen:
                            seen.add(other)
                            following.append(other)
            frontier = following
    result["neighbours"] = [summary(graph, graph.nodes[i]) for i in sorted(seen - {n.id for n in chosen})][
        :60
    ]
    top = chosen[0]
    if words & _DOWNSTREAM_WORDS:
        result["impact"] = impact(graph, top.id)
    elif words & _UPSTREAM_WORDS:
        result["lineage"] = trace(graph, top.id)
    return result


# Text rendering


def _line(item: dict[str, Any]) -> str:
    parts = [f"{item['label']}"]
    meta = [item.get("relation_kind") or item["kind"]]
    for key in ("data_type", "transform", "role", "command"):
        if item.get(key):
            meta.append(str(item[key]))
    if item.get("through"):
        meta.append(f"via {item['through']}")
    parts.append(f"[{', '.join(meta)}]")
    if item.get("path"):
        parts.append(item["path"])
    if "depth" in item:
        parts.insert(0, f"{item['depth']:>2}")
    return "  ".join(parts)


def render_walk(result: dict[str, Any]) -> str:
    target = result["target"]
    title = "Downstream of" if result["direction"] == "downstream" else "Upstream of"
    lines = [f"{title} {target['label']} ({target.get('relation_kind') or target['kind']})"]
    if target.get("description"):
        lines.append(f"  {_shorten(target['description'])}")
    sections = [
        ("columns", "Columns"),
        ("relations", "Relations"),
        ("joins_and_filters", "Relations that join, filter or group on it"),
        ("filtered_by", "Columns that filter or join its relation"),
        ("tests", "Tests"),
        ("exposures", "Exposures"),
        ("tasks", "Orchestration"),
    ]
    for key, heading in sections:
        items = result.get(key) or []
        if items:
            lines.append("")
            lines.append(f"{heading} ({len(items)})")
            lines.extend(f"  {_line(item)}" for item in items)
    if not result.get("total"):
        lines.append("")
        lines.append("Nothing found." if result["direction"] == "downstream" else "No upstream found.")
    return "\n".join(lines)


def render_path(result: dict[str, Any]) -> str:
    if not result["found"]:
        return f"No path between {result['from']['label']} and {result['to']['label']}."
    lines = [f"Path from {result['from']['label']} to {result['to']['label']}:"]
    for step in result["steps"]:
        arrow = ""
        if step.get("via"):
            arrow = f"-{step['via']}-> " if step.get("direction") == "forward" else f"<-{step['via']}- "
        lines.append(f"  {arrow}{step['label']} ({step.get('relation_kind') or step['kind']})")
    return "\n".join(lines)


def render_search(result: dict[str, Any]) -> str:
    if not result["results"]:
        return f"Nothing matches {result['query']!r}."
    lines = []
    for item in result["results"]:
        line = _line(item)
        if item.get("description"):
            line += f"\n      {_shorten(item['description'])}"
        lines.append(line)
    return "\n".join(lines)


def _shorten(text: str, width: int = 140) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 3] + "..."


def render_describe(result: dict[str, Any]) -> str:
    lines = [f"{result['label']} ({result.get('relation_kind') or result['kind']})", f"  id: {result['id']}"]
    for key in ("relation", "path", "data_type", "description"):
        if result.get(key):
            lines.append(f"  {key}: {result[key]}")
    if result.get("origins"):
        lines.append(f"  from: {', '.join(result['origins'])}")
    attrs = {k: v for k, v in (result.get("attrs") or {}).items() if k not in ("fqn", "dbt_name", "checksum")}
    for key, value in attrs.items():
        if value not in (None, "", [], {}):
            lines.append(f"  {key}: {value}")
    if result.get("columns"):
        lines.append("")
        lines.append(f"Columns ({len(result['columns'])})")
        for column in result["columns"]:
            text = f"  {column['label'].rsplit('.', 1)[-1]}"
            if column.get("data_type"):
                text += f"  {column['data_type']}"
            if column.get("description"):
                text += f"  {_shorten(column['description'], 100)}"
            lines.append(text)
    for key, heading in (
        ("tests", "Tests"),
        ("upstream", "Upstream"),
        ("downstream", "Downstream"),
        ("tasks", "Tasks"),
        ("runs", "Runs"),
        ("after", "After"),
        ("before", "Before"),
    ):
        if result.get(key):
            lines.append("")
            lines.append(f"{heading} ({len(result[key])})")
            lines.extend(f"  {item}" for item in result[key])
    return "\n".join(lines)


def render_query(graph: Graph, result: dict[str, Any]) -> str:
    if not result["matches"]:
        return f"Nothing in the graph matches {result['question']!r}. Try `kerplunk search` with a table or column name."
    lines = ["Best matches"]
    for item in result["matches"]:
        line = f"  {_line(item)}"
        if item.get("description"):
            line += f"\n      {_shorten(item['description'])}"
        lines.append(line)
    if result["edges"]:
        lines.append("")
        lines.append("Connections")
        for edge in result["edges"][:80]:
            source = (
                graph.label(graph.nodes[edge["source"]]) if edge["source"] in graph.nodes else edge["source"]
            )
            target = (
                graph.label(graph.nodes[edge["target"]]) if edge["target"] in graph.nodes else edge["target"]
            )
            extra = (
                f" ({edge.get('transform') or edge.get('role')})"
                if edge.get("transform") or edge.get("role")
                else ""
            )
            lines.append(f"  {source} -{edge['kind']}-> {target}{extra}")
        if len(result["edges"]) > 80:
            lines.append(f"  and {len(result['edges']) - 80} more")
    if "impact" in result:
        lines += ["", render_walk(result["impact"])]
    if "lineage" in result:
        lines += ["", render_walk(result["lineage"])]
    return "\n".join(lines)
