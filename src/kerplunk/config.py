"""Configuration: ``kerplunk.toml`` (or ``[tool.kerplunk]`` in ``pyproject.toml``) plus discovery.

Everything is optional. Without configuration kerplunk discovers dbt projects, Airflow DAG
files, SQL files, DuckDB files and the warehouse each dbt profile points at.

Example ``kerplunk.toml``::

    dialect = "postgres"                  # default: the dbt adapter, else sqlglot's default
    default_catalog = "analytics"         # for unqualified tables in Airflow and SQL files
    default_schema = "public"
    exclude = ["scratch/**"]

    [dbt]
    projects = ["transform"]              # default: every dbt_project.yml found
    run = true                            # run `dbt docs generate` when artefacts are stale

    [airflow]
    dags = ["orchestration/dags"]         # files or directories; default: discovered

    [sql]
    paths = ["sql/**/*.sql"]              # default: discovered *.sql outside dbt projects

    [[warehouse]]
    type = "postgres"
    dsn = "${WAREHOUSE_DSN}"              # ${VAR} reads the environment
    schemas = ["raw", "analytics"]

    [[warehouse]]
    type = "iceberg"
    name = "lake"                         # the catalogue name SQL uses for these tables
    uri = "http://localhost:8181"
    namespaces = ["sales"]
    [warehouse.properties]
    credential = "${ICEBERG_CREDENTIAL}"
"""

from __future__ import annotations

import contextlib
import os
import re
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

from kerplunk.dbt import SKIP_DIRS, DbtProject, walk_dirs
from kerplunk.warehouse import Connector, DuckDBConnector, IcebergConnector, PostgresConnector


class ConfigError(Exception):
    pass


_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    return value


@dataclass
class WarehouseConfig:
    type: str
    options: dict[str, Any]
    source: str = "kerplunk.toml"

    def connector(self, root: Path) -> Connector:
        options = expand_env(self.options)
        schemas = options.get("schemas")
        if self.type == "duckdb":
            path = Path(str(options.get("path", "")))
            if not path.is_absolute():
                path = root / path
            return DuckDBConnector(path, schemas=schemas)
        if self.type in ("postgres", "postgresql"):
            dsn = options.get("dsn") or _dsn_from_parts(options)
            return PostgresConnector(dsn, schemas=schemas)
        if self.type == "iceberg":
            properties = {str(k): str(v) for k, v in (options.get("properties") or {}).items()}
            for key in ("uri", "warehouse", "type"):
                if key in options and key not in properties:
                    properties[key] = str(options[key])
            properties.setdefault("type", "rest")
            return IcebergConnector(
                name=str(options.get("name", "iceberg")),
                properties=properties,
                namespaces=options.get("namespaces"),
            )
        raise ConfigError(f"unknown warehouse type {self.type!r} (expected duckdb, postgres or iceberg)")

    def describe(self, root: Path | None = None) -> str:
        if self.type == "duckdb":
            path = Path(str(self.options.get("path", "")))
            if root is not None and path.is_absolute():
                with contextlib.suppress(ValueError):
                    path = path.relative_to(root)
            return f"duckdb {path}"
        if self.type == "iceberg":
            return f"iceberg {self.options.get('name', 'iceberg')} at {self.options.get('uri', '?')}"
        return f"{self.type} ({self.source})"


def _dsn_from_parts(options: dict[str, Any]) -> str:
    parts = {
        "host": options.get("host"),
        "port": options.get("port"),
        "dbname": options.get("dbname") or options.get("database"),
        "user": options.get("user"),
        "password": options.get("password") or options.get("pass"),
        "sslmode": options.get("sslmode"),
    }
    return " ".join(f"{k}={_quote_libpq(str(v))}" for k, v in parts.items() if v not in (None, ""))


def _quote_libpq(value: str) -> str:
    if value and not re.search(r"[\s'\\]", value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


@dataclass
class Config:
    root: Path
    out_dir: Path
    dialect: str | None = None
    default_catalog: str | None = None
    default_schema: str | None = None
    exclude: list[str] = field(default_factory=list)
    run_dbt: bool = True
    dbt_projects: list[Path] | None = None
    dag_paths: list[Path] | None = None
    sql_paths: list[str] | None = None
    warehouses: list[WarehouseConfig] | None = None
    discover_warehouses: bool = True
    config_file: Path | None = None

    def excluded(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.root).as_posix()
        except ValueError:
            return False
        return any(fnmatch(relative, pattern) for pattern in self.exclude)


def load_config(root: Path, out_dir: Path | None = None) -> Config:
    root = root.resolve()
    data: dict[str, Any] = {}
    config_file: Path | None = None
    if (root / "kerplunk.toml").is_file():
        config_file = root / "kerplunk.toml"
        data = _read_toml(config_file)
    elif (root / "pyproject.toml").is_file():
        pyproject = _read_toml(root / "pyproject.toml")
        tool = pyproject.get("tool", {}).get("kerplunk")
        if isinstance(tool, dict):
            config_file = root / "pyproject.toml"
            data = tool

    dbt = data.get("dbt", {})
    airflow = data.get("airflow", {})
    sql = data.get("sql", {})
    warehouses = data.get("warehouse")
    if isinstance(warehouses, dict):
        warehouses = [warehouses]
    config = Config(
        root=root,
        out_dir=(out_dir or root / str(data.get("out_dir", "kerplunk-out"))).resolve(),
        dialect=data.get("dialect"),
        default_catalog=data.get("default_catalog"),
        default_schema=data.get("default_schema"),
        exclude=list(data.get("exclude", [])),
        run_dbt=bool(dbt.get("run", True)),
        dbt_projects=[root / p for p in dbt["projects"]] if "projects" in dbt else None,
        dag_paths=[root / p for p in airflow["dags"]] if "dags" in airflow else None,
        sql_paths=list(sql["paths"]) if "paths" in sql else None,
        warehouses=[
            WarehouseConfig(str(w.get("type", "")).lower(), {k: v for k, v in w.items() if k != "type"})
            for w in warehouses
        ]
        if warehouses is not None
        else None,
        discover_warehouses=bool(data.get("discover_warehouses", True)),
        config_file=config_file,
    )
    return config


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"cannot read {path}: {error}") from error


