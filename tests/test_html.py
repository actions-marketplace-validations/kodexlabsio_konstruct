from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from kerplunk.graph import Graph, Node, column_id
from kerplunk.html import write_html

HOSTILE = "Ends here </script><script>alert(1)</script> and\u2028continues & <b>more</b>"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def sample_graph() -> Graph:
    g = Graph()
    g.meta = {"root": "/tmp/project", "generated_at": "2026-11-06T10:00:00+00:00"}
    raw = g.add_node(Node("source.shop.raw.orders", "source", "orders", relation="db.raw.orders"))
    stg = g.add_node(
        Node(
            "model.shop.stg_orders",
            "model",
            "stg_orders",
            relation="db.main.stg_orders",
            description=HOSTILE,
            attrs={"materialized": "view"},
        )
    )
    final = g.add_node(Node("model.shop.orders", "model", "orders", relation="db.main.orders"))
    task = g.add_node(Node("task.nightly.dbt_run", "task", "dbt_run", attrs={"operator": "BashOperator"}))
    dag = g.add_node(Node("dag.nightly", "dag", "nightly"))
    test = g.add_node(Node("test.shop.not_null_orders_id", "test", "not_null_orders_id"))
    for rel, cols in [
        (raw, ["id", "amount", "status"]),
        (stg, ["order_id", "amount"]),
        (final, ["order_id", "amount"]),
    ]:
        for col in cols:
            g.add_column(rel, col, data_type="integer")
    g.add_edge(raw.id, stg.id, "feeds")
    g.add_edge(stg.id, final.id, "feeds")
    g.add_edge(column_id(stg.id, "order_id"), column_id(raw.id, "id"), "derived_from", transform="direct")
    g.add_edge(
        column_id(stg.id, "amount"), column_id(raw.id, "amount"), "derived_from", transform="transformed"
    )
    g.add_edge(column_id(final.id, "amount"), column_id(stg.id, "amount"), "derived_from")
    g.add_edge(column_id(raw.id, "status"), stg.id, "feeds", role="reference")
    g.add_edge(test.id, column_id(final.id, "order_id"), "tests")
    g.add_edge(dag.id, task.id, "runs")
    g.add_edge(task.id, final.id, "runs")
    g.warn("dbt: model orders: could not parse a statement")
    return g


def embedded_json(page: str) -> str:
    match = re.search(r'<script type="application/json" id="kerplunk-data">(.*?)</script>', page, re.S)
    assert match is not None
    return match.group(1)


def inline_script(page: str) -> str:
    match = re.search(r"<script>(.*?)</script>", page, re.S)
    assert match is not None
    return match.group(1)


def test_writes_a_file_with_the_graph_embedded(tmp_path: Path) -> None:
    graph = sample_graph()
    out = write_html(graph, tmp_path / "out" / "graph.html")
    assert out == tmp_path / "out" / "graph.html"
    assert out.is_file()
    page = out.read_text(encoding="utf-8")
    assert json.loads(embedded_json(page)) == graph.to_dict()


def test_embedded_data_cannot_break_out_of_its_script_tag(tmp_path: Path) -> None:
    page = write_html(sample_graph(), tmp_path / "graph.html").read_text(encoding="utf-8")
    assert "</script><script>alert" not in page
    assert "<b>more" not in page
    assert "\u2028" not in page
    assert page.count("</script>") == 2
    data = json.loads(embedded_json(page))
    stg = next(n for n in data["nodes"] if n["id"] == "model.shop.stg_orders")
    assert stg["description"] == HOSTILE


def test_page_references_no_external_resources(tmp_path: Path) -> None:
    page = write_html(sample_graph(), tmp_path / "graph.html").read_text(encoding="utf-8")
    remainder = page.replace(SVG_NAMESPACE, "")
    assert "http://" not in remainder
    assert "https://" not in remainder
    assert not re.search(r"\b(src|href)\s*=", remainder)
    assert "@import" not in remainder
    assert "url(" not in remainder.replace("url(#arrow)", "")


def test_inline_script_is_valid_javascript(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    page = write_html(sample_graph(), tmp_path / "graph.html").read_text(encoding="utf-8")
    script = tmp_path / "viewer.js"
    script.write_text(inline_script(page), encoding="utf-8")
    result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
