"""`konstruct verify`: test the static lineage against the data, by perturbation.

Static column lineage is a claim about SQL. This module checks the claim empirically, the way
perturbation-based provenance does: for every model built in a local DuckDB warehouse it copies the
model's inputs, perturbs one input column at a time (shifting values, nulling them, shuffling them
between rows with a seeded permutation), reruns the model's SQL, and records which output columns
change. Comparing what changed with what the graph claims gives, per model and overall:

* confirmed: claimed edges the data shows to be real;
* not exercised: claimed edges no perturbation moved (often genuine, but masked by the data, such
  as the second argument of a COALESCE whose first argument is never null);
* through joins and filters: influences the graph predicted as references (JOIN, WHERE, GROUP BY);
* unexplained: influences the graph did not predict at all, which point at gaps in the static
  analysis.

Everything is seeded, so a run can be reproduced exactly.
"""

from __future__ import annotations

import json
import random
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from konstruct.graph import Graph, Node
from konstruct.sql import strip_templating, table_key

STRATEGIES = ("shift", "null", "shuffle")
_TEXT = ("VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR", "CHARACTER VARYING", "CHARACTER")
_NUMERIC = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "REAL",
    "DOUBLE",
    "DECIMAL",
    "NUMERIC",
)


@dataclass
class ModelResult:
    node_id: str
    label: str
    rows: int = 0
    claimed: int = 0
    confirmed: list[tuple[str, str]] = field(default_factory=list)
    not_exercised: list[tuple[str, str]] = field(default_factory=list)
    through_references: list[tuple[str, str]] = field(default_factory=list)
    unexplained: list[tuple[str, str]] = field(default_factory=list)
    skipped: str | None = None
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("confirmed", "not_exercised", "through_references", "unexplained"):
            data[key] = [{"output": o, "input": i} for o, i in data[key]]
        return data


