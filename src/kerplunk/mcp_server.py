"""`kerplunk mcp`: a stdio MCP server over the graph in kerplunk-out.

The server rereads graph.json whenever it changes, so `kerplunk build --update` in another
terminal is picked up without restarting the agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from kerplunk import __version__, query
from kerplunk.graph import Graph

INSTRUCTIONS = """\
kerplunk answers questions about this repository's data stack: dbt models, sources, tests and
exposures; warehouse and Iceberg tables; Airflow DAGs and tasks; and column-level lineage.
Refer to tables as `name` or `schema.name` and to columns as `table.column`.
Use `impact` before changing a model, table or column, `trace` to find where a value comes from,
`describe` for one node's columns and tests, `search` when unsure of a name, and `path` to see how
two nodes connect. If a tool says there is no graph, ask the user to run `kerplunk build`."""


class GraphSource:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._graph: Graph | None = None
        self._stamp: tuple[float, int] | None = None

    def get(self) -> Graph:
        from kerplunk.cli import find_out_dir

        path = find_out_dir(self.root) / "graph.json"
        try:
            stat = path.stat()
        except OSError as error:
            raise ToolError(f"no graph at {path}; run `kerplunk build` in the project first") from error
        stamp = (stat.st_mtime, stat.st_size)
        if self._graph is None or stamp != self._stamp:
            self._graph = Graph.read(path)
            self._stamp = stamp
        return self._graph


def build_server(root: Path) -> MCPServer:
    source = GraphSource(root.resolve())
    server = MCPServer("kerplunk", version=__version__, instructions=INSTRUCTIONS)

    def run(func: Any, *args: Any) -> dict[str, Any]:
        try:
            result: dict[str, Any] = func(source.get(), *args)
        except query.QueryError as error:
            raise ToolError(str(error)) from error
        return result

    @server.tool()
    def impact(ref: str, max_depth: int | None = None) -> dict[str, Any]:
        """Everything downstream of a table, model or column (e.g. `orders` or `orders.amount`):
        derived columns, relations, models that join or filter on it, tests, exposures and the
        orchestration tasks that run them. Use before renaming, dropping or changing it."""
        return run(query.impact, ref, max_depth)

    @server.tool()
    def trace(ref: str, max_depth: int | None = None) -> dict[str, Any]:
        """Everything upstream of a table, model or column: the source columns it is derived from,
        each marked direct (passed through or renamed) or transformed, and the tasks that build it."""
        return run(query.trace, ref, max_depth)

    @server.tool()
    def search(text: str, limit: int = 20, kinds: list[str] | None = None) -> dict[str, Any]:
        """Find nodes whose names or descriptions match the words in `text`. `kinds` limits the
        results to node kinds: source, table, column, model, seed, snapshot, dag, task, test, exposure."""
        return run(query.search, text, limit, kinds)

    @server.tool()
    def describe(ref: str) -> dict[str, Any]:
        """One node in detail: description, path, columns with types and descriptions, tests,
        immediate upstream and downstream neighbours, and for tasks what they run."""
        return run(query.describe, ref)

    @server.tool()
    def path(start: str, end: str) -> dict[str, Any]:
        """The shortest connection between two nodes, following data flow where possible."""
        return run(query.path, start, end)

    return server


def serve(root: Path) -> None:
    build_server(root).run("stdio")
