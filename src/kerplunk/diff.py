"""`kerplunk diff`: what a change does to the data stack, column by column, before it is merged.

For every dbt model, SQL file and Airflow DAG file that differs between a base commit and the
working tree (or a head commit), kerplunk analyses both versions and compares each output column's
canonical expression (CTEs, subqueries and aliases inlined down to physical columns, so formatting,
renamed CTEs and reordered columns do not count as changes) and the join, filter and grouping
conditions. It then reports:

* columns added, removed or changed, with the expression before and after;
* changed conditions, which change the rows a model returns;
* breaking changes: a removed column that a downstream model still reads in the new version;
* the impact of each change, from the graph: columns, relations, tests, exposures and tasks;
* DAG changes: tasks and dependencies added or removed, and dbt selections that now run
  different models.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kerplunk import airflow, dbt
from kerplunk.graph import RELATION_KINDS, Graph, Node
from kerplunk.jinja import Renderer, render_model
from kerplunk.query import Walk, impact
from kerplunk.sql import StatementLineage, analyse, strip_templating
from kerplunk.verify import defining_sql_file


class DiffError(Exception):
    pass


# git


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise DiffError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def default_base(root: Path) -> str:
    """The merge base with origin/main (or main, or master); otherwise HEAD."""
    for candidate in ("origin/main", "origin/master", "main", "master"):
        if _git(root, "rev-parse", "--verify", "--quiet", candidate, check=False).strip():
            base = _git(root, "merge-base", "HEAD", candidate, check=False).strip()
            if base and base != _git(root, "rev-parse", "HEAD", check=False).strip():
                return base
    return "HEAD"


def changed_files(root: Path, base: str, head: str | None) -> dict[str, str]:
    """Paths (relative to the repository) mapped to A, M, D or R, between base and head or the
    working tree including untracked files."""
    args = ["diff", "--name-status", "--no-renames", base] + ([head] if head else [])
    changes: dict[str, str] = {}
    for line in _git(root, *args).splitlines():
        status, _, path = line.partition("\t")
        if path:
            changes[path] = status[:1]
    if head is None:
        for path in _git(root, "ls-files", "--others", "--exclude-standard").splitlines():
            changes.setdefault(path, "A")
    return changes


def file_at(repository: Path, ref: str | None, path: str) -> str | None:
    """A file's text at ``ref``, or in the working tree when ``ref`` is None; None if absent."""
    if ref is None:
        target = repository / path
        return target.read_text(encoding="utf-8") if target.is_file() else None
    result = subprocess.run(
        ["git", "-C", str(repository), "show", f"{ref}:{path}"], capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


# Results


@dataclass
class ColumnChange:
    column: str
    change: str
    before: str | None = None
    after: str | None = None
    impact: dict[str, Any] | None = None
    breaks: list[str] = field(default_factory=list)


@dataclass
class RelationChange:
    path: str
    label: str
    kind: str
    status: str
    columns: list[ColumnChange] = field(default_factory=list)
    conditions_added: list[str] = field(default_factory=list)
    conditions_removed: list[str] = field(default_factory=list)
    rows_impact: dict[str, Any] | None = None
    note: str | None = None

    @property
    def changed(self) -> bool:
        return bool(
            self.columns
            or self.conditions_added
            or self.conditions_removed
            or self.status in ("added", "removed")
        )


@dataclass
class DagChange:
    path: str
    dag: str
    status: str
    tasks_added: list[str] = field(default_factory=list)
    tasks_removed: list[str] = field(default_factory=list)
    dependencies_added: list[str] = field(default_factory=list)
    dependencies_removed: list[str] = field(default_factory=list)
    runs_changed: list[str] = field(default_factory=list)
    sql_changed: list[str] = field(default_factory=list)


@dataclass
class Diff:
    base: str
    head: str
    files: dict[str, str]
    relations: list[RelationChange] = field(default_factory=list)
    dags: list[DagChange] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    @property
    def source_files(self) -> list[str]:
        """Changed files, leaving out generated ones (target/, logs/, kerplunk-out/, packages)."""
        return [f for f in self.files if not set(Path(f).parts) & dbt.SKIP_DIRS]

    @property
    def breaking(self) -> list[tuple[str, ColumnChange]]:
        return [(r.label, c) for r in self.relations for c in r.columns if c.breaks]

    def counts(self) -> dict[str, int]:
        columns = [c for r in self.relations for c in r.columns]
        exposures: set[str] = set()
        for change in columns:
            for exposure in (change.impact or {}).get("exposures", []):
                exposures.add(exposure["label"])
        for relation in self.relations:
            for exposure in (relation.rows_impact or {}).get("exposures", []):
                exposures.add(exposure["label"])
        return {
            "files": len(self.source_files),
            "relations": sum(1 for r in self.relations if r.changed),
            "added": sum(1 for c in columns if c.change == "added"),
            "removed": sum(1 for c in columns if c.change == "removed"),
            "changed": sum(1 for c in columns if c.change == "changed"),
            "conditions": sum(1 for r in self.relations if r.conditions_added or r.conditions_removed),
            "breaking": len(self.breaking),
            "dags": len(self.dags),
            "exposures": len(exposures),
        }

    def summary(self) -> str:
        c = self.counts()
        if not c["relations"] and not c["dags"]:
            return "No change to models, SQL or DAGs."
        parts = []
        if c["breaking"]:
            parts.append(f"{c['breaking']} breaking change{'s' if c['breaking'] != 1 else ''}")
        for key, noun in (("changed", "changed"), ("removed", "removed"), ("added", "added")):
            if c[key]:
                parts.append(f"{c[key]} column{'s' if c[key] != 1 else ''} {noun}")
        if c["conditions"]:
            parts.append(
                f"row logic changed in {c['conditions']} relation{'s' if c['conditions'] != 1 else ''}"
            )
        if c["dags"]:
            parts.append(f"{c['dags']} DAG{'s' if c['dags'] != 1 else ''} changed")
        if c["exposures"]:
            parts.append(f"{c['exposures']} exposure{'s' if c['exposures'] != 1 else ''} affected")
        text = ", ".join(parts)
        return text[:1].upper() + text[1:] + "."

    def to_dict(self) -> dict[str, Any]:
        def relation(r: RelationChange) -> dict[str, Any]:
            return {
                "path": r.path,
                "relation": r.label,
                "kind": r.kind,
                "status": r.status,
                "note": r.note,
                "columns": [c.__dict__ for c in r.columns],
                "conditions_added": r.conditions_added,
                "conditions_removed": r.conditions_removed,
                "rows_impact": r.rows_impact,
            }

        return {
            "base": self.base,
            "head": self.head,
            "summary": self.summary(),
            "counts": self.counts(),
            "files": self.files,
            "relations": [relation(r) for r in self.relations if r.changed or r.note],
            "dags": [d.__dict__ for d in self.dags],
            "other": self.other,
        }

    # Rendering

    def _impact_line(self, data: dict[str, Any] | None) -> str | None:
        if not data or not data.get("total"):
            return None
        text = str(data["summary"]).rstrip(".")
        exposures = [e["label"] for e in data.get("exposures", [])]
        if exposures:
            text += f" (exposures: {', '.join(exposures)})"
        return text

    def __str__(self) -> str:
        lines = [
            f"kerplunk diff  {self.base}..{self.head}  ({len(self.source_files)} file{'s' if len(self.source_files) != 1 else ''} changed)",
            "",
        ]
        lines.append(self.summary())
        for relation in self.relations:
            if not relation.changed and not relation.note:
                continue
            lines += ["", f"{relation.path}  ({relation.kind} {relation.label}, {relation.status})"]
            if relation.note:
                lines.append(f"  {relation.note}")
            for change in relation.columns:
                head = f"  {change.change:<8} {change.column}"
                if change.change == "changed":
                    head += f"\n             was  {change.before}\n             now  {change.after}"
                lines.append(head)
                for consumer in change.breaks:
                    lines.append(f"           BREAKS {consumer}, which still reads it")
                impact_line = self._impact_line(change.impact)
                if impact_line:
                    lines.append(f"           affects {impact_line}")
            for condition in relation.conditions_added:
                lines.append(f"  rows     + {condition}")
            for condition in relation.conditions_removed:
                lines.append(f"  rows     - {condition}")
            if relation.conditions_added or relation.conditions_removed:
                impact_line = self._impact_line(relation.rows_impact)
                if impact_line:
                    lines.append(f"           affects {impact_line}")
        for dag in self.dags:
            lines += ["", f"{dag.path}  (DAG {dag.dag}, {dag.status})"]
            for label, items in (
                ("task +", dag.tasks_added),
                ("task -", dag.tasks_removed),
                ("after +", dag.dependencies_added),
                ("after -", dag.dependencies_removed),
                ("runs", dag.runs_changed),
                ("sql", dag.sql_changed),
            ):
                lines += [f"  {label:<8} {item}" for item in items]
        if self.other:
            lines += ["", f"Not analysed: {', '.join(self.other)}"]
        return "\n".join(lines)

    def markdown(self) -> str:
        c = self.counts()
        lines = [f"### kerplunk: {self.summary()}", ""]
        if self.breaking:
            lines += ["**Breaking changes**", ""]
            for label, change in self.breaking:
                lines.append(
                    f"- `{label}.{change.column}` is removed but still read by "
                    + ", ".join(f"`{b}`" for b in change.breaks)
                )
            lines.append("")
        rows = []
        for relation in self.relations:
            for change in relation.columns:
                detail = ""
                if change.change == "changed":
                    detail = f"`{change.before}` → `{change.after}`"
                impact_line = self._impact_line(change.impact) or ""
                rows.append(
                    f"| `{relation.label}.{change.column}` | {change.change} | {detail} | {impact_line} |"
                )
            for condition in relation.conditions_added:
                rows.append(
                    f"| `{relation.label}` rows | condition added | `{condition}` | {self._impact_line(relation.rows_impact) or ''} |"
                )
            for condition in relation.conditions_removed:
                rows.append(
                    f"| `{relation.label}` rows | condition removed | `{condition}` | {self._impact_line(relation.rows_impact) or ''} |"
                )
        if rows:
            lines += [
                "| Column | Change | Detail | Downstream |",
                "|--------|--------|--------|------------|",
                *rows,
                "",
            ]
        for dag in self.dags:
            items = [
                *(f"task added: `{t}`" for t in dag.tasks_added),
                *(f"task removed: `{t}`" for t in dag.tasks_removed),
                *(f"dependency added: `{d}`" for d in dag.dependencies_added),
                *(f"dependency removed: `{d}`" for d in dag.dependencies_removed),
                *dag.runs_changed,
                *dag.sql_changed,
            ]
            if items:
                lines += [f"**DAG `{dag.dag}`** ({dag.path})", "", *(f"- {i}" for i in items), ""]
        lines.append(
            f"<sub>{c['files']} files compared between `{self.base}` and `{self.head}` by kerplunk. "
            "Expressions are compared with CTEs and aliases inlined, so formatting and renames do not count.</sub>"
        )
        return "\n".join(lines) + "\n"


# Analysis


class _Context:
    def __init__(self, graph: Graph, root: Path) -> None:
        self.graph = graph
        self.root = root
        self.dialect = graph.meta.get("dialect")
        self.schema = self._schema()
        self.manifests: dict[str, dict[str, Any]] = {}
        self.items: dict[str, dict[str, Any]] = {}
        self.renderers: dict[str, Renderer] = {}
        for project in dbt.find_projects(root):
            try:
                manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            macro_paths = [p for p in project.source_paths if p.name == "macros" or "macro" in p.name]
            renderer = Renderer(project.root, manifest, macro_paths=macro_paths or None)
            for node_id, item in manifest.get("nodes", {}).items():
                self.manifests[node_id] = manifest
                self.items[node_id] = item
                self.renderers[node_id] = renderer

    def _schema(self) -> dict[str, dict[str, str]]:
        schema: dict[str, dict[str, str]] = {}
        for node in self.graph.relations():
            if node.relation:
                columns = {
                    c.name: c.data_type or "UNKNOWN"
                    for c in self.graph.columns_of(node.id)
                    if not c.attrs.get("nested") and not c.attrs.get("documented_only")
                }
                if columns:
                    schema[node.relation] = columns
        return schema

    def model_sql(self, node: Node, text: str) -> tuple[str, str | None, str | None]:
        item = self.items.get(node.id, {})
        manifest = self.manifests.get(node.id, {"nodes": {}, "sources": {}})
        sql = render_model(text, self.renderers.get(node.id), lambda raw: dbt.render_refs(raw, manifest))
        return sql, item.get("database"), item.get("schema")

    def lineage(
        self, sql: str, catalog: str | None, schema: str | None, target: str | None
    ) -> list[StatementLineage]:
        return analyse(
            sql,
            dialect=self.dialect,
            schema=self.schema,
            default_catalog=catalog or self.graph.meta.get("default_catalog"),
            default_db=schema or self.graph.meta.get("default_schema"),
            target=target,
        )


def diff(graph: Graph, root: Path, *, base: str | None = None, head: str | None = None) -> Diff:
    """Compare ``base`` (default: the merge base with main) with ``head`` (default: the working tree)."""
    repository = Path(_git(root, "rev-parse", "--show-toplevel").strip())
    base = base or default_base(root)
    files = changed_files(root, base, head)
    result = Diff(base=base, head=head or "working tree", files=files)
    context = _Context(graph, root)
    prefix = root.resolve().relative_to(repository.resolve())
    by_path: dict[str, list[Node]] = {}
    for node in graph.nodes.values():
        if node.path:
            by_path.setdefault(node.path, []).append(node)
        sql_path = node.attrs.get("sql_path")
        if sql_path:
            by_path.setdefault(str(sql_path), []).append(node)
    for path in sorted(files):
        relative = Path(path)
        if set(relative.parts) & dbt.SKIP_DIRS:
            continue
        try:
            in_project = str(relative.relative_to(prefix)) if str(prefix) != "." else path
        except ValueError:
            continue
        before = file_at(repository, base, path)
        after = file_at(repository, head, path)
        nodes = by_path.get(in_project, [])
        models = [n for n in nodes if n.kind in ("model", "snapshot") and "dbt" in n.origins]
        if models:
            for model in models:
                result.relations.append(_compare_model(context, model, in_project, before, after))
        elif in_project.endswith(".sql") and (nodes or before or after):
            result.relations.extend(_compare_sql_file(context, in_project, before, after))
        elif in_project.endswith(".py") and (
            "DAG" in (before or "") + (after or "") or "@dag" in (before or "") + (after or "")
        ):
            result.dags.extend(_compare_dag_file(context, repository / path, in_project, before, after))
        else:
            result.other.append(in_project)
    return result


def _status(before: str | None, after: str | None) -> str:
    if before is None:
        return "added"
    if after is None:
        return "removed"
    return "modified"


def _compare_model(
    context: _Context, node: Node, path: str, before: str | None, after: str | None
) -> RelationChange:
    change = RelationChange(
        path=path, label=context.graph.label(node), kind=node.kind, status=_status(before, after)
    )
    statements = {}
    for side, text in (("before", before), ("after", after)):
        if text is None:
            continue
        sql, catalog, schema = context.model_sql(node, text)
        selects = [
            s
            for s in context.lineage(sql, catalog, schema, node.relation)
            if s.kind in ("select", "unparsed")
        ]
        if selects and not selects[-1].errors:
            statements[side] = selects[-1]
        elif selects:
            change.note = f"could not compare the {side} version: {selects[-1].errors[0]}"
    _compare_statements(
        context, change, node, statements.get("before"), statements.get("after"), before, after
    )
    return change


def _compare_sql_file(
    context: _Context, path: str, before: str | None, after: str | None
) -> list[RelationChange]:
    sides: dict[str, dict[str, StatementLineage]] = {"before": {}, "after": {}}
    for side, text in (("before", before), ("after", after)):
        if text is None:
            continue
        for statement in context.lineage(strip_templating(text), None, None, None):
            if statement.target and statement.columns:
                sides[side][statement.target] = statement
    changes = []
    for target in sorted(set(sides["before"]) | set(sides["after"])):
        node = context.graph.node_for_relation(target)
        label = context.graph.label(node) if node else target.rsplit(".", 1)[-1]
        old, new = sides["before"].get(target), sides["after"].get(target)
        change = RelationChange(
            path=path,
            label=label,
            kind=node.kind if node else "table",
            status="added" if old is None else "removed" if new is None else "modified",
        )
        _compare_statements(context, change, node, old, new, before if old else None, after if new else None)
        changes.append(change)
    return changes


def _compare_statements(
    context: _Context,
    change: RelationChange,
    node: Node | None,
    old: StatementLineage | None,
    new: StatementLineage | None,
    before: str | None,
    after: str | None,
) -> None:
    graph = context.graph
    old_columns = old.expressions if old else {}
    new_columns = new.expressions if new else {}
    for column in sorted(
        set(old_columns) | set(new_columns),
        key=lambda c: (list(new_columns).index(c) if c in new_columns else 10**6, c),
    ):
        if column not in old_columns:
            change.columns.append(ColumnChange(column, "added", after=new_columns[column]))
        elif column not in new_columns:
            entry = ColumnChange(column, "removed", before=old_columns[column])
            if node is not None:
                entry.breaks = _still_read(context, node, column)
                entry.impact = _walk(graph, node, column)
            change.columns.append(entry)
        elif old_columns[column] != new_columns[column]:
            entry = ColumnChange(column, "changed", before=old_columns[column], after=new_columns[column])
            if node is not None:
                entry.impact = _walk(graph, node, column)
            change.columns.append(entry)
    if old is not None and new is not None:
        change.conditions_added = sorted(set(new.conditions) - set(old.conditions))
        change.conditions_removed = sorted(set(old.conditions) - set(new.conditions))
        if (change.conditions_added or change.conditions_removed) and node is not None:
            change.rows_impact = impact(graph, node.id).to_dict()
    if change.status == "removed" and node is not None:
        change.rows_impact = impact(graph, node.id).to_dict()


def _walk(graph: Graph, node: Node, column: str) -> dict[str, Any] | None:
    target = graph.column(node.id, column)
    if target is None:
        return None
    walk: Walk = impact(graph, target.id)
    return walk.to_dict()


def _still_read(context: _Context, node: Node, column: str) -> list[str]:
    """Downstream models and SQL-built tables whose current SQL still reads ``node.column``."""
    graph = context.graph
    readers = []
    consumers = {e.target for e in graph.out_edges(node.id, "feeds")}
    target = graph.column(node.id, column)
    if target is not None:
        consumers |= {e.source.split("#")[0] for e in graph.in_edges(target.id, "derived_from")}
        consumers |= {e.target for e in graph.out_edges(target.id, "feeds")}
    for consumer_id in sorted(consumers):
        consumer = graph.nodes.get(consumer_id)
        if consumer is None or consumer.kind not in RELATION_KINDS:
            continue
        current = (
            (context.root / consumer.path)
            if consumer.path
            else defining_sql_file(graph, consumer, context.root)
        )
        if current is None or not current.is_file():
            continue
        text = current.read_text(encoding="utf-8")
        if "dbt" in consumer.origins:
            sql, catalog, schema = context.model_sql(consumer, text)
            statements = context.lineage(sql, catalog, schema, consumer.relation)
        else:
            statements = [
                s
                for s in context.lineage(strip_templating(text), None, None, None)
                if s.target == consumer.relation
            ]
        for statement in statements:
            uses = {
                (s.relation, s.column.split(".")[0])
                for sources in statement.columns.values()
                for s in sources
            }
            uses |= {(relation, name.split(".")[0]) for relation, name in statement.references}
            if (node.relation, column) in uses:
                readers.append(graph.label(consumer))
                break
    return readers


def _compare_dag_file(
    context: _Context, path: Path, rel: str, before: str | None, after: str | None
) -> list[DagChange]:
    def parse(text: str | None) -> dict[str, airflow.DagInfo]:
        if text is None:
            return {}
        try:
            return {d.dag_id: d for d in airflow.parse_source(path, text)}
        except SyntaxError:
            return {}

    old, new = parse(before), parse(after)
    changes = []
    for dag_id in sorted(set(old) | set(new)):
        a, b = old.get(dag_id), new.get(dag_id)
        change = DagChange(
            path=rel, dag=dag_id, status="added" if a is None else "removed" if b is None else "modified"
        )
        old_tasks = a.tasks if a else {}
        new_tasks = b.tasks if b else {}
        change.tasks_added = sorted(set(new_tasks) - set(old_tasks))
        change.tasks_removed = sorted(set(old_tasks) - set(new_tasks))
        old_deps = {f"{u} >> {d}" for u, d in (a.dependencies if a else [])}
        new_deps = {f"{u} >> {d}" for u, d in (b.dependencies if b else [])}
        change.dependencies_added = sorted(new_deps - old_deps)
        change.dependencies_removed = sorted(old_deps - new_deps)
        for task_id in sorted(set(old_tasks) & set(new_tasks)):
            ran = _runs(context.graph, old_tasks[task_id])
            runs = _runs(context.graph, new_tasks[task_id])
            if ran != runs:
                added, removed = sorted(runs - ran), sorted(ran - runs)
                detail = "; ".join(
                    part
                    for part in (
                        f"now also runs {', '.join(added)}" if added else "",
                        f"no longer runs {', '.join(removed)}" if removed else "",
                    )
                    if part
                )
                change.runs_changed.append(f"task `{task_id}`: {detail}")
            if (old_tasks[task_id].sql or "").strip() != (new_tasks[task_id].sql or "").strip():
                change.sql_changed.append(f"task `{task_id}`: SQL changed")
        if (
            any(
                (
                    change.tasks_added,
                    change.tasks_removed,
                    change.dependencies_added,
                    change.dependencies_removed,
                    change.runs_changed,
                    change.sql_changed,
                )
            )
            or change.status != "modified"
        ):
            changes.append(change)
    return changes


def _runs(graph: Graph, task: airflow.TaskInfo) -> set[str]:
    selected: set[str] = set()
    for invocation in task.dbt:
        nodes = dbt.select_nodes(graph, invocation.selectors, invocation.excludes)
        kinds = {"seed": {"seed"}, "snapshot": {"snapshot"}, "run": {"model"}}.get(invocation.command)
        selected |= {graph.label(n) for n in nodes if kinds is None or n.kind in kinds}
    return selected