@dataclass
class Verification:
    warehouse: str
    seed: int
    max_rows: int
    strategies: tuple[str, ...]
    models: list[ModelResult] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def checked(self) -> list[ModelResult]:
        return [m for m in self.models if not m.skipped]

    def totals(self) -> dict[str, int]:
        checked = self.checked
        return {
            "models": len(checked),
            "skipped": len(self.models) - len(checked),
            "claimed": sum(m.claimed for m in checked),
            "confirmed": sum(len(m.confirmed) for m in checked),
            "not_exercised": sum(len(m.not_exercised) for m in checked),
            "through_references": sum(len(m.through_references) for m in checked),
            "unexplained": sum(len(m.unexplained) for m in checked),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "warehouse": self.warehouse,
            "seed": self.seed,
            "max_rows": self.max_rows,
            "strategies": list(self.strategies),
            "seconds": round(self.seconds, 2),
            "totals": self.totals(),
            "models": [m.to_dict() for m in self.models],
        }

    def summary(self) -> str:
        t = self.totals()
        if not t["models"]:
            return "No model could be verified."
        share = 100 * t["confirmed"] / t["claimed"] if t["claimed"] else 100.0
        lines = [
            f"{t['confirmed']} of {t['claimed']} claimed column edges confirmed by perturbation ({share:.0f}%) "
            f"across {t['models']} model{'s' if t['models'] != 1 else ''}.",
            f"{t['through_references']} influence{'s' if t['through_references'] != 1 else ''} through joins, "
            f"filters or grouping, all predicted as references; {t['unexplained']} unexplained.",
        ]
        if t["not_exercised"]:
            lines.append(
                f"{t['not_exercised']} claimed edge{'s' if t['not_exercised'] != 1 else ''} not exercised by the data "
                "(no perturbation changed the output; see VERIFY.md)."
            )
        return "\n".join(lines)

    def __str__(self) -> str:
        header = (
            f"Verified against {self.warehouse} (seed {self.seed}, strategies {', '.join(self.strategies)}, "
            f"up to {self.max_rows} rows per input)"
        )
        width = max([len(m.label) for m in self.models] + [5])
        rows = [
            f"  {'model'.ljust(width)}  claimed  confirmed  not exercised  via joins/filters  unexplained",
        ]
        for m in self.models:
            if m.skipped:
                rows.append(f"  {m.label.ljust(width)}  skipped: {m.skipped}")
                continue
            rows.append(
                f"  {m.label.ljust(width)}  {m.claimed:>7}  {len(m.confirmed):>9}  {len(m.not_exercised):>13}  "
                f"{len(m.through_references):>17}  {len(m.unexplained):>11}"
            )
        details = []
        for m in self.checked:
            for kind, items in (("unexplained", m.unexplained), ("not exercised", m.not_exercised)):
                for output, source in items:
                    details.append(f"  {m.label}.{output} <- {source}  ({kind})")
        text = [header, "", *rows, "", self.summary()]
        if details:
            text += ["", *details[:40]]
            if len(details) > 40:
                text.append(f"  and {len(details) - 40} more in VERIFY.md")
        return "\n".join(text)

    def markdown(self) -> str:
        t = self.totals()
        lines = [
            "# konstruct lineage verification",
            "",
            f"Warehouse `{self.warehouse}`, seed {self.seed}, strategies {', '.join(self.strategies)}, "
            f"up to {self.max_rows} rows per input, {self.seconds:.1f}s.",
            "",
            self.summary(),
            "",
            "Each input column of each model was perturbed in turn (shifted, nulled and shuffled between rows), "
            "the model's SQL was rerun on the perturbed copy, and every output column whose values changed was "
            "recorded. *Confirmed* edges are static lineage the data shows to be real. *Not exercised* edges "
            "were claimed but no perturbation changed the output on this data. *Via joins/filters* are changes "
            "caused by columns the static analysis recorded as join, filter or grouping references. "
            "*Unexplained* changes were not predicted by the static analysis at all.",
            "",
            "| Model | Rows | Claimed | Confirmed | Not exercised | Via joins/filters | Unexplained |",
            "|-------|------|---------|-----------|---------------|-------------------|-------------|",
        ]
        for m in self.models:
            if m.skipped:
                lines.append(f"| `{m.label}` | | | | | | skipped: {m.skipped} |")
            else:
                lines.append(
                    f"| `{m.label}` | {m.rows} | {m.claimed} | {len(m.confirmed)} | {len(m.not_exercised)} | "
                    f"{len(m.through_references)} | {len(m.unexplained)} |"
                )
        lines.append(
            f"| **Total** | | **{t['claimed']}** | **{t['confirmed']}** | **{t['not_exercised']}** | "
            f"**{t['through_references']}** | **{t['unexplained']}** |"
        )
        for title, key in (
            ("Unexplained influences", "unexplained"),
            ("Claimed but not exercised", "not_exercised"),
        ):
            items = [(m.label, o, s) for m in self.checked for o, s in getattr(m, key)]
            lines += ["", f"## {title}", ""]
            lines += [f"- `{label}.{o}` <- `{s}`" for label, o, s in items] or ["None."]
        return "\n".join(lines) + "\n"


# Candidates: models (and SQL-built tables) with SQL and a relation in the warehouse


@dataclass
class Candidate:
    node: Node
    sql: str
    default_catalog: str | None
    default_schema: str | None


def candidates(graph: Graph, root: Path) -> list[Candidate]:
    """Nodes whose defining SELECT konstruct can rerun: dbt models from the manifest, and tables
    built by a SQL file or an Airflow SQL task."""
    found: list[Candidate] = []
    manifests: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("target/manifest.json")):
        if "dbt_packages" in path.parts:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        manifests.update(
            {k: v for k, v in data.get("nodes", {}).items() if v.get("resource_type") == "model"}
        )
    for node in sorted(graph.relations(), key=lambda n: n.id):
        item = manifests.get(node.id)
        if item and item.get("compiled_code") and node.kind == "model":
            found.append(Candidate(node, item["compiled_code"], item.get("database"), item.get("schema")))
            continue
        if node.kind != "table" or not node.relation:
            continue
        sql_file = defining_sql_file(graph, node, root)
        if sql_file is None:
            continue
        query = _query_for(sql_file.read_text(encoding="utf-8"), node.relation, graph)
        if query is not None:
            catalog, schema = _defaults(graph)
            found.append(Candidate(node, query, catalog, schema))
    return found


def _defaults(graph: Graph) -> tuple[str | None, str | None]:
    if graph.meta.get("default_schema"):
        return graph.meta.get("default_catalog"), graph.meta.get("default_schema")
    relations = [n.relation for n in graph.relations() if n.kind == "model" and n.relation]
    parts = Counter(tuple(r.split(".")[:2]) for r in relations if r and r.count(".") == 2)
    if parts:
        catalog, schema = parts.most_common(1)[0][0]
        return catalog, schema
    return None, None


