"""Column-level lineage for SQL statements, built on sqlglot.

`analyse()` takes one SQL string (one or more statements) and returns, per statement, the
relation it writes (if any), the relations it reads, the physical columns each output column
is derived from, and the columns it only joins, filters or groups on.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import Node as LineageNode
from sqlglot.lineage import lineage, to_node
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope
from sqlglot.schema import MappingSchema

from kerplunk.graph import relation_key

Transform = Literal["direct", "transformed"]

# dbt adapter types (and common aliases) to sqlglot dialect names.
DIALECTS: dict[str, str] = {
    "duckdb": "duckdb",
    "postgres": "postgres",
    "postgresql": "postgres",
    "redshift": "redshift",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "databricks": "databricks",
    "spark": "spark",
    "trino": "trino",
    "athena": "athena",
    "sqlserver": "tsql",
    "synapse": "tsql",
    "fabric": "tsql",
    "clickhouse": "clickhouse",
    "mysql": "mysql",
    "sqlite": "sqlite",
    "oracle": "oracle",
    "teradata": "teradata",
}

# Schema columns: relation key ("catalog.db.table") -> {column: type}
SchemaColumns = Mapping[str, Mapping[str, str]]


def dialect_for(name: str | None) -> str | None:
    if not name:
        return None
    return DIALECTS.get(name.lower(), name.lower())


@dataclass(frozen=True)
class ColumnSource:
    relation: str
    column: str
    transform: Transform


@dataclass
class StatementLineage:
    kind: str
    target: str | None = None
    reads: list[str] = field(default_factory=list)
    columns: dict[str, list[ColumnSource]] = field(default_factory=dict)
    references: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "reads": self.reads,
            "columns": {
                name: [[s.relation, s.column, s.transform] for s in sources]
                for name, sources in self.columns.items()
            },
            "references": [list(r) for r in self.references],
            "errors": self.errors,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StatementLineage:
        return cls(
            kind=data["kind"],
            target=data.get("target"),
            reads=list(data.get("reads", [])),
            columns={
                name: [ColumnSource(r, c, t) for r, c, t in sources]
                for name, sources in data.get("columns", {}).items()
            },
            references=[(r, c) for r, c in data.get("references", [])],
            errors=list(data.get("errors", [])),
        )


_JINJA_EXPR = re.compile(r"(['\"]?)\{\{.*?\}\}(['\"]?)", re.DOTALL)
_JINJA_BLOCK = re.compile(r"\{%.*?%\}|\{#.*?#\}", re.DOTALL)
PLACEHOLDER = "__kerplunk__"


def strip_templating(sql: str) -> str:
    """Replace Jinja expressions with placeholders and drop Jinja blocks and comments.

    Good enough for Airflow-style ``{{ ds }}`` parameters; dbt models are read compiled. A
    template next to a dot is part of a name, so it becomes an identifier; anywhere else it
    becomes a string literal.
    """

    def replace(match: re.Match[str]) -> str:
        before = sql[match.start() - 1] if match.start() > 0 else ""
        after = sql[match.end()] if match.end() < len(sql) else ""
        open_quote, close_quote = match.group(1), match.group(2)
        if open_quote == '"' or before == "." or after == ".":
            return f"{open_quote}{PLACEHOLDER}{close_quote}"
        return f"'{PLACEHOLDER}'"

    return _JINJA_BLOCK.sub("", _JINJA_EXPR.sub(replace, sql))


def build_schema(columns: SchemaColumns, dialect: str | None) -> MappingSchema:
    """A sqlglot schema with every relation at catalog.db.table depth."""
    nested: dict[str, Any] = {}
    for key, cols in columns.items():
        parts = key.split(".")
        if len(parts) == 2:
            parts = ["", *parts]
        elif len(parts) == 1:
            parts = ["", "", *parts]
        if len(parts) != 3 or not cols:
            continue
        catalog, db, table = parts
        nested.setdefault(catalog, {}).setdefault(db, {})[table] = {
            name: _safe_type(dtype, dialect) for name, dtype in cols.items()
        }
    return MappingSchema(nested, dialect=dialect, normalize=False)


def _safe_type(dtype: str | None, dialect: str | None) -> str:
    if not dtype:
        return "UNKNOWN"
    try:
        exp.DataType.build(dtype, dialect=dialect)
    except (SqlglotError, ValueError):
        return "UNKNOWN"
    return dtype


def _table_key(table: exp.Table, default_catalog: str | None, default_db: str | None) -> str:
    return relation_key(
        table.catalog or (default_catalog if table.db or default_db else None),
        table.db or default_db,
        table.name,
    )


def analyse(
    sql: str,
    *,
    dialect: str | None = None,
    schema: SchemaColumns | None = None,
    default_catalog: str | None = None,
    default_db: str | None = None,
    target: str | None = None,
) -> list[StatementLineage]:
    """Analyse every statement in ``sql``.

    ``target`` names the relation a bare SELECT writes (a dbt model's relation). Relations
    created by earlier statements become part of the schema for later ones.
    """
    known: dict[str, dict[str, str]] = {k: dict(v) for k, v in (schema or {}).items()}
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=dialect) if s is not None]
    except SqlglotError as error:
        return [StatementLineage(kind="unparsed", target=target, errors=[_first_line(error)])]

    results: list[StatementLineage] = []
    for statement in statements:
        result = _analyse_statement(statement, dialect, known, default_catalog, default_db, target)
        if result is None:
            continue
        if result.target and result.columns and result.target not in known:
            known[result.target] = dict.fromkeys(result.columns, "UNKNOWN")
        results.append(result)
    return results


def _first_line(error: Exception) -> str:
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__


def _analyse_statement(
    statement: exp.Expr,
    dialect: str | None,
    known: SchemaColumns,
    default_catalog: str | None,
    default_db: str | None,
    target: str | None,
) -> StatementLineage | None:
    query: exp.Expr | None = None
    out_names: list[str] | None = None
    kind = type(statement).__name__.lower()
    write: exp.Table | None = None

    if isinstance(statement, exp.Query):
        query, kind = statement, "select"
    elif isinstance(statement, exp.Create):
        if str(statement.args.get("kind") or "").upper() not in ("TABLE", "VIEW"):
            return None
        this = statement.this
        write = this.this if isinstance(this, exp.Schema) else this
        if isinstance(this, exp.Schema):
            out_names = [c.name for c in this.expressions if isinstance(c, exp.ColumnDef)] or None
        expression = statement.expression
        query = expression if isinstance(expression, exp.Query) else None
        kind = f"create_{(statement.args.get('kind') or 'table').lower()}"
        if query is None and not isinstance(write, exp.Table):
            return None
    elif isinstance(statement, exp.Insert):
        this = statement.this
        write = this.this if isinstance(this, exp.Schema) else this
        if isinstance(this, exp.Schema):
            out_names = [c.name for c in this.expressions] or None
        expression = statement.expression
        query = expression if isinstance(expression, exp.Query) else None
    elif isinstance(statement, (exp.Merge, exp.Update, exp.Delete, exp.Copy)):
        this = statement.this
        write = this if isinstance(this, exp.Table) else None
    else:
        return None

    write_key = _table_key(write, default_catalog, default_db) if isinstance(write, exp.Table) else target
    result = StatementLineage(kind=kind, target=write_key)

    ctes = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    reads: list[str] = []
    for table in statement.find_all(exp.Table):
        if table is write or (not table.db and table.name.lower() in ctes) or not table.name:
            continue
        key = _table_key(table, default_catalog, default_db)
        if key != write_key and key not in reads:
            reads.append(key)
    result.reads = reads

    if query is None:
        return result

    try:
        _column_lineage(query, dialect, known, default_catalog, default_db, out_names, result)
    except (SqlglotError, ValueError, KeyError, IndexError, AttributeError, RecursionError) as error:
        result.errors.append(f"column lineage failed: {_first_line(error)}")
    return result


def _column_lineage(
    query: exp.Expr,
    dialect: str | None,
    known: SchemaColumns,
    default_catalog: str | None,
    default_db: str | None,
    out_names: list[str] | None,
    result: StatementLineage,
) -> None:
    schema = build_schema(known, dialect)
    qualify_kwargs: dict[str, Any] = {
        "dialect": dialect,
        "schema": schema,
        "validate_qualify_columns": False,
        "identify": False,
    }
    if default_db:
        qualify_kwargs["db"] = default_db
        if default_catalog:
            qualify_kwargs["catalog"] = default_catalog
    qualified = qualify(query.copy(), **qualify_kwargs)
    scope = build_scope(qualified)
    if scope is None:
        result.errors.append("column lineage failed: not a query")
        return

    nodes = lineage(None, qualified, schema=schema, dialect=dialect, scope=scope, copy=False)
    if out_names is not None and len(out_names) != len(nodes):
        result.errors.append("INSERT column list does not match the SELECT list")
        out_names = None

    star_tables = [_table_key(t, default_catalog, default_db) for t in _unexpanded_star_tables(scope)]
    for index, (name, node) in enumerate(nodes.items()):
        if name == "*":
            continue
        out_name = (out_names[index] if out_names else name).lower()
        sources: list[ColumnSource] = []
        for leaf, transform, asked_for in _leaves(node):
            source = _leaf_source(leaf, asked_for, transform, default_catalog, default_db)
            if source and source not in sources:
                sources.append(source)
        result.columns[out_name] = sources
    if star_tables:
        result.errors.append(
            "SELECT * over relations with unknown columns: " + ", ".join(sorted(set(star_tables)))
        )

    result.references = _references(scope, dialect, schema, default_catalog, default_db)


def _is_passthrough(node: LineageNode) -> bool:
    expression = node.expression
    if isinstance(node.source, exp.SetOperation) or isinstance(expression, (exp.Table, exp.Star)):
        return True
    if isinstance(expression, exp.Alias):
        expression = expression.this
    return isinstance(expression, (exp.Column, exp.Star))


def _leaves(node: LineageNode) -> Iterator[tuple[LineageNode, Transform, str]]:
    """Leaf (physical table) nodes under ``node``, whether the path changes the value, and the
    name of the node that reached each leaf (for a ``*`` leaf, the column that was asked for)."""
    stack: list[tuple[LineageNode, bool, frozenset[int], str]] = [(node, True, frozenset(), node.name)]
    while stack:
        current, direct, seen, parent = stack.pop()
        if id(current) in seen:
            continue
        direct = direct and _is_passthrough(current)
        if not current.downstream:
            if isinstance(current.expression, exp.Table):
                yield current, "direct" if direct else "transformed", parent
            continue
        for child in reversed(current.downstream):
            stack.append((child, direct, seen | {id(current)}, current.name))


def _leaf_source(
    leaf: LineageNode,
    asked_for: str,
    transform: Transform,
    default_catalog: str | None,
    default_db: str | None,
) -> ColumnSource | None:
    table = leaf.expression
    if not isinstance(table, exp.Table):
        return None
    relation = _table_key(table, default_catalog, default_db)
    column = leaf.name.rsplit(".", 1)[-1].strip('"`[]').lower()
    if column == "*":
        # A relation whose columns sqlglot could not expand: the column is the one the query
        # asked the star for, which the relation must therefore have.
        column = asked_for.rsplit(".", 1)[-1].strip('"`[]').lower()
        if not column or column == "*":
            return None
    return ColumnSource(relation, column, transform)


def _unexpanded_star_tables(scope: Scope) -> Iterator[exp.Table]:
    for inner in scope.traverse():
        if not isinstance(inner.expression, exp.Select) or not inner.expression.is_star:
            continue
        for source in inner.sources.values():
            if isinstance(source, exp.Table):
                yield source


_REFERENCE_CLAUSES = ("joins", "where", "group", "having", "qualify")


def _references(
    root: Scope,
    dialect: str | None,
    schema: MappingSchema,
    default_catalog: str | None,
    default_db: str | None,
) -> list[tuple[str, str]]:
    """Physical columns used in joins, filters, GROUP BY, HAVING and QUALIFY."""
    found: list[tuple[str, str]] = []

    def add(relation: str, column: str) -> None:
        item = (relation, column.lower())
        if item not in found:
            found.append(item)

    for scope in root.traverse():
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        columns: list[exp.Column] = []
        for key in _REFERENCE_CLAUSES:
            value = select.args.get(key)
            for part in value if isinstance(value, list) else [value]:
                if isinstance(part, exp.Expr):
                    columns.extend(part.find_all(exp.Column))
        for column in columns:
            source = scope.sources.get(column.table)
            if isinstance(source, exp.Table):
                add(_table_key(source, default_catalog, default_db), column.name)
            elif isinstance(source, Scope):
                try:
                    traced = to_node(column.name, scope=source, dialect=dialect, schema=schema)
                except (SqlglotError, ValueError, KeyError, IndexError, AttributeError):
                    continue
                for leaf, _, asked_for in _leaves(traced):
                    leaf_source = _leaf_source(leaf, asked_for, "direct", default_catalog, default_db)
                    if leaf_source:
                        add(leaf_source.relation, leaf_source.column)
    return found
