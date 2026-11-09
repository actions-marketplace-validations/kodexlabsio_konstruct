"""The documentation stays in step with the code it describes."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_the_mcp_reference_lists_every_tool() -> None:
    tree = ast.parse((ROOT / 'konstruct' / 'mcp_server.py').read_text())
    build = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'build_server')
    tools = {
        node.name
        for node in build.body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(d, ast.Call) and getattr(d.func, 'attr', '') == 'tool' for d in node.decorator_list
        )
    }
    page = (ROOT / 'docs' / 'reference' / 'mcp.md').read_text()
    listed = {line.split('`')[1] for line in page.splitlines() if line.startswith('| `')}
    assert listed == tools


def test_every_command_has_a_row_in_the_cli_reference() -> None:
    import argparse

    from konstruct.cli import parser

    action = next(a for a in parser()._actions if isinstance(a, argparse._SubParsersAction))
    commands = set(action.choices)
    page = (ROOT / 'docs' / 'reference' / 'cli.md').read_text()
    missing = [c for c in sorted(commands) if f'`konstruct {c}' not in page]
    assert missing == []