def defining_sql_file(graph: Graph, node: Node, root: Path) -> Path | None:
    for edge in graph.in_edges(node.id, "feeds"):
        source = graph.nodes.get(edge.source)
        if source is not None and source.kind == "task" and source.attrs.get("sql_path"):
            return root / str(source.attrs["sql_path"])
    if node.path and node.path.endswith(".sql"):
        return root / node.path
    return None


def _query_for(text: str, relation: str, graph: Graph) -> str | None:
    catalog, schema = _defaults(graph)
    try:
        statements = sqlglot.parse(strip_templating(text), dialect="duckdb")
    except SqlglotError:
        return None
    for statement in statements:
        if isinstance(statement, (exp.Create, exp.Insert)) and isinstance(statement.expression, exp.Query):
            this = statement.this
            table = this.this if isinstance(this, exp.Schema) else this
            if isinstance(table, exp.Table) and table_key(table, catalog, schema) == relation:
                return statement.expression.sql(dialect="duckdb")
    return None


# Claims from the graph


def claims(graph: Graph, node: Node) -> tuple[set[tuple[str, str]], set[str]]:
    """The static claims for one node: (output column, input relation.column) value edges, and the
    input columns it references in joins, filters and grouping."""
    value: set[tuple[str, str]] = set()
    for column in graph.columns_of(node.id):
        for edge in graph.out_edges(column.id, "derived_from"):
            if edge.attrs.get("same_relation"):
                continue
            upstream = graph.nodes.get(edge.target)
            parent = graph.parent_of(upstream) if upstream else None
            if upstream is not None and parent is not None and parent.relation:
                value.add((column.name.lower(), f"{parent.relation}.{upstream.name.lower()}"))
    references = set()
    for edge in graph.in_edges(node.id, "feeds"):
        if edge.attrs.get("role") != "reference":
            continue
        used = graph.nodes.get(edge.source)
        parent = graph.parent_of(used) if used else None
        if used is not None and parent is not None and parent.relation:
            references.add(f"{parent.relation}.{used.name.lower()}")
    return value, references


# Running


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _rewrite(
    sql: str, mapping: dict[str, str], catalog: str | None, schema: str | None, dialect: str = "duckdb"
) -> tuple[str, set[str]]:
    """Point every input table at a scratch copy; return the SQL and the relations it reads."""
    tree = sqlglot.parse_one(sql, dialect=dialect)
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    seen: set[str] = set()

    def replace(node: exp.Expr) -> exp.Expr:
        if not isinstance(node, exp.Table) or not node.name or (not node.db and node.name.lower() in ctes):
            return node
        key = table_key(node, catalog, schema)
        seen.add(key)
        if key not in mapping:
            return node
        alias = node.alias or node.name
        return exp.alias_(exp.to_table(mapping[key]), alias, table=True)

    return tree.transform(replace).sql(dialect=dialect), seen


