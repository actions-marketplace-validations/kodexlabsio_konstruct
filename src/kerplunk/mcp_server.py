"""`kerplunk mcp`: a stdio MCP server over the graph in kerplunk-out.

The server rereads graph.json whenever it changes, so `kerplunk build --update` in another
terminal is picked up without restarting the agent.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from kerplunk import __version__
from kerplunk.project import Project
from kerplunk.query import QueryError

INSTRUCTIONS = """\
kerplunk answers questions about this repository's data stack: dbt models, sources, tests and
exposures; warehouse and Iceberg tables; Airflow DAGs and tasks; and column-level lineage.
Refer to tables as `name` or `schema.name` and to columns as `table.column`.
Use `impact` before changing a model, table or column, `trace` to find where a value comes from,
`describe` for one node's columns and tests, `search` when unsure of a name, and `path` to see how
two nodes connect. If a tool says there is no graph, ask the user to run `kerplunk build`."""


def build_server(root: Path) -> MCPServer:
    project = Project(root)
    server = MCPServer("kerplunk", version=__version__, instructions=INSTRUCTIONS)

    def run(func: Callable[..., Any], *args: Any) -> dict[str, Any]:
        if not project.has_graph():
            raise ToolError(f"no graph at {project.graph_path}; run `kerplunk build` in the project first")
        try:
            result: dict[str, Any] = func(*args).to_dict()
        except QueryError as error:
            raise ToolError(str(error)) from error
        return result

    @server.tool()
    def impact(ref: str, max_depth: int | None = None) -> dict[str, Any]:
        """Everything downstream of a table, model or column (e.g. `orders` or `orders.amount`):
        derived columns, relations, models that join or filter on it, tests, exposures and the
        orchestration tasks that run them. Use before renaming, dropping or changing it."""
        return run(project.impact, ref, max_depth)

    @server.tool()
    def trace(ref: str, max_depth: int | None = None) -> dict[str, Any]:
        """Everything upstream of a table, model or column: the source columns it is derived from,
        each marked direct (passed through or renamed) or transformed, and the tasks that build it."""
        return run(project.trace, ref, max_depth)

    @server.tool()
    def search(text: str, limit: int = 20, kinds: list[str] | None = None) -> dict[str, Any]:
        """Find nodes whose names or descriptions match the words in `text`. `kinds` limits the
        results to node kinds: source, table, column, model, seed, snapshot, dag, task, test, exposure."""
        return run(project.search, text, limit, kinds)

    @server.tool()
    def describe(ref: str) -> dict[str, Any]:
        """One node in detail: description, path, columns with types and descriptions, tests,
        immediate upstream and downstream neighbours, and for tasks what they run."""
        return run(project.describe, ref)

    @server.tool()
    def path(start: str, end: str) -> dict[str, Any]:
        """The shortest connection between two nodes, following data flow where possible."""
        return run(project.path_between, start, end)

    @server.tool()
    def diff(base: str | None = None, head: str | None = None) -> dict[str, Any]:
        """What the current branch changes, column by column, compared with `base` (default: the
        merge base with main): columns added, removed or changed with their expressions before and
        after, changed join and filter conditions, removed columns still read downstream (breaking),
        changed DAG tasks, and the downstream impact of each change. Use before proposing a merge."""
        from kerplunk.diff import DiffError

        try:
            return run(project.diff, base, head)
        except DiffError as error:
            raise ToolError(str(error)) from error

    @server.tool()
    def query(question: str) -> dict[str, Any]:
        """A free-text question: the best-matching nodes and their connections, plus impact or
        lineage when the question asks for it. Prefer the specific tools when you know the name."""
        return run(project.query, question)

    return server


def serve(root: Path) -> None:
    build_server(root).run("stdio")
