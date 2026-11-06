"""The kerplunk command line."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from kerplunk import __version__

OUT_DIR = "kerplunk-out"


def find_out_dir(start: Path) -> Path:
    """The nearest kerplunk-out directory with a graph, looking up from ``start``."""
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / OUT_DIR / "graph.json").is_file():
            return directory / OUT_DIR
        if (directory / "graph.json").is_file() and directory.name == OUT_DIR:
            return directory
    return start / OUT_DIR


def _print(data: Any, as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        print(text)


def cmd_build(args: argparse.Namespace) -> int:
    from kerplunk.build import build
    from kerplunk.config import load_config

    root = Path(args.path)
    if not root.is_dir():
        print(f"kerplunk: {root} is not a directory", file=sys.stderr)
        return 1
    config = load_config(root, Path(args.out) if args.out else None)
    if args.no_dbt:
        config.run_dbt = False
    if args.no_warehouse:
        config.warehouses = []
    if args.dialect:
        config.dialect = args.dialect
    result = build(config, update=args.update)
    graph = result.graph
    stats = graph.stats()
    kinds = ", ".join(
        f"{count} {kind}{'s' if count != 1 else ''}"
        for kind, count in sorted(stats.items())
        if not kind.startswith("edge:")
    )
    lineage = stats.get("edge:derived_from", 0)
    print(f"kerplunk: {kinds}")
    print(f"kerplunk: {lineage} column lineage edges, {len(graph.edges)} edges in all")
    if args.update:
        total = result.cache_hits + result.cache_misses
        print(f"kerplunk: reused {result.cache_hits} of {total} cached analyses")
    if graph.warnings:
        print(f"kerplunk: {len(graph.warnings)} warning(s); see {_relative(result.outputs[1])}")
    print(f"kerplunk: wrote {', '.join(_relative(p) for p in result.outputs)} in {result.seconds:.1f}s")
    return 0


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _graph(args: argparse.Namespace) -> Any:
    from kerplunk.query import load_graph

    out = Path(args.graph).parent if args.graph else find_out_dir(Path(args.path))
    return load_graph(out)


def cmd_impact(args: argparse.Namespace) -> int:
    from kerplunk.query import impact, render_walk

    result = impact(_graph(args), args.ref, args.depth)
    _print(result, args.json, render_walk(result))
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    from kerplunk.query import render_walk, trace

    result = trace(_graph(args), args.ref, args.depth)
    _print(result, args.json, render_walk(result))
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    from kerplunk.query import path, render_path

    result = path(_graph(args), args.start, args.end)
    _print(result, args.json, render_path(result))
    return 0 if result["found"] else 1


def cmd_search(args: argparse.Namespace) -> int:
    from kerplunk.query import render_search, search

    result = search(_graph(args), " ".join(args.text), args.limit, args.kind)
    _print(result, args.json, render_search(result))
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    from kerplunk.query import describe, render_describe

    result = describe(_graph(args), args.ref)
    _print(result, args.json, render_describe(result))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    from kerplunk.query import query, render_query

    graph = _graph(args)
    result = query(graph, " ".join(args.question), depth=args.depth)
    _print(result, args.json, render_query(graph, result))
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    from kerplunk.skill import install

    for line in install(
        agents=args.agent,
        project=Path(args.project).resolve() if args.project else None,
        mcp=args.mcp,
        force=args.force,
    ):
        print(f"kerplunk: {line}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from kerplunk.mcp_server import serve

    serve(Path(args.path))
    return 0


def _add_graph_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--path", default=".", help="project directory; the graph is found in kerplunk-out here or above"
    )
    parser.add_argument("--graph", help="path to a graph.json, instead of looking for kerplunk-out")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="kerplunk",
        description="Turn a data stack into a queryable graph with column-level lineage.",
    )
    root.add_argument("--version", action="version", version=f"kerplunk {__version__}")
    root.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")
    commands = root.add_subparsers(dest="command", required=True, metavar="command")

    build = commands.add_parser("build", help="build kerplunk-out/graph.json, REPORT.md and graph.html")
    build.add_argument("path", nargs="?", default=".", help="project directory (default: .)")
    build.add_argument("--update", action="store_true", help="reuse cached analyses of unchanged inputs")
    build.add_argument("--out", help="output directory (default: <path>/kerplunk-out)")
    build.add_argument("--no-dbt", action="store_true", help="never run dbt; use existing artefacts")
    build.add_argument("--no-warehouse", action="store_true", help="do not connect to any warehouse")
    build.add_argument("--dialect", help="SQL dialect for sqlglot (default: from the dbt adapter)")
    build.set_defaults(func=cmd_build)

    query = commands.add_parser("query", help="answer a free-text question from the graph")
    query.add_argument("question", nargs="+")
    query.add_argument("--depth", type=int, default=1, help="neighbourhood depth around matches")
    _add_graph_args(query)
    query.set_defaults(func=cmd_query)

    for name, func, text in (
        ("impact", cmd_impact, "everything downstream of a table or table.column"),
        ("trace", cmd_trace, "everything upstream of a table or table.column"),
    ):
        command = commands.add_parser(name, help=text)
        command.add_argument("ref", help="table, model or column, e.g. orders or orders.amount")
        command.add_argument("--depth", type=int, help="stop after this many hops")
        _add_graph_args(command)
        command.set_defaults(func=func)

    path = commands.add_parser("path", help="shortest connection between two nodes")
    path.add_argument("start")
    path.add_argument("end")
    _add_graph_args(path)
    path.set_defaults(func=cmd_path)

    search = commands.add_parser("search", help="find nodes by name or description")
    search.add_argument("text", nargs="+")
    search.add_argument("--kind", action="append", help="limit to a node kind (repeatable)")
    search.add_argument("--limit", type=int, default=20)
    _add_graph_args(search)
    search.set_defaults(func=cmd_search)

    describe = commands.add_parser("describe", help="details, columns, tests and neighbours of a node")
    describe.add_argument("ref")
    _add_graph_args(describe)
    describe.set_defaults(func=cmd_describe)

    install = commands.add_parser("install", help="register the /kerplunk skill for Claude Code and Cursor")
    install.add_argument(
        "--agent", choices=["claude", "cursor", "both"], default="both", help="which agent to install for"
    )
    install.add_argument("--project", help="install into this project instead of your home directory")
    install.add_argument("--mcp", action="store_true", help="also register the MCP server (needs --project)")
    install.add_argument("--force", action="store_true", help="overwrite a skill that was edited by hand")
    install.set_defaults(func=cmd_install)

    mcp = commands.add_parser("mcp", help="run the MCP server on stdio")
    mcp.add_argument("--path", default=".", help="project directory with kerplunk-out")
    mcp.set_defaults(func=cmd_mcp)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    from kerplunk.config import ConfigError
    from kerplunk.query import QueryError
    from kerplunk.skill import InstallError

    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="kerplunk: %(message)s",
        stream=sys.stderr,
    )
    try:
        code: int = args.func(args)
    except (QueryError, ConfigError, InstallError) as error:
        print(f"kerplunk: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return code


if __name__ == "__main__":
    sys.exit(main())