def _run(con: Any, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    cursor = con.execute(sql)
    names = [d[0].lower() for d in cursor.description]
    return names, cursor.fetchall()


def _signature(rows: list[tuple[Any, ...]], index: int) -> Counter[str]:
    return Counter(repr(row[index]) for row in rows)


def _via_parent(output: str, source: str, influence: set[tuple[str, str]]) -> bool:
    """Whether perturbing a struct column that contains ``source`` (``db.schema.table.column.field``)
    changed ``output``."""
    parts = source.split(".")
    return any((output, ".".join(parts[:end])) in influence for end in range(4, len(parts)))


def _paired(rows: list[tuple[Any, ...]], index: int, anchors: list[int]) -> Counter[str]:
    return Counter(repr((tuple(row[i] for i in anchors), row[index])) for row in rows)


def _mutation(
    reference: str, dtype: str, strategy: str, rng: random.Random, cast: str = "TRY_CAST"
) -> str | None:
    """A perturbed value for ``reference`` (a quoted column or struct field) of type ``dtype``."""
    base = dtype.upper().split("(")[0].strip()
    if strategy == "null":
        return f"CAST(NULL AS {dtype})"
    if strategy == "shift":
        if base in _NUMERIC:
            return f"{cast}({reference} * {rng.randint(2, 9)} + {rng.randint(1, 97)} AS {dtype})"
        if base in _TEXT:
            return f"{reference} || '~{rng.randint(10, 99)}'"
        if base == "DATE":
            return f"CAST({reference} + INTERVAL '{rng.randint(3, 400)} days' AS DATE)"
        if base.startswith("TIMESTAMP"):
            return f"{reference} + INTERVAL '{rng.randint(1, 300)} hours'"
        if base == "BOOLEAN":
            return f"NOT {reference}"
        return None
    return None


def _targets(column: str, dtype: str) -> list[tuple[tuple[str, ...], str]]:
    """What to perturb in one input column: the column, and each field reachable through structs
    alone (fields inside list elements are covered by perturbing the list itself)."""
    from sqlglot import exp
    from sqlglot.errors import SqlglotError

    targets: list[tuple[tuple[str, ...], str]] = [((column,), dtype)]
    if not dtype.upper().startswith("STRUCT"):
        return targets
    try:
        built = exp.DataType.build(dtype, dialect="duckdb")
    except (SqlglotError, ValueError):
        return targets

    def walk(path: tuple[str, ...], node: exp.DataType) -> None:
        if node.this != exp.DataType.Type.STRUCT:
            return
        for field_def in node.expressions:
            if isinstance(field_def, exp.ColumnDef) and isinstance(field_def.kind, exp.DataType):
                child = (*path, field_def.name)
                targets.append((child, field_def.kind.sql(dialect="duckdb")))
                walk(child, field_def.kind)

    walk((column,), built)
    return targets


def _reference(path: tuple[str, ...], prefix: str = "") -> str:
    return prefix + ".".join(_quote(p) for p in path)


def _rebuild(path: tuple[str, ...], value: str, prefix: str = "") -> str:
    """The top-level column of ``path`` with only the field at ``path`` replaced by ``value``."""
    if len(path) == 1:
        return value
    inner = _rebuild(path[1:], value, prefix=f"{_reference(path[:1], prefix)}.") if len(path) > 2 else value
    return f"struct_update({_reference(path[:1], prefix)}, {_quote(path[1])} := {inner})"


def _overlaps(source: str, other: str) -> bool:
    return source == other or other.startswith(source + ".") or source.startswith(other + ".")


class Engine:
    """Where perturbed copies of a model's inputs live and its SQL is rerun."""

    dialect = "duckdb"
    label = ""

    def start(self) -> None:
        """Clear scratch tables before the next model."""

    def copy(self, key: str, index: int, max_rows: int) -> tuple[str, list[tuple[str, str]]]:
        raise NotImplementedError

    def mutate(
        self,
        scratch: str,
        columns: list[tuple[str, str]],
        path: tuple[str, ...],
        dtype: str,
        strategy: str,
        rng: random.Random,
    ) -> str | None:
        raise NotImplementedError

    def run(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        raise NotImplementedError

    def close(self) -> None:
        """Release the connection."""


class DuckDBEngine(Engine):
    """An in-memory DuckDB with the warehouse file attached read-only."""

    dialect = "duckdb"

    def __init__(self, warehouse: Path) -> None:
        import duckdb

        self.label = str(warehouse)
        self.con = duckdb.connect()
        self.con.execute(f"ATTACH {_literal(str(warehouse))} (READ_ONLY)")
        row = self.con.execute(
            "SELECT database_name FROM duckdb_databases() WHERE path = ?", [str(warehouse.resolve())]
        ).fetchone()
        self.database = (row[0] if row else warehouse.stem).lower()

    def start(self) -> None:
        self.con.execute("DROP SCHEMA IF EXISTS kp CASCADE")
        self.con.execute("CREATE SCHEMA kp")

    def copy(self, key: str, index: int, max_rows: int) -> tuple[str, list[tuple[str, str]]]:
        parts = key.split(".")
        if len(parts) != 3 or parts[0] != self.database:
            raise LookupError(f"input {key} is not in the warehouse {self.database}")
        scratch = f"kp.in{index}"
        source = ".".join(_quote(p) for p in parts)
        self.con.execute(f"CREATE TABLE {scratch} AS SELECT * FROM {source} LIMIT {int(max_rows)}")
        return scratch, [(row[0], row[1]) for row in self.con.execute(f"DESCRIBE {scratch}").fetchall()]

    def mutate(
        self,
        scratch: str,
        columns: list[tuple[str, str]],
        path: tuple[str, ...],
        dtype: str,
        strategy: str,
        rng: random.Random,
    ) -> str | None:
        quoted = _quote(path[0])
        if strategy == "shuffle":
            salt = rng.randint(1, 2**31)
            self.con.execute(
                f"""
                CREATE OR REPLACE TABLE kp.mut AS
                WITH base AS (SELECT *, row_number() OVER () AS __kp_rn FROM {scratch}),
                perm AS (
                    SELECT __kp_v, row_number() OVER (ORDER BY hash(__kp_i, {salt})) AS __kp_rn
                    FROM (SELECT {_reference(path)} AS __kp_v, row_number() OVER () AS __kp_i FROM {scratch})
                )
                SELECT base.* EXCLUDE (__kp_rn) REPLACE ({_rebuild(path, "perm.__kp_v", prefix="base.")} AS {quoted})
                FROM base JOIN perm USING (__kp_rn)
                """
            )
            return "kp.mut"
        expression = _mutation(_reference(path), dtype, strategy, rng)
        if expression is None:
            return None
        self.con.execute(
            f"CREATE OR REPLACE TABLE kp.mut AS SELECT * REPLACE ({_rebuild(path, expression)} AS {quoted}) "
            f"FROM {scratch}"
        )
        return "kp.mut"

    def run(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        return _run(self.con, sql)

    def close(self) -> None:
        self.con.close()


class PostgresEngine(Engine):
    """Temporary tables in one transaction that is always rolled back, so nothing persists."""

    dialect = "postgres"

    def __init__(self, dsn: str) -> None:
        import psycopg

        self.label = "postgres"
        self.con = psycopg.connect(dsn)
        row = self.con.execute("SELECT current_database()").fetchone()
        self.database = str(row[0]).lower() if row else ""
        self.label = f"postgres database {self.database}"
        self.tables: list[str] = []

    def _savepoint(self, sql: str) -> Any:
        self.con.execute("SAVEPOINT kp")
        try:
            cursor = self.con.execute(sql)
        except Exception:
            self.con.execute("ROLLBACK TO SAVEPOINT kp")
            raise
        self.con.execute("RELEASE SAVEPOINT kp")
        return cursor

    def start(self) -> None:
        for table in self.tables:
            self.con.execute(f"DROP TABLE IF EXISTS {table}")
        self.tables = []

    def _columns(self, table: str) -> list[tuple[str, str]]:
        rows = self.con.execute(
            "SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
            [table],
        ).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    def copy(self, key: str, index: int, max_rows: int) -> tuple[str, list[tuple[str, str]]]:
        parts = key.split(".")
        if len(parts) != 3 or parts[0] != self.database:
            raise LookupError(f"input {key} is not in the database {self.database}")
        scratch = f"kp_in{index}"
        source = ".".join(_quote(p) for p in parts[1:])
        self._savepoint(f"CREATE TEMP TABLE {scratch} AS SELECT * FROM {source} LIMIT {int(max_rows)}")
        self.tables.append(scratch)
        return scratch, self._columns(scratch)

    def mutate(
        self,
        scratch: str,
        columns: list[tuple[str, str]],
        path: tuple[str, ...],
        dtype: str,
        strategy: str,
        rng: random.Random,
    ) -> str | None:
        if len(path) > 1:
            return None
        column = path[0]
        self.con.execute("DROP TABLE IF EXISTS kp_mut")
        if "kp_mut" not in self.tables:
            self.tables.append("kp_mut")
        if strategy == "shuffle":
            salt = rng.randint(1, 2**31)
            listed = ", ".join(
                f"perm.__kp_v AS {_quote(name)}" if name == column else f"base.{_quote(name)}"
                for name, _ in columns
            )
            sql = (
                f"CREATE TEMP TABLE kp_mut AS WITH base AS (SELECT *, row_number() OVER () AS __kp_rn FROM {scratch}), "
                f"perm AS (SELECT __kp_v, row_number() OVER (ORDER BY md5(__kp_i::text || '{salt}')) AS __kp_rn "
                f"FROM (SELECT {_quote(column)} AS __kp_v, row_number() OVER () AS __kp_i FROM {scratch}) AS x) "
                f"SELECT {listed} FROM base JOIN perm USING (__kp_rn)"
            )
        else:
            expression = _mutation(_quote(column), dtype, strategy, rng, cast="CAST")
            if expression is None:
                return None
            listed = ", ".join(
                f"{expression} AS {_quote(name)}" if name == column else _quote(name) for name, _ in columns
            )
            sql = f"CREATE TEMP TABLE kp_mut AS SELECT {listed} FROM {scratch}"
        try:
            self._savepoint(sql)
        except Exception:  # a value the type cannot hold (an overflow, say): try the next perturbation
            return None
        return "kp_mut"

    def run(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        cursor = self._savepoint(sql)
        names = [d.name.lower() for d in cursor.description or []]
        return names, list(cursor.fetchall())

    def close(self) -> None:
        self.con.rollback()
        self.con.close()


def engine_for(warehouse: str | Path) -> Engine:
    """A Postgres connection string or URL, or a DuckDB file."""
    text = str(warehouse)
    if text.startswith(("postgres://", "postgresql://")) or "host=" in text or "dbname=" in text:
        return PostgresEngine(text)
    return DuckDBEngine(Path(text))


def verify(
    graph: Graph,
    root: Path,
    warehouse: str | Path,
    *,
    seed: int = 1729,
    max_rows: int = 10000,
    strategies: tuple[str, ...] = STRATEGIES,
    only: list[str] | None = None,
) -> Verification:
    started = time.perf_counter()
    engine = engine_for(warehouse)
    result = Verification(engine.label, seed, max_rows, strategies)
    try:
        for candidate in candidates(graph, root):
            node = candidate.node
            if only and not any(name in (node.name, node.id, graph.label(node)) for name in only):
                continue
            model = ModelResult(node.id, graph.label(node))
            result.models.append(model)
            timer = time.perf_counter()
            try:
                _verify_one(engine, graph, candidate, model, seed, max_rows, strategies)
            except Exception as error:  # a model that will not run is reported, not fatal
                model.skipped = f"{type(error).__name__}: {str(error).strip().splitlines()[0][:160]}"
            model.seconds = round(time.perf_counter() - timer, 3)
    finally:
        engine.close()
    result.seconds = time.perf_counter() - started
    return result


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _verify_one(
    engine: Engine,
    graph: Graph,
    candidate: Candidate,
    model: ModelResult,
    seed: int,
    max_rows: int,
    strategies: tuple[str, ...],
) -> None:
    node = candidate.node
    claimed, references = claims(graph, node)
    dialect = engine.dialect
    _, inputs = _rewrite(candidate.sql, {}, candidate.default_catalog, candidate.default_schema, dialect)
    mapping: dict[str, str] = {}
    columns: dict[str, list[tuple[str, str]]] = {}
    engine.start()
    for index, key in enumerate(sorted(inputs)):
        mapping[key], columns[key] = engine.copy(key, index, max_rows)
    query, _ = _rewrite(candidate.sql, mapping, candidate.default_catalog, candidate.default_schema, dialect)
    names, baseline = engine.run(query)
    _, again = engine.run(query)
    if Counter(map(repr, baseline)) != Counter(map(repr, again)):
        raise RuntimeError("the model is not deterministic, so changes cannot be attributed")
    model.rows = len(baseline)
    if not baseline:
        raise RuntimeError("the model returns no rows on its inputs")
    base_signatures = [_signature(baseline, i) for i in range(len(names))]

    value_influence: set[tuple[str, str]] = set()
    row_influence: set[str] = set()
    rng = random.Random(seed)
    for key in sorted(columns):
        for column, dtype in columns[key]:
            for path, field_type in _targets(column, dtype):
                source_column = f"{key}.{'.'.join(path).lower()}"
                for strategy in strategies:
                    try:
                        mutated = engine.mutate(mapping[key], columns[key], path, field_type, strategy, rng)
                    except Exception:  # a perturbation the engine cannot build tells us nothing
                        continue
                    if mutated is None:
                        continue
                    perturbed, _ = _rewrite(
                        candidate.sql,
                        {**mapping, key: mutated},
                        candidate.default_catalog,
                        candidate.default_schema,
                        dialect,
                    )
                    try:
                        _, rows = engine.run(perturbed)
                    except Exception:  # a perturbation the SQL cannot accept tells us nothing
                        continue
                    _observe(
                        rows,
                        names,
                        baseline,
                        base_signatures,
                        claimed,
                        source_column,
                        value_influence,
                        row_influence,
                    )
    _classify(model, names, claimed, references, value_influence, row_influence)


def _classify(
    model: ModelResult,
    names: list[str],
    claimed: set[tuple[str, str]],
    references: set[str],
    value_influence: set[tuple[str, str]],
    row_influence: set[str],
) -> None:
    outputs = set(names)
    for output, source in sorted(claimed):
        if output not in outputs:
            continue
        # Only a change in values confirms an edge: a row-count change cannot be pinned on one column.
        if (output, source) in value_influence or _via_parent(output, source, value_influence):
            model.confirmed.append((output, source))
        else:
            model.not_exercised.append((output, source))
    model.claimed = len(model.confirmed) + len(model.not_exercised)
    for output, source in sorted(value_influence - claimed):
        # Perturbing a struct column moves its fields; a claim on one of those fields explains it.
        if any(o == output and s.startswith(source + ".") for o, s in claimed):
            continue
        if source in references or any(r.startswith(source + ".") for r in references):
            model.through_references.append((output, source))
        else:
            model.unexplained.append((output, source))
    expected = references | {s for _, s in claimed}
    for source in sorted(row_influence):
        if not any(_overlaps(source, other) for other in expected):
            model.unexplained.append(("(row count)", source))


def _observe(
    rows: list[tuple[Any, ...]],
    names: list[str],
    baseline: list[tuple[Any, ...]],
    base_signatures: list[Counter[str]],
    claimed: set[tuple[str, str]],
    source_column: str,
    value_influence: set[tuple[str, str]],
    row_influence: set[str],
) -> None:
    """Record which outputs changed between the baseline and a perturbed run."""
    claimed_on = {
        i
        for i, name in enumerate(names)
        if any(o == name and _overlaps(source_column, c) for o, c in claimed)
    }
    row_id = _row_key(baseline, rows, [i for i in range(len(names)) if i not in claimed_on])
    if row_id is not None:
        # Rows identified by an output column that does not depend on this input: compare each
        # surviving row's values directly, which works even when the perturbation adds or drops rows.
        before = {row[row_id]: row for row in baseline}
        after = {row[row_id]: row for row in rows}
        for identity in before.keys() & after.keys():
            for index in range(len(names)):
                if before[identity][index] != after[identity][index] and not _both_nan(
                    before[identity][index], after[identity][index]
                ):
                    value_influence.add((names[index], source_column))
    if len(rows) != len(baseline):
        row_influence.add(source_column)
        return
    changed = {i for i in range(len(names)) if _signature(rows, i) != base_signatures[i]}
    value_influence.update((names[i], source_column) for i in changed)
    # Values that only moved between rows (a ROW_NUMBER ordered by the perturbed column keeps
    # 1..n) leave each column's multiset unchanged. Pair each such column with the columns claimed
    # not to depend on the input; if those pairs change, it moved.
    independent = [
        i
        for i in range(len(names))
        if i not in changed and not any(o == names[i] and _overlaps(source_column, s) for o, s in claimed)
    ]
    for index in range(len(names)):
        if index in changed:
            continue
        anchors = [i for i in independent if i != index]
        if anchors and _paired(rows, index, anchors) != _paired(baseline, index, anchors):
            value_influence.add((names[index], source_column))


def _row_key(
    baseline: list[tuple[Any, ...]], rows: list[tuple[Any, ...]], candidates: list[int]
) -> int | None:
    """An output column that identifies rows uniquely in both runs, if there is one."""
    for index in candidates:
        values = [row[index] for row in baseline]
        if None in values or len(set(values)) != len(values):
            continue
        perturbed = [row[index] for row in rows]
        if len(set(perturbed)) == len(perturbed) and set(perturbed) & set(values):
            return index
    return None


def _both_nan(a: Any, b: Any) -> bool:
    return isinstance(a, float) and isinstance(b, float) and a != a and b != b


def annotate(graph: Graph, verification: Verification) -> None:
    """Record on each derived_from edge whether perturbation confirmed it."""
    states: dict[tuple[str, str, str], str] = {}
    for model in verification.checked:
        for state, items in (("confirmed", model.confirmed), ("not exercised", model.not_exercised)):
            for output, source in items:
                states[(model.node_id, output, source)] = state
    for model in verification.checked:
        node = graph.nodes.get(model.node_id)
        if node is None:
            continue
        for column in graph.columns_of(node.id):
            for edge in graph.out_edges(column.id, "derived_from"):
                upstream = graph.nodes.get(edge.target)
                parent = graph.parent_of(upstream) if upstream else None
                if upstream is None or parent is None or not parent.relation:
                    continue
                found = states.get(
                    (node.id, column.name.lower(), f"{parent.relation}.{upstream.name.lower()}")
                )
                if found:
                    edge.attrs["verified"] = found
    graph.meta["verification"] = {"seed": verification.seed, **verification.totals()}
