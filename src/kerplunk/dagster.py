"""Static parsing of Dagster code: assets, their dependencies, dbt assets, jobs and schedules.

Like the Airflow parser, this reads files with `ast` and never imports them, so Dagster does not
need to be installed and no user code runs. It understands the common ways of declaring assets:

* ``@asset`` and ``@multi_asset`` functions, their keys (``name=``, ``key=``, ``key_prefix=``,
  ``AssetKey([...])``), ``group_name=``, docstrings and descriptions;
* dependencies from function parameters, ``ins={"x": AssetIn("key")}`` and ``deps=[...]``
  (asset functions, strings and ``AssetKey``s);
* ``@dbt_assets(manifest=..., select=..., exclude=...)``, which runs a selection of dbt models;
* ``define_asset_job(name, selection=...)`` with string selections and ``AssetSelection.all()``,
  ``.groups(...)`` and ``.keys(...)``; and ``ScheduleDefinition`` / ``build_schedule_from_partitioned_job``;
* SQL passed as a literal to ``.execute(...)``, ``.sql(...)`` or ``.query(...)`` inside an asset,
  which is analysed for lineage like an Airflow SQL task.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ASSET_DECORATORS = frozenset({"asset", "multi_asset", "graph_asset", "observable_source_asset"})
SQL_METHODS = frozenset({"execute", "sql", "query", "run_query", "executescript"})


@dataclass
class AssetInfo:
    key: str
    function: str
    line: int
    group: str | None = None
    description: str | None = None
    deps: list[str] = field(default_factory=list)
    sql: str | None = None
    kinds: list[str] = field(default_factory=list)
    dbt_select: list[str] | None = None
    dbt_exclude: list[str] = field(default_factory=list)


@dataclass
class JobInfo:
    name: str
    line: int
    selection: list[str] = field(default_factory=list)
    schedule: str | None = None
    description: str | None = None


@dataclass
class DagsterFile:
    path: str
    assets: list[AssetInfo] = field(default_factory=list)
    jobs: list[JobInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DagsterFile:
        return cls(
            path=data["path"],
            assets=[AssetInfo(**a) for a in data.get("assets", [])],
            jobs=[JobInfo(**j) for j in data.get("jobs", [])],
            warnings=list(data.get("warnings", [])),
        )


def looks_like_dagster_file(text: str) -> bool:
    return "dagster" in text and any(
        marker in text
        for marker in ("@asset", "@multi_asset", "@dbt_assets", "define_asset_job", "Definitions(")
    )


def _name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class _Parser:
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self.constants: dict[str, Any] = {}
        self.functions: dict[str, AssetInfo] = {}
        self.multi: dict[str, AssetInfo] = {}
        self.result = DagsterFile(path=str(path))
        self.schedules: list[tuple[str, str]] = []

    def value(self, node: ast.expr | None) -> Any:
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in self.functions:
                return {"__asset__": self.functions[node.id].key}
            return self.constants.get(node.id)
        if isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                if isinstance(part, ast.Constant):
                    parts.append(str(part.value))
                elif isinstance(part, ast.FormattedValue):
                    inner = self.value(part.value)
                    if inner is None:
                        return None
                    parts.append(str(inner))
            return "".join(parts)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [self.value(e) for e in node.elts]
        if isinstance(node, ast.Dict):
            return {
                self.value(k): self.value(v)
                for k, v in zip(node.keys, node.values, strict=True)
                if k is not None
            }
        if isinstance(node, ast.Call):
            name = _name(node)
            if name == "AssetKey" and node.args:
                return {"__asset__": _key(self.value(node.args[0]))}
            if name == "AssetIn":
                key = self.value(node.args[0]) if node.args else self.kwarg(node, "key")
                prefix = self.kwarg(node, "key_prefix")
                return {"__asset_in__": _key(key, prefix) if key is not None else None}
            if name == "AssetDep" and node.args:
                inner = self.value(node.args[0])
                return inner if isinstance(inner, dict) else {"__asset__": _key(inner)}
            if name == "AssetOut":
                return {
                    "__asset_out__": self.kwarg(node, "key"),
                    "prefix": self.kwarg(node, "key_prefix"),
                    "group": self.kwarg(node, "group_name"),
                    "description": self.kwarg(node, "description"),
                }
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "AssetSelection"
            ):
                return self._selection(node)
            if isinstance(node.func, ast.Attribute) and node.func.attr in (
                "downstream",
                "upstream",
                "required_multi_asset_neighbors",
            ):
                return self.value(node.func.value)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitOr, ast.Add)):
            left, right = self.value(node.left), self.value(node.right)
            if isinstance(left, list) and isinstance(right, list):
                return left + right
            if isinstance(left, str) and isinstance(right, str) and isinstance(node.op, ast.Add):
                return left + right
        return None

    def _selection(self, call: ast.Call) -> list[str]:
        assert isinstance(call.func, ast.Attribute)
        method = call.func.attr
        values = [self.value(a) for a in call.args]
        if method == "all":
            return ["*"]
        if method == "groups":
            return [f"group:{v}" for v in values if isinstance(v, str)]
        if method in ("keys", "assets"):
            found = []
            for item in values:
                if isinstance(item, dict) and item.get("__asset__"):
                    found.append(str(item["__asset__"]))
                elif isinstance(item, (str, list)):
                    found.append(_key(item))
            return found
        if method == "key_prefixes":
            return [f"prefix:{_key(v)}" for v in values if v is not None]
        return []

    def kwarg(self, call: ast.Call, name: str) -> Any:
        for keyword in call.keywords:
            if keyword.arg == name:
                return self.value(keyword.value)
        return None

    def parse(self) -> DagsterFile:
        tree = ast.parse(self.source, filename=str(self.path))
        for statement in tree.body:
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                folded = self.value(statement.value)
                if folded is not None:
                    self.constants[statement.targets[0].id] = folded
        nodes = list(ast.walk(tree))
        functions = [n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for func in functions:
            self.visit_function(func)
        for func in functions:
            if func.name in self.functions or func.name in self.multi:
                self.resolve_dependencies(func)
        assigned: set[int] = set()
        for item in nodes:
            if isinstance(item, ast.Assign) and isinstance(item.value, ast.Call):
                self.visit_call(item.value, item.targets[0])
                assigned.add(id(item.value))
        for item in nodes:
            if isinstance(item, ast.Call) and id(item) not in assigned:
                self.visit_call(item, None)
        jobs = {job.name: job for job in self.result.jobs}
        for job_name, cron in self.schedules:
            if job_name in jobs:
                jobs[job_name].schedule = cron
        return self.result

    def visit_function(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in func.decorator_list:
            name = _name(decorator) or ""
            call = decorator if isinstance(decorator, ast.Call) else None
            if name == "dbt_assets":
                select = self.kwarg(call, "select") if call else None
                exclude = self.kwarg(call, "exclude") if call else None
                asset = AssetInfo(
                    key=func.name,
                    function=func.name,
                    line=func.lineno,
                    description=ast.get_docstring(func),
                    kinds=["dbt"],
                    dbt_select=str(select).split() if isinstance(select, str) else [],
                    dbt_exclude=str(exclude).split() if isinstance(exclude, str) else [],
                )
                self.functions[func.name] = asset
                self.result.assets.append(asset)
                return
            if name not in ASSET_DECORATORS:
                continue
            if name == "multi_asset" and call is not None:
                outs = self.kwarg(call, "outs") or {}
                if isinstance(outs, dict):
                    prefix = self.kwarg(call, "key_prefix")
                    group = self.kwarg(call, "group_name")
                    first: AssetInfo | None = None
                    for out_name, out in outs.items():
                        spec = out if isinstance(out, dict) else {}
                        out_key = spec.get("__asset_out__") or _key(out_name, spec.get("prefix") or prefix)
                        asset = AssetInfo(
                            key=_key(out_key)
                            if not isinstance(out_key, str) or "/" not in out_key
                            else out_key,
                            function=func.name,
                            line=func.lineno,
                            group=spec.get("group") or group,
                            description=spec.get("description") or ast.get_docstring(func),
                        )
                        self.result.assets.append(asset)
                        first = first or asset
                    if first is not None:
                        # Registered for dependency resolution only; downstream assets name outputs.
                        self.multi[func.name] = first
                    return
            key: Any = func.name
            group = None
            description = ast.get_docstring(func)
            kinds: list[str] = []
            if call is not None:
                explicit = self.kwarg(call, "key")
                named = self.kwarg(call, "name")
                prefix = self.kwarg(call, "key_prefix")
                if isinstance(explicit, dict) and explicit.get("__asset__"):
                    key = explicit["__asset__"]
                elif explicit is not None:
                    key = _key(explicit)
                else:
                    key = _key(named or func.name, prefix)
                group = self.kwarg(call, "group_name")
                description = self.kwarg(call, "description") or description
                for kind_arg in ("kinds", "compute_kind"):
                    found = self.kwarg(call, kind_arg)
                    kinds += (
                        [found] if isinstance(found, str) else [k for k in found or [] if isinstance(k, str)]
                    )
            asset = AssetInfo(
                key=_key(key),
                function=func.name,
                line=func.lineno,
                group=group,
                description=description.strip() if isinstance(description, str) else None,
                kinds=kinds,
            )
            asset.sql = self._sql_in(func)
            self.functions[func.name] = asset
            self.result.assets.append(asset)
            return

    def resolve_dependencies(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        asset = self.functions.get(func.name) or self.multi[func.name]
        assets = [a for a in self.result.assets if a.function == func.name]
        deps: list[str] = []
        ins: dict[str, Any] = {}
        for decorator in func.decorator_list:
            if isinstance(decorator, ast.Call):
                ins = self.kwarg(decorator, "ins") or {}
                for item in self.kwarg(decorator, "deps") or []:
                    if isinstance(item, dict) and item.get("__asset__"):
                        deps.append(str(item["__asset__"]))
                    elif isinstance(item, (str, list)):
                        deps.append(_key(item))
        params = [
            a.arg
            for a in func.args.args + func.args.kwonlyargs
            if a.arg not in ("context", "self", "config") and not _is_resource(a.annotation)
        ]
        for param in params:
            spec = ins.get(param) if isinstance(ins, dict) else None
            if isinstance(spec, dict) and spec.get("__asset_in__"):
                deps.append(str(spec["__asset_in__"]))
            elif param not in ("dbt", "duckdb", "database", "warehouse") and not param.endswith(
                ("_resource", "_client")
            ):
                # As Dagster does: a parameter names the upstream asset under this asset's own key prefix.
                prefix = asset.key.rsplit("/", 1)[0] if "/" in asset.key else None
                deps.append(_key(param, prefix))
        for item in assets or [asset]:
            item.deps = sorted(set(deps) - {item.key})

    def _sql_in(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
        statements = []
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in SQL_METHODS
                and node.args
            ):
                text = self.value(node.args[0])
                if isinstance(text, str) and text.strip():
                    statements.append(text.strip().rstrip(";"))
        return ";\n".join(statements) or None

    def visit_call(self, call: ast.Call, target: ast.expr | None) -> None:
        name = _name(call) or ""
        if name == "define_asset_job":
            job_name = self.value(call.args[0]) if call.args else self.kwarg(call, "name")
            if not isinstance(job_name, str):
                self.result.warnings.append(f"line {call.lineno}: asset job name is not a constant; skipped")
                return
            selection = self.kwarg(call, "selection")
            if selection is None and len(call.args) > 1:
                selection = self.value(call.args[1])
            self.result.jobs.append(
                JobInfo(
                    name=job_name,
                    line=call.lineno,
                    selection=_selection_list(selection),
                    description=self.kwarg(call, "description"),
                )
            )
            if isinstance(target, ast.Name):
                self.constants[target.id] = {"__job__": job_name}
        elif name in ("ScheduleDefinition", "build_schedule_from_partitioned_job"):
            job = self.kwarg(call, "job") or (self.value(call.args[0]) if call.args else None)
            cron = self.kwarg(call, "cron_schedule")
            job_name = job.get("__job__") if isinstance(job, dict) else self.kwarg(call, "job_name")
            if isinstance(job_name, str):
                self.schedules.append((job_name, cron if isinstance(cron, str) else "partitioned"))


def _is_resource(annotation: ast.expr | None) -> bool:
    """Parameters typed as resources (DuckDBResource, DbtCliResource, ...) or contexts are not assets."""
    name = _name(annotation) if annotation is not None else None
    if isinstance(annotation, ast.Subscript):
        name = _name(annotation.value)
    if not name:
        return False
    return name.endswith(("Resource", "Context", "Config")) or name == "ResourceParam"


def _selection_list(selection: Any) -> list[str]:
    if selection is None:
        return ["*"]
    if isinstance(selection, str):
        return [_key(part) if part not in ("*",) and ":" not in part else part for part in selection.split()]
    if isinstance(selection, list):
        found = []
        for item in selection:
            if isinstance(item, dict) and item.get("__asset__"):
                found.append(str(item["__asset__"]))
            elif isinstance(item, str):
                found.append(item if item.startswith(("group:", "prefix:")) or item == "*" else _key(item))
            elif isinstance(item, list):
                found.extend(_selection_list(item))
        return found
    if isinstance(selection, dict) and selection.get("__asset__"):
        return [str(selection["__asset__"])]
    return []


def _key(value: Any, prefix: Any = None) -> str:
    """An asset key as slash-separated text: ``["analytics", "orders"]`` is ``analytics/orders``."""
    if isinstance(value, dict) and value.get("__asset__"):
        base = str(value["__asset__"])
    elif isinstance(value, (list, tuple)):
        base = "/".join(str(v) for v in value if v is not None)
    else:
        base = str(value)
    if prefix:
        parts = [prefix] if isinstance(prefix, str) else [str(p) for p in prefix]
        base = "/".join([*parts, base])
    return base


def parse_file(path: Path) -> DagsterFile:
    """Parse one Dagster file. Raises SyntaxError if it is not valid Python."""
    return _Parser(path, path.read_text(encoding="utf-8")).parse()


def parse_source(path: Path, source: str) -> DagsterFile:
    return _Parser(path, source).parse()
