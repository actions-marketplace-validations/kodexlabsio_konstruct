"""Build the graph from every input, then write graph.json, REPORT.md and graph.html."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kerplunk import __version__, airflow, dbt
from kerplunk.config import (
    Config,
    ConfigError,
    discover_dag_files,
    discover_sql_files,
    discover_warehouses,
)
from kerplunk.graph import RELATION_KINDS, Graph, Node, relation_key
from kerplunk.sql import PLACEHOLDER, StatementLineage, analyse, dialect_for, strip_templating
from kerplunk.warehouse import RelationMeta

log = logging.getLogger(__name__)

CACHE_VERSION = 1


class Cache:
    """Results of the expensive steps (SQL lineage, DAG parsing), keyed by a hash of their inputs."""

    def __init__(self, path: Path, enabled: bool) -> None:
        self.path = path
        self.entries: dict[str, Any] = {}
        self.used: dict[str, Any] = {}
        self.hits = 0
        self.misses = 0
        if enabled and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if data.get("version") == CACHE_VERSION and data.get("kerplunk") == __version__:
                self.entries = dict(data.get("entries", {}))

    @staticmethod
    def key(*parts: Any) -> str:
        blob = json.dumps(parts, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def get_or_compute(self, key: str, compute: Callable[[], Any]) -> Any:
        if key in self.entries:
            self.hits += 1
            value = self.entries[key]
        else:
            self.misses += 1
            value = compute()
        self.used[key] = value
        return value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": CACHE_VERSION, "kerplunk": __version__, "entries": self.used}
        self.path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


@dataclass
class BuildResult:
    graph: Graph
    outputs: list[Path]
    seconds: float
    cache_hits: int
    cache_misses: int
    inputs: dict[str, int] = field(default_factory=dict)


class RelationIndex:
    """Resolves relation keys written in SQL or asset URIs to graph nodes, allowing suffixes."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        self.by_suffix: dict[str, set[str]] = {}
        for node in graph.relations():
            self.add(node)

    def add(self, node: Node) -> None:
        if not node.relation:
            return
        parts = node.relation.split(".")
        for start in range(len(parts)):
            self.by_suffix.setdefault(".".join(parts[start:]), set()).add(node.relation)

    def find(self, key: str, *, drop_leading: bool = False) -> Node | None:
        parts = [p for p in key.lower().split(".") if p and p != PLACEHOLDER.lower()]
        while parts:
            matches = self.by_suffix.get(".".join(parts), set())
            if len(matches) == 1:
                return self.graph.node_for_relation(next(iter(matches)))
            if matches or not drop_leading or len(parts) <= 2:
                return None
            parts = parts[1:]
        return None


