"""Warehouse and catalogue metadata: DuckDB, Postgres and Iceberg REST (or any PyIceberg) catalogs.

Each connector returns `RelationMeta` records; `kerplunk.build` merges them into the graph.
Connections are read only. Credentials come from configuration or environment variables and
are never written to the graph.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from kerplunk.graph import relation_key

SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog", "pg_toast"})


@dataclass
class ColumnMeta:
    name: str
    data_type: str | None = None
    comment: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class RelationMeta:
    parts: tuple[str, ...]
    kind: str
    comment: str | None = None
    columns: list[ColumnMeta] = field(default_factory=list)
    view_sql: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return relation_key(*self.parts)

    @property
    def name(self) -> str:
        return self.parts[-1]


class Connector(Protocol):
    label: str
    dialect: str | None
    default_schema: str | None

    def fetch(self) -> list[RelationMeta]: ...


def _schemas_filter(schemas: list[str] | None) -> set[str] | None:
    return {s.lower() for s in schemas} if schemas else None


@dataclass
class DuckDBConnector:
    path: Path
    schemas: list[str] | None = None
    label: str = "duckdb"
    dialect: str | None = "duckdb"
    default_schema: str | None = "main"

    def fetch(self) -> list[RelationMeta]:
        import duckdb

        wanted = _schemas_filter(self.schemas)
        con = duckdb.connect(str(self.path), read_only=True)
        try:
            database = con.execute("select current_database()").fetchone()
            current = database[0] if database else self.path.stem
            tables = con.execute(
                """
                select t.table_catalog, t.table_schema, t.table_name, t.table_type, d.comment
                from information_schema.tables t
                left join duckdb_tables() d
                  on d.database_name = t.table_catalog and d.schema_name = t.table_schema
                 and d.table_name = t.table_name
                where t.table_catalog = ?
                order by 1, 2, 3
                """,
                [current],
            ).fetchall()
            views = {
                (row[0], row[1], row[2]): (row[3], row[4])
                for row in con.execute(
                    "select database_name, schema_name, view_name, sql, comment from duckdb_views() "
                    "where not internal and database_name = ?",
                    [current],
                ).fetchall()
            }
            columns: dict[tuple[str, str, str], list[ColumnMeta]] = {}
            for catalog, schema, table, name, dtype, comment in con.execute(
                """
                select table_catalog, table_schema, table_name, column_name, data_type, column_comment
                from information_schema.columns
                where table_catalog = ?
                order by table_catalog, table_schema, table_name, ordinal_position
                """,
                [current],
            ).fetchall():
                columns.setdefault((catalog, schema, table), []).extend(
                    [ColumnMeta(name, dtype, comment), *_duckdb_struct_fields(name, dtype)]
                )
        finally:
            con.close()

        relations = []
        for catalog, schema, table, table_type, comment in tables:
            if schema.lower() in SYSTEM_SCHEMAS or (wanted and schema.lower() not in wanted):
                continue
            view = views.get((catalog, schema, table))
            relations.append(
                RelationMeta(
                    parts=(catalog, schema, table),
                    kind="view" if table_type == "VIEW" else "table",
                    comment=comment or (view[1] if view else None),
                    columns=columns.get((catalog, schema, table), []),
                    view_sql=view[0] if view else None,
                )
            )
        return relations


def _duckdb_struct_fields(name: str, dtype: str | None) -> list[ColumnMeta]:
    """Fields of a DuckDB STRUCT column as dotted columns (``address.city``), recursively."""
    if not dtype or not dtype.upper().startswith("STRUCT"):
        return []
    from sqlglot import exp
    from sqlglot.errors import SqlglotError

    try:
        built = exp.DataType.build(dtype, dialect="duckdb")
    except (SqlglotError, ValueError):
        return []
    fields: list[ColumnMeta] = []
    for field_def in built.expressions:
        if not isinstance(field_def, exp.ColumnDef) or not isinstance(field_def.kind, exp.DataType):
            continue
        child = f"{name}.{field_def.name}"
        child_type = field_def.kind.sql(dialect="duckdb")
        fields.append(ColumnMeta(child, child_type, None, {"nested": True}))
        fields.extend(_duckdb_struct_fields(child, child_type))
    return fields


@dataclass
class PostgresConnector:
    dsn: str
    schemas: list[str] | None = None
    label: str = "postgres"
    dialect: str | None = "postgres"
    default_schema: str | None = "public"

    def fetch(self) -> list[RelationMeta]:
        import psycopg

        wanted = _schemas_filter(self.schemas)
        with psycopg.connect(self.dsn, autocommit=True) as con, con.cursor() as cur:
            cur.execute("set default_transaction_read_only = on")
            cur.execute(
                """
                select current_database(), n.nspname, c.relname,
                       case c.relkind when 'v' then 'view' when 'm' then 'view' else 'table' end,
                       obj_description(c.oid, 'pg_class'),
                       case when c.relkind in ('v', 'm') then pg_get_viewdef(c.oid, true) end,
                       c.oid
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                where c.relkind in ('r', 'p', 'v', 'm', 'f')
                  and not c.relispartition
                  and n.nspname not in ('pg_catalog', 'information_schema', 'pg_toast')
                  and n.nspname not like 'pg_temp_%' and n.nspname not like 'pg_toast_temp_%'
                order by 2, 3
                """
            )
            tables = cur.fetchall()
            cur.execute(
                """
                select a.attrelid, a.attname, format_type(a.atttypid, a.atttypmod),
                       col_description(a.attrelid, a.attnum)
                from pg_attribute a
                join pg_class c on c.oid = a.attrelid
                join pg_namespace n on n.oid = c.relnamespace
                where a.attnum > 0 and not a.attisdropped
                  and c.relkind in ('r', 'p', 'v', 'm', 'f')
                  and n.nspname not in ('pg_catalog', 'information_schema', 'pg_toast')
                order by a.attrelid, a.attnum
                """
            )
            columns: dict[int, list[ColumnMeta]] = {}
            for oid, name, dtype, comment in cur.fetchall():
                columns.setdefault(oid, []).append(ColumnMeta(name, dtype, comment))

        relations = []
        for database, schema, table, kind, comment, view_sql, oid in tables:
            if wanted and schema.lower() not in wanted:
                continue
            relations.append(
                RelationMeta(
                    parts=(database, schema, table),
                    kind=kind,
                    comment=comment,
                    columns=columns.get(oid, []),
                    view_sql=f"create view {_quote_pg(schema)}.{_quote_pg(table)} as {view_sql}"
                    if view_sql
                    else None,
                )
            )
        return relations


def _quote_pg(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass
class IcebergConnector:
    """Any PyIceberg catalog; ``properties`` are passed to ``pyiceberg.catalog.load_catalog``.

    ``name`` is the catalogue name an engine uses to query it (for example the Trino or Spark
    catalogue name), so that SQL such as ``lake.sales.orders`` resolves to these tables.
    """

    name: str
    properties: dict[str, str]
    namespaces: list[str] | None = None
    label: str = "iceberg"
    dialect: str | None = None
    default_schema: str | None = None

    def fetch(self) -> list[RelationMeta]:
        from pyiceberg.catalog import load_catalog

        # kerplunk reads metadata only. PyIceberg still builds a FileIO for every table it loads,
        # and its default needs pyarrow for some locations; the fsspec FileIO opens nothing
        # until a file is read, so it works for any location without extra packages.
        properties = {"py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO", **self.properties}
        catalog = load_catalog(self.name, **properties)
        relations = []
        for namespace in self._namespaces(catalog):
            for identifier in catalog.list_tables(namespace):
                table = catalog.load_table(identifier)
                schema = table.schema()
                identifier_ids = set(getattr(schema, "identifier_field_ids", []) or [])
                columns = list(_flatten_fields(schema.fields, prefix="", identifier_ids=identifier_ids))
                table_properties = dict(table.properties or {})
                attrs: dict[str, Any] = {"format_version": table.format_version}
                spec = table.spec()
                if spec.fields:
                    attrs["partitioned_by"] = [
                        f"{field.transform}({schema.find_column_name(field.source_id)})"
                        for field in spec.fields
                    ]
                if identifier_ids:
                    attrs["identifier_fields"] = [schema.find_column_name(i) for i in sorted(identifier_ids)]
                snapshot = table.current_snapshot()
                if snapshot is not None:
                    attrs["snapshot_id"] = snapshot.snapshot_id
                relations.append(
                    RelationMeta(
                        parts=(self.name, *identifier),
                        kind="table",
                        comment=table_properties.get("comment"),
                        columns=columns,
                        attrs=attrs,
                    )
                )
        return relations

    def _namespaces(self, catalog: Any) -> Iterator[tuple[str, ...]]:
        if self.namespaces:
            for configured in self.namespaces:
                yield tuple(configured.split("."))
            return
        pending: list[tuple[str, ...]] = list(catalog.list_namespaces())
        seen: set[tuple[str, ...]] = set()
        while pending:
            namespace = tuple(pending.pop(0))
            if namespace in seen:
                continue
            seen.add(namespace)
            yield namespace
            try:
                children = catalog.list_namespaces(namespace)
            except Exception:
                children = []
            pending.extend(tuple(child) for child in children if tuple(child) != namespace)


def _flatten_fields(fields: Any, prefix: str, identifier_ids: set[int]) -> Iterator[ColumnMeta]:
    """Top-level Iceberg columns, plus nested struct fields as dotted names (``address.city``)."""
    from pyiceberg.types import ListType, MapType, StructType

    for item in fields:
        name = f"{prefix}{item.name}"
        attrs: dict[str, Any] = {"field_id": item.field_id}
        if item.required:
            attrs["required"] = True
        if item.field_id in identifier_ids:
            attrs["identifier"] = True
        if prefix:
            attrs["nested"] = True
        yield ColumnMeta(name, str(item.field_type), item.doc, attrs)
        field_type = item.field_type
        if isinstance(field_type, StructType):
            yield from _flatten_fields(field_type.fields, f"{name}.", identifier_ids)
        elif isinstance(field_type, ListType) and isinstance(field_type.element_type, StructType):
            yield from _flatten_fields(field_type.element_type.fields, f"{name}.element.", identifier_ids)
        elif isinstance(field_type, MapType) and isinstance(field_type.value_type, StructType):
            yield from _flatten_fields(field_type.value_type.fields, f"{name}.value.", identifier_ids)
