from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client, StdioServerParameters

from kerplunk.mcp_server import build_server

pytestmark = pytest.mark.dbt


def structured(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content
    data = result.structured_content
    assert isinstance(data, dict)
    return data


async def _exercise(client: Client) -> None:
    tools = await client.list_tools()
    assert {t.name for t in tools.tools} == {"impact", "trace", "search", "describe", "path"}
    impact = structured(await client.call_tool("impact", {"ref": "stg_payments.amount"}))
    assert impact["exposures"][0]["label"] == "customer_dashboard"
    trace = structured(await client.call_tool("trace", {"ref": "orders.amount", "max_depth": 1}))
    assert [c["label"] for c in trace["columns"]] == ["stg_payments.amount"]
    search = structured(
        await client.call_tool("search", {"text": "lifetime", "limit": 2, "kinds": ["column"]})
    )
    assert len(search["results"]) == 2
    describe = structured(await client.call_tool("describe", {"ref": "customers"}))
    assert describe["kind"] == "model"
    path = structured(await client.call_tool("path", {"start": "raw_orders", "end": "orders"}))
    assert path["found"] is True
    missing = await client.call_tool("impact", {"ref": "nope"})
    assert missing.is_error
    assert "nothing in the graph matches" in str(missing.content)


def test_in_process(jaffle_built_graph: Path) -> None:
    async def main() -> None:
        async with Client(build_server(jaffle_built_graph)) as client:
            await _exercise(client)

    anyio.run(main)


def test_over_stdio(jaffle_built_graph: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "kerplunk.cli", "mcp", "--path", str(jaffle_built_graph)], env=env
    )

    async def main() -> None:
        async with Client(params) as client:
            await _exercise(client)

    anyio.run(main)


def test_reloads_a_rebuilt_graph_and_reports_a_missing_one(jaffle_built_graph: Path, tmp_path: Path) -> None:
    project = tmp_path / "p"
    (project / "kerplunk-out").mkdir(parents=True)

    async def main() -> None:
        async with Client(build_server(project)) as client:
            missing = await client.call_tool("describe", {"ref": "orders"})
            assert missing.is_error and "kerplunk build" in str(missing.content)
            shutil.copy(
                jaffle_built_graph / "kerplunk-out" / "graph.json", project / "kerplunk-out" / "graph.json"
            )
            found = structured(await client.call_tool("describe", {"ref": "orders"}))
            assert found["kind"] == "model"

    anyio.run(main)