class Builder:
    def __init__(self, config: Config, *, update: bool) -> None:
        self.config = config
        self.graph = Graph()
        self.graph.meta = {
            "kerplunk_version": __version__,
            "root": str(config.root),
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.cache = Cache(config.out_dir / "cache.json", enabled=update)
        self.index = RelationIndex(self.graph)
        self.dialect: str | None = config.dialect
        self.default_catalog = config.default_catalog
        self.default_schema = config.default_schema
        self.inputs: dict[str, int] = {}
        # One entry per input, for the build summary and `kerplunk status`.
        self.sources: list[dict[str, str]] = []
        self.watched: set[str] = set()

    # Shared helpers

    def watch(self, *paths: Path) -> None:
        for path in paths:
            if path.exists():
                self.watched.add(self.rel_path(path))

    def rel_path(self, path: Path | str) -> str:
        return os.path.relpath(Path(path).resolve(), self.config.root)

    def relation(self, key: str, origin: str, *, drop_leading: bool = False) -> Node:
        """The node for a relation, creating an inferred ``table`` node if none exists."""
        found = self.index.find(key, drop_leading=drop_leading)
        if found is not None:
            if origin not in found.origins:
                found.origins.append(origin)
            return found
        normalised = relation_key(*[p for p in key.split(".") if p != PLACEHOLDER])
        existing = self.graph.node_for_relation(normalised)
        if existing is not None:
            return existing
        node = self.graph.add_node(
            Node(
                id=f"table.{normalised}",
                kind="table",
                name=normalised.rsplit(".", 1)[-1],
                relation=normalised,
                origins=[origin],
                attrs={"inferred": True},
            )
        )
        self.index.add(node)
        return node

    def column(self, parent: Node, name: str, origin: str) -> Node:
        existing = self.graph.column(parent.id, name)
        if existing is not None:
            return existing
        return self.graph.add_column(parent, name, origin=origin, attrs={"inferred": True})

    def schema_columns(self, nodes: Iterable[Node] | None = None) -> dict[str, dict[str, str]]:
        schema: dict[str, dict[str, str]] = {}
        for node in nodes if nodes is not None else self.graph.relations():
            if node.relation and node.kind in RELATION_KINDS:
                columns = {
                    c.name: c.data_type or "UNKNOWN"
                    for c in self.graph.columns_of(node.id)
                    if not c.attrs.get("nested") and not c.attrs.get("documented_only")
                }
                if columns:
                    schema[node.relation] = columns
        return schema

    def apply(
        self,
        statement: StatementLineage,
        *,
        target: Node | None,
        origin: str,
        label: str,
        via: str | None = None,
    ) -> None:
        """Turn one statement's lineage into feeds and derived_from edges."""
        attrs: dict[str, Any] = {"origin": origin}
        if via:
            attrs["via"] = via
        for error in statement.errors:
            self.graph.warn(f"{label}: {error}")
        if target is None and statement.target:
            target = self.relation(statement.target, origin)
        if target is None:
            return
        for read in statement.reads:
            source = self.relation(read, origin)
            self.graph.add_edge(source.id, target.id, "feeds", **attrs)
        for name, sources in statement.columns.items():
            out = self.column(target, name, origin)
            for item in sources:
                upstream = self.column(self.relation(item.relation, origin), item.column, origin)
                self.graph.add_edge(out.id, upstream.id, "derived_from", transform=item.transform, **attrs)
        for relation, column in statement.references:
            used = self.column(self.relation(relation, origin), column, origin)
            self.graph.add_edge(used.id, target.id, "feeds", role="reference", **attrs)

    def analyse_cached(
        self, sql: str, *, kind: str, schema_key: str, target: str | None = None
    ) -> list[StatementLineage]:
        key = Cache.key(
            kind, sql, self.dialect, self.default_catalog, self.default_schema, schema_key, target
        )

        def compute() -> list[dict[str, Any]]:
            return [
                s.to_dict()
                for s in analyse(
                    sql,
                    dialect=self.dialect,
                    schema=self.schema_columns(),
                    default_catalog=self.default_catalog,
                    default_db=self.default_schema,
                    target=target,
                )
            ]

        return [StatementLineage.from_dict(item) for item in self.cache.get_or_compute(key, compute)]

    # Steps

    def run(self) -> Graph:
        projects = self.load_dbt()
        self.load_warehouses(projects)
        self.link_shared_relations()
        self.warn_missing_catalogs()
        self.index = RelationIndex(self.graph)
        self.dbt_lineage()
        schema_key = Cache.key(self.schema_columns())
        self.warehouse_view_lineage(schema_key)
        used_sql = self.load_airflow(schema_key)
        self.load_sql_files(projects, used_sql, schema_key)
        self.graph.meta["dialect"] = self.dialect
        self.graph.meta["inputs"] = self.inputs
        self.graph.meta["sources"] = self.sources
        if self.config.config_file:
            self.watch(self.config.config_file)
        self.graph.meta["watched"] = sorted(self.watched)
        return self.graph

    def load_dbt(self) -> list[dbt.DbtProject]:
        if self.config.dbt_projects is not None:
            projects = [p for root in self.config.dbt_projects if (p := dbt.read_project(root))]
        else:
            projects = [p for p in dbt.find_projects(self.config.root) if not self.config.excluded(p.root)]
        self.loads: list[dbt.DbtLoad] = []
        for project in projects:
            status = dbt.ensure_artefacts(project, run_dbt=self.config.run_dbt, graph=self.graph)
            self.watch(project.root / "dbt_project.yml", *project.source_paths)
            loaded = dbt.load(project, self.graph, dialect=self.config.dialect)
            entry = {
                "input": "dbt",
                "name": project.name,
                "path": self.rel_path(project.root),
                "note": status,
            }
            self.sources.append(entry)
            if loaded is None:
                entry["status"] = "failed"
                continue
            self.watch(project.manifest_path, project.catalog_path)
            entry["detail"] = ", ".join(
                f"{count} {kind}{'s' if count != 1 else ''}" for kind, count in loaded.counts.items() if count
            )
            self.loads.append(loaded)
            self.dialect = self.dialect or loaded.dialect
            self.default_catalog = self.default_catalog or loaded.default_catalog
            self.default_schema = self.default_schema or loaded.default_schema
        self.inputs["dbt_projects"] = len(self.loads)
        return projects

    def load_warehouses(self, projects: list[dbt.DbtProject]) -> None:
        count = 0
        for warehouse in discover_warehouses(self.config, projects):
            try:
                connector = warehouse.connector(self.config.root)
                relations = connector.fetch()
            except ConfigError:
                raise
            except Exception as error:  # any driver error becomes a warning
                message = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
                self.graph.warn(f"warehouse {warehouse.describe(self.config.root)}: {message}")
                self.sources.append(
                    {
                        "input": warehouse.type,
                        "name": warehouse.describe(self.config.root),
                        "status": "failed",
                        "note": message,
                    }
                )
                continue
            count += 1
            self.sources.append(
                {
                    "input": warehouse.type,
                    "name": warehouse.describe(self.config.root),
                    "detail": f"{len(relations)} relation{'s' if len(relations) != 1 else ''}",
                    "note": warehouse.source,
                }
            )
            self.dialect = self.dialect or connector.dialect
            if self.default_schema is None and connector.default_schema and relations:
                # Unqualified tables in SQL files and DAGs then resolve inside this warehouse.
                self.default_catalog = self.default_catalog or relations[0].parts[0]
                self.default_schema = connector.default_schema
            for meta in relations:
                self.merge_relation(meta, connector.label)
        self.inputs["warehouses"] = count

    def merge_relation(self, meta: RelationMeta, origin: str) -> None:
        nodes = self.graph.nodes_for_relation(meta.key)
        if not nodes:
            self._merge_into(None, meta, origin)
        for node in nodes:
            self._merge_into(node, meta, origin)

    def _merge_into(self, node: Node | None, meta: RelationMeta, origin: str) -> None:
        attrs = {"table_type": meta.kind, **meta.attrs}
        if node is None:
            node = self.graph.add_node(
                Node(
                    id=f"table.{meta.key}",
                    kind="table",
                    name=meta.name,
                    relation=meta.key,
                    description=meta.comment,
                    origins=[origin],
                    attrs=attrs,
                )
            )
        else:
            if origin not in node.origins:
                node.origins.append(origin)
            node.description = node.description or meta.comment
            for key, value in attrs.items():
                node.attrs.setdefault(key, value)
            node.attrs.pop("inferred", None)
        node.attrs["in_warehouse"] = True
        if meta.view_sql and "dbt" not in node.origins:
            node.attrs["view_sql"] = meta.view_sql
        for column in meta.columns:
            existing = self.graph.column(node.id, column.name)
            if existing is None:
                self.graph.add_column(
                    node,
                    column.name,
                    data_type=column.data_type,
                    description=column.comment,
                    origin=origin,
                    attrs={**column.attrs, "in_warehouse": True},
                )
            else:
                existing.data_type = existing.data_type or column.data_type
                existing.description = existing.description or column.comment
                if origin not in existing.origins:
                    existing.origins.append(origin)
                existing.attrs.pop("documented_only", None)
                existing.attrs.pop("inferred", None)
                existing.attrs["in_warehouse"] = True
                for key, value in column.attrs.items():
                    existing.attrs.setdefault(key, value)

    def link_shared_relations(self) -> None:
        """Connect nodes that describe one physical relation, such as a seed and the dbt source
        that reads the table it loads: the producer feeds the reader, column by column."""
        for _, nodes in self.graph.shared_relations():
            reader = nodes[0]
            for producer in nodes[1:]:
                if producer.kind not in ("model", "snapshot", "seed") or reader.kind != "source":
                    continue
                self.graph.add_edge(producer.id, reader.id, "feeds", same_relation=True)
                for column in self.graph.columns_of(producer.id):
                    mirror = self.graph.column(reader.id, column.name)
                    if mirror is None:
                        mirror = self.graph.add_column(
                            reader,
                            column.name,
                            data_type=column.data_type,
                            description=column.description,
                            origin="dbt",
                            attrs={"from_relation": producer.id},
                        )
                    self.graph.add_edge(
                        mirror.id, column.id, "derived_from", transform="direct", same_relation=True
                    )

    def warn_missing_catalogs(self) -> None:
        for loaded in self.loads:
            if loaded.has_catalog:
                continue
            nodes = [
                n
                for n in self.graph.relations()
                if n.id.startswith(("model.", "source.", "seed.", "snapshot."))
            ]
            unseen = [n for n in nodes if not n.attrs.get("in_warehouse")]
            if unseen:
                self.graph.warn(
                    f"dbt: {loaded.project.name}: no catalog.json and {len(unseen)} dbt node(s) not found in "
                    "a warehouse, so their column types are unknown; run `dbt docs generate`"
                )

    def dbt_lineage(self) -> None:
        count = 0
        for loaded in self.loads:
            for model in _topological(loaded.models):
                node = self.graph.nodes[model.node_id]
                upstream = [self.graph.nodes[u] for u in model.upstream if u in self.graph.nodes]
                upstream_schema = self.schema_columns(upstream)
                checksum = node.attrs.get("checksum")
                key = Cache.key("dbt", model.sql, self.dialect, model.relation, upstream_schema, checksum)
                dialect = loaded.dialect or self.dialect

                def compute(
                    model: dbt.ModelSql = model,
                    schema: dict[str, Any] = upstream_schema,
                    dialect: str | None = dialect,
                    loaded: dbt.DbtLoad = loaded,
                ) -> Any:
                    return [
                        s.to_dict()
                        for s in analyse(
                            model.sql,
                            dialect=dialect,
                            schema=schema,
                            default_catalog=loaded.default_catalog,
                            default_db=loaded.default_schema,
                            target=model.relation,
                        )
                    ]

                statements = [StatementLineage.from_dict(s) for s in self.cache.get_or_compute(key, compute)]
                selects = [s for s in statements if s.kind in ("select", "unparsed")]
                if not model.compiled:
                    node.attrs["lineage"] = "from uncompiled SQL"
                for statement in selects[-1:]:
                    self.apply(statement, target=node, origin="dbt", label=f"dbt model {node.name}")
                count += 1
        self.inputs["dbt_models"] = count

    def warehouse_view_lineage(self, schema_key: str) -> None:
        for node in list(self.graph.relations()):
            view_sql = node.attrs.pop("view_sql", None)
            if not view_sql or not node.relation:
                continue
            parts = node.relation.split(".")
            saved = (self.default_catalog, self.default_schema)
            if len(parts) == 3:
                self.default_catalog, self.default_schema = parts[0], parts[1]
            try:
                statements = self.analyse_cached(
                    view_sql, kind="view", schema_key=schema_key, target=node.relation
                )
            finally:
                self.default_catalog, self.default_schema = saved
            for statement in statements:
                self.apply(statement, target=node, origin="warehouse", label=f"view {node.name}")

    def load_airflow(self, schema_key: str) -> set[Path]:
        used_sql: set[Path] = set()
        files = discover_dag_files(self.config)
        dags_seen = 0
        for path in files:
            self.watch(path)
            try:
                content = path.read_bytes()
            except OSError as error:
                self.graph.warn(f"airflow: cannot read {self.rel_path(path)}: {error}")
                self.sources.append({"input": "airflow", "path": self.rel_path(path), "status": "failed"})
                continue
            key = Cache.key("dag", hashlib.sha256(content).hexdigest(), str(path))

            def compute(path: Path = path) -> Any:
                try:
                    return [d.to_dict() for d in airflow.parse_file(path)]
                except SyntaxError as error:
                    return {"error": f"syntax error on line {error.lineno}: {error.msg}"}

            parsed = self.cache.get_or_compute(key, compute)
            if isinstance(parsed, dict):
                self.graph.warn(f"airflow: {self.rel_path(path)}: {parsed['error']}")
                self.sources.append(
                    {
                        "input": "airflow",
                        "path": self.rel_path(path),
                        "status": "failed",
                        "note": parsed["error"],
                    }
                )
                continue
            tasks = 0
            for item in parsed:
                dag = airflow.DagInfo.from_dict(item)
                for task in dag.tasks.values():
                    if task.sql_path and Path(task.sql_path).is_file():
                        task.sql = Path(task.sql_path).read_text(encoding="utf-8")
                        used_sql.add(Path(task.sql_path).resolve())
                        self.watch(Path(task.sql_path))
                self.add_dag(dag, schema_key)
                dags_seen += 1
                tasks += len(dag.tasks)
            self.sources.append(
                {
                    "input": "airflow",
                    "path": self.rel_path(path),
                    "detail": f"{len(parsed)} DAG{'s' if len(parsed) != 1 else ''}, {tasks} task{'s' if tasks != 1 else ''}",
                }
            )
        self.inputs["airflow_dags"] = dags_seen
        return used_sql

    def add_dag(self, dag: airflow.DagInfo, schema_key: str) -> None:
        path = self.rel_path(dag.path)
        dag_node = self.graph.add_node(
            Node(
                id=f"dag.{dag.dag_id}",
                kind="dag",
                name=dag.dag_id,
                description=dag.description,
                path=path,
                origins=["airflow"],
                attrs={
                    k: v
                    for k, v in {"schedule": dag.schedule, "tags": dag.tags, "line": dag.line}.items()
                    if v
                },
            )
        )
        for warning in dag.warnings:
            self.graph.warn(f"airflow: {path}: {warning}")
        for task in dag.tasks.values():
            task_node = self.graph.add_node(
                Node(
                    id=f"task.{dag.dag_id}.{task.task_id}",
                    kind="task",
                    name=task.task_id,
                    description=task.doc,
                    path=path,
                    origins=["airflow"],
                    attrs={
                        k: v
                        for k, v in {
                            "dag": dag.dag_id,
                            "operator": task.operator,
                            "line": task.line,
                            "bash_command": task.bash_command,
                            "sql_path": self.rel_path(task.sql_path) if task.sql_path else None,
                        }.items()
                        if v
                    },
                )
            )
            self.graph.add_edge(dag_node.id, task_node.id, "runs")
            self.task_dbt(task, task_node, path)
            if task.sql:
                label = f"airflow task {dag.dag_id}.{task.task_id}"
                sql = strip_templating(task.sql)
                for statement in self.analyse_cached(sql, kind="task", schema_key=schema_key):
                    self.task_statement(statement, task_node, label)
            for name in task.writes:
                self.graph.add_edge(
                    task_node.id, self.relation(name, "airflow").id, "feeds", origin="airflow"
                )
            for name in task.reads:
                self.graph.add_edge(
                    self.relation(name, "airflow").id, task_node.id, "feeds", origin="airflow"
                )
            for uri in task.outlets:
                self.graph.add_edge(task_node.id, self.asset(uri).id, "feeds", origin="airflow", asset=uri)
            for uri in task.inlets:
                self.graph.add_edge(self.asset(uri).id, task_node.id, "feeds", origin="airflow", asset=uri)
        for upstream, downstream in dag.dependencies:
            self.graph.add_edge(
                f"task.{dag.dag_id}.{upstream}", f"task.{dag.dag_id}.{downstream}", "precedes"
            )

    def task_dbt(self, task: airflow.TaskInfo, node: Node, path: str) -> None:
        for invocation in task.dbt:
            if invocation.unsupported:
                self.graph.warn(
                    f"airflow: {path}: task {task.task_id}: dbt selection not resolved statically: "
                    + " ".join(invocation.unsupported)
                )
            selected = dbt.select_nodes(self.graph, invocation.selectors, invocation.excludes)
            if invocation.command == "seed":
                selected = [n for n in selected if n.kind == "seed"]
            elif invocation.command == "snapshot":
                selected = [n for n in selected if n.kind == "snapshot"]
            elif invocation.command == "run":
                selected = [n for n in selected if n.kind == "model"]
            elif invocation.command == "test":
                selected = []
                tested = {
                    n.id for n in dbt.select_nodes(self.graph, invocation.selectors, invocation.excludes)
                }
                for test in (n for n in self.graph.nodes.values() if n.kind == "test"):
                    targets = {e.target.split("#")[0] for e in self.graph.out_edges(test.id, "tests")}
                    if targets & tested:
                        self.graph.add_edge(node.id, test.id, "runs", command="test")
            for target in selected:
                self.graph.add_edge(node.id, target.id, "runs", command=invocation.command)
            if invocation.selectors and not selected and invocation.command != "test":
                self.graph.warn(
                    f"airflow: {path}: task {task.task_id}: `dbt {invocation.command} --select "
                    f"{' '.join(invocation.selectors)}` matched no dbt nodes"
                )

    def task_statement(self, statement: StatementLineage, task: Node, label: str) -> None:
        for error in statement.errors:
            self.graph.warn(f"{label}: {error}")
        statement.errors = []
        if statement.target:
            target = self.relation(statement.target, "airflow")
            self.graph.add_edge(task.id, target.id, "feeds", origin="airflow")
            self.apply(statement, target=target, origin="airflow", label=label, via=task.id)
        for read in statement.reads:
            self.graph.add_edge(self.relation(read, "airflow").id, task.id, "feeds", origin="airflow")

    def asset(self, uri: str) -> Node:
        """An Airflow asset: a known relation if the URI names one, otherwise a table node for it."""
        _, _, rest = uri.partition("://")
        if rest:
            host, _, path = rest.partition("/")
            parts = [p for p in path.split("/") if p]
            key = ".".join(parts) if parts else host
            found = self.index.find(key, drop_leading=True) or self.index.find(f"{host.split(':')[0]}.{key}")
            if found is not None:
                return found
        node = self.graph.add_node(
            Node(id=f"table.asset:{uri}", kind="table", name=uri, origins=["airflow"], attrs={"asset": uri})
        )
        return node

    def load_sql_files(self, projects: list[dbt.DbtProject], used: set[Path], schema_key: str) -> None:
        files = discover_sql_files(self.config, projects, used)
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as error:
                self.graph.warn(f"sql: cannot read {self.rel_path(path)}: {error}")
                continue
            rel = self.rel_path(path)
            self.watch(path)
            statements = self.analyse_cached(strip_templating(text), kind="file", schema_key=schema_key)
            writes = sorted({s.target for s in statements if s.target})
            self.sources.append(
                {
                    "input": "sql",
                    "path": rel,
                    "detail": f"writes {', '.join(writes)}" if writes else "writes nothing",
                }
            )
            for statement in statements:
                if not statement.target:
                    for problem in statement.errors:
                        self.graph.warn(f"sql {rel}: {problem}")
                    continue
                target = self.relation(statement.target, "sql")
                target.path = target.path or rel
                self.apply(statement, target=target, origin="sql", label=f"sql {rel}", via=rel)
        self.inputs["sql_files"] = len(files)


def changed_inputs(graph_path: Path, graph: Graph) -> list[str]:
    """Files the last build read that have changed since it ran, relative to the project root."""
    try:
        built = graph_path.stat().st_mtime
    except OSError:
        return []
    root = Path(str(graph.meta.get("root", graph_path.parent.parent)))
    changed = []
    for name in graph.meta.get("watched", []):
        path = root / name
        candidates = path.rglob("*") if path.is_dir() else [path]
        for candidate in candidates:
            try:
                if candidate.is_file() and candidate.stat().st_mtime > built:
                    changed.append(os.path.relpath(candidate, root))
            except OSError:
                continue
    return sorted(set(changed))


def _topological(models: list[dbt.ModelSql]) -> list[dbt.ModelSql]:
    by_id = {m.node_id: m for m in models}
    ordered: list[dbt.ModelSql] = []
    state: dict[str, int] = {}

    def visit(model: dbt.ModelSql) -> None:
        stack: list[tuple[dbt.ModelSql, int]] = [(model, 0)]
        while stack:
            current, index = stack.pop()
            if index == 0:
                if state.get(current.node_id):
                    continue
                state[current.node_id] = 1
            deps = [by_id[u] for u in current.upstream if u in by_id]
            if index < len(deps):
                stack.append((current, index + 1))
                if not state.get(deps[index].node_id):
                    stack.append((deps[index], 0))
                continue
            state[current.node_id] = 2
            ordered.append(current)

    for model in models:
        visit(model)
    return ordered


def build(config: Config, *, update: bool = False) -> BuildResult:
    from kerplunk.html import write_html
    from kerplunk.report import write_report

    started = time.perf_counter()
    builder = Builder(config, update=update)
    graph = builder.run()
    graph.meta["dialect"] = builder.dialect or dialect_for(None)
    config.out_dir.mkdir(parents=True, exist_ok=True)
    graph_path = config.out_dir / "graph.json"
    graph.meta["build_seconds"] = round(time.perf_counter() - started, 2)
    graph.write(graph_path)
    outputs = [
        graph_path,
        write_report(graph, config.out_dir / "REPORT.md"),
        write_html(graph, config.out_dir / "graph.html"),
    ]
    builder.cache.save()
    return BuildResult(
        graph=graph,
        outputs=outputs,
        seconds=time.perf_counter() - started,
        cache_hits=builder.cache.hits,
        cache_misses=builder.cache.misses,
        inputs=builder.inputs,
    )
