"""The Python API: one object for a project, its graph and every question kerplunk answers.

import kerplunk

project = kerplunk.Project("path/to/repo")
project.build(update=True)
walk = project.impact("orders.amount")
print(walk.summary())
for column in walk.columns:
    print(column.label, column.transform, column.path)
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from kerplunk import query
from kerplunk.graph import Graph

if TYPE_CHECKING:
    from kerplunk.build import BuildResult
    from kerplunk.config import Config
    from kerplunk.verify import Verification

OUT_DIR = "kerplunk-out"


def find_out_dir(start: Path) -> Path:
    """The nearest ``kerplunk-out`` holding a graph, looking up from ``start``; otherwise
    ``start/kerplunk-out``."""
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / OUT_DIR / "graph.json").is_file():
            return directory / OUT_DIR
        if directory.name == OUT_DIR and (directory / "graph.json").is_file():
            return directory
    return start / OUT_DIR


class Project:
    """A repository kerplunk builds a graph for.

    ``path`` is the project root (or any directory inside one that has already been built).
    ``out_dir`` overrides where the graph is written and read; by default it is the
    ``kerplunk-out`` directory found at or above ``path``, or ``<path>/kerplunk-out``.
    """

    def __init__(self, path: str | Path = ".", *, out_dir: str | Path | None = None) -> None:
        self.path = Path(path).resolve()
        if out_dir is not None:
            self.out_dir = Path(out_dir).resolve()
        else:
            found = find_out_dir(self.path)
            self.out_dir = found if (found / "graph.json").is_file() else self.path / OUT_DIR
        self._graph: Graph | None = None
        self._stamp: tuple[float, int] | None = None

    def __repr__(self) -> str:
        return f"Project({str(self.path)!r})"

    @property
    def root(self) -> Path:
        """The directory the graph was built from (``path`` until a graph exists)."""
        graph_root = self.graph.meta.get("root") if self.has_graph() else None
        return Path(graph_root) if graph_root else self.path

    @property
    def graph_path(self) -> Path:
        return self.out_dir / "graph.json"

    def has_graph(self) -> bool:
        return self.graph_path.is_file()

    def config(self) -> Config:
        from kerplunk.config import load_config

        return load_config(self.path, self.out_dir)

    def build(
        self,
        *,
        update: bool = False,
        run_dbt: bool | None = None,
        warehouses: bool = True,
        dialect: str | None = None,
        config: Config | None = None,
    ) -> BuildResult:
        """Build (or with ``update``, refresh) the graph and write graph.json, REPORT.md and graph.html."""
        from kerplunk.build import build

        config = config or self.config()
        if run_dbt is not None:
            config.run_dbt = run_dbt
        if not warehouses:
            config.warehouses = []
        if dialect:
            config.dialect = dialect
        result = build(config, update=update)
        self.out_dir = config.out_dir
        self._graph = result.graph
        self._stamp = self._file_stamp()
        return result

    def _file_stamp(self) -> tuple[float, int] | None:
        try:
            stat = self.graph_path.stat()
        except OSError:
            return None
        return (stat.st_mtime, stat.st_size)

    @property
    def graph(self) -> Graph:
        """The built graph, reread when graph.json changes. Raises QueryError if there is none."""
        stamp = self._file_stamp()
        if stamp is None:
            if self._graph is not None:
                return self._graph
            raise query.QueryError(f"no graph at {self.graph_path}; run `kerplunk build` first")
        if self._graph is None or stamp != self._stamp:
            self._graph = Graph.read(self.graph_path)
            self._stamp = stamp
        return self._graph

    def changed_inputs(self) -> list[str]:
        """Input files that changed after the graph was built; empty when it is up to date."""
        from kerplunk.build import changed_inputs

        return changed_inputs(self.graph_path, self.graph) if self.has_graph() else []

    def impact(self, ref: str, max_depth: int | None = None) -> query.Walk:
        """Everything downstream of a table, model or column, e.g. ``orders`` or ``orders.amount``."""
        return query.impact(self.graph, ref, max_depth)

    def trace(self, ref: str, max_depth: int | None = None) -> query.Walk:
        """Everything upstream of a table, model or column."""
        return query.trace(self.graph, ref, max_depth)

    def path_between(self, start: str, end: str) -> query.PathResult:
        """The shortest connection between two nodes."""
        return query.path(self.graph, start, end)

    def search(self, text: str, limit: int = 20, kinds: list[str] | None = None) -> query.SearchResult:
        return query.search(self.graph, text, limit, kinds)

    def describe(self, ref: str) -> query.Description:
        return query.describe(self.graph, ref)

    def list(self, kinds: list[str] | None = None) -> query.Listing:
        return query.list_nodes(self.graph, kinds)

    def verify(
        self,
        *,
        warehouse: str | Path | None = None,
        seed: int = 1729,
        max_rows: int = 10000,
        models: Sequence[str] | None = None,
        write: bool = True,
    ) -> Verification:
        """Check the graph's column lineage against the data by perturbation (see kerplunk.verify).

        Needs the models built in a local DuckDB warehouse; by default the one the build read.
        With ``write``, saves VERIFY.md and verify.json and marks verified edges in graph.json.
        """
        from kerplunk.verify import annotate, verify

        graph = self.graph
        path = Path(warehouse) if warehouse else self._duckdb_warehouse()
        result = verify(
            graph, self.root, path, seed=seed, max_rows=max_rows, only=list(models) if models else None
        )
        if path.resolve().is_relative_to(self.root.resolve()):
            result.warehouse = str(path.resolve().relative_to(self.root.resolve()))
        if write:
            annotate(graph, result)
            graph.write(self.graph_path)
            self._stamp = self._file_stamp()
            (self.out_dir / "VERIFY.md").write_text(result.markdown(), encoding="utf-8")
            (self.out_dir / "verify.json").write_text(
                json.dumps(result.to_dict(), indent=1) + "\n", encoding="utf-8"
            )
        return result

    def _duckdb_warehouse(self) -> Path:
        for source in self.graph.meta.get("sources", []):
            if source.get("input") == "duckdb" and source.get("status") != "failed":
                path = Path(str(source["name"]).removeprefix("duckdb ").strip())
                return path if path.is_absolute() else self.root / path
        raise query.QueryError(
            "verification needs a local DuckDB warehouse with the models built in it; none was found "
            "(pass --warehouse PATH)"
        )

    def query(self, question: str, depth: int = 1) -> query.QueryResult:
        return query.query(self.graph, question, depth=depth)