# Discovery


def iter_files(config: Config, suffix: str) -> list[Path]:
    found = []
    for directory in walk_dirs(config.root):
        if directory.resolve() == config.out_dir:
            continue
        for path in sorted(directory.glob(f"*{suffix}")):
            if path.is_file() and not config.excluded(path):
                found.append(path)
    return found


def discover_dag_files(config: Config) -> list[Path]:
    from kerplunk.airflow import looks_like_dag_file

    if config.dag_paths is not None:
        files: list[Path] = []
        for path in config.dag_paths:
            if path.is_dir():
                files.extend(sorted(p for p in path.rglob("*.py") if not set(p.parts) & SKIP_DIRS))
            elif path.is_file():
                files.append(path)
        return files
    candidates = []
    for path in iter_files(config, ".py"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if looks_like_dag_file(text):
            candidates.append(path)
    return candidates


def discover_sql_files(config: Config, projects: list[DbtProject], used: set[Path]) -> list[Path]:
    if config.sql_paths is not None:
        files: list[Path] = []
        for pattern in config.sql_paths:
            files.extend(sorted(p for p in config.root.glob(pattern) if p.is_file()))
        return [f for f in files if f.resolve() not in used]
    owned = [p.resolve() for project in projects for p in [*project.source_paths, project.target_path]]
    owned += [(project.root / name).resolve() for project in projects for name in ("analyses", "analysis")]
    result = []
    for path in iter_files(config, ".sql"):
        resolved = path.resolve()
        if resolved in used or any(resolved.is_relative_to(base) for base in owned):
            continue
        result.append(path)
    return result


def discover_warehouses(config: Config, projects: list[DbtProject]) -> list[WarehouseConfig]:
    if config.warehouses is not None:
        return list(config.warehouses)
    if not config.discover_warehouses:
        return []
    found: list[WarehouseConfig] = []
    seen_paths: set[Path] = set()
    for project in projects:
        target = _dbt_profile_target(project)
        if not target:
            continue
        kind = str(target.get("type", "")).lower()
        if kind == "duckdb" and target.get("path") and target["path"] != ":memory:":
            path = Path(str(target["path"]))
            path = path if path.is_absolute() else project.root / path
            if path.is_file() and path.resolve() not in seen_paths:
                seen_paths.add(path.resolve())
                found.append(
                    WarehouseConfig("duckdb", {"path": str(path)}, source=f"dbt profile {project.profile}")
                )
        elif kind in ("postgres", "postgresql"):
            keys = ("host", "port", "user", "password", "pass", "dbname", "database", "sslmode")
            options = {k: target.get(k) for k in keys}
            found.append(WarehouseConfig("postgres", options, source=f"dbt profile {project.profile}"))
    for path in iter_files(config, ".duckdb"):
        if path.resolve() not in seen_paths:
            seen_paths.add(path.resolve())
            found.append(WarehouseConfig("duckdb", {"path": str(path)}, source="discovered"))
    return found


_ENV_VAR_CALL = re.compile(
    r"\{\{\s*env_var\(\s*['\"]([^'\"]+)['\"]\s*(?:,\s*['\"]([^'\"]*)['\"]\s*)?\)\s*(?:\|\s*\w+\s*)?\}\}"
)


def _render_profile_value(value: Any) -> Any:
    if isinstance(value, str):
        rendered = _ENV_VAR_CALL.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
        return None if "{{" in rendered or "{%" in rendered else rendered
    return value


def _dbt_profile_target(project: DbtProject) -> dict[str, Any] | None:
    if not project.profile:
        return None
    candidates = []
    if os.environ.get("DBT_PROFILES_DIR"):
        candidates.append(Path(os.environ["DBT_PROFILES_DIR"]) / "profiles.yml")
    candidates += [project.root / "profiles.yml", Path.home() / ".dbt" / "profiles.yml"]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            profiles = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        profile = profiles.get(project.profile) if isinstance(profiles, dict) else None
        if not isinstance(profile, dict):
            continue
        target_name = _render_profile_value(os.environ.get("DBT_TARGET") or profile.get("target", "dev"))
        target = (profile.get("outputs") or {}).get(target_name)
        if isinstance(target, dict):
            return {k: _render_profile_value(v) for k, v in target.items()}
    return None
