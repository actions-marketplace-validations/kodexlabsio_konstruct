"""graph.html in a real browser (Chromium through Playwright).

Skipped when Playwright's Chromium is not installed (`uv run playwright install chromium`);
CI installs it, and KERPLUNK_REQUIRE_SERVICES turns the skip into a failure there.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from kerplunk.graph import Graph, Node, column_id
from kerplunk.html import write_html

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed")
    with sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch()
        except Error as error:
            if os.environ.get("KERPLUNK_REQUIRE_SERVICES"):
                raise
            pytest.skip(f"Chromium is not available: {str(error).splitlines()[0]}")
        yield instance
        instance.close()


@pytest.fixture
def open_page(browser: Any) -> Iterator[Any]:
    pages = []

    def opener(path: Path) -> Any:
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.problems = []
        page.on("pageerror", lambda error: page.problems.append(str(error)))
        page.on("console", lambda m: page.problems.append(m.text) if m.type in ("error", "warning") else None)
        page.goto(path.as_uri())
        page.wait_for_selector("#nodes [data-id]")
        pages.append(page)
        return page

    yield opener
    for page in pages:
        assert page.problems == [], page.problems
        page.close()


@pytest.mark.dbt
def test_example_graph(jaffle_built_graph: Path, open_page: Any) -> None:
    page = open_page(jaffle_built_graph / "kerplunk-out" / "graph.html")
    ids = page.eval_on_selector_all("#nodes [data-id]", "els => els.map(e => e.getAttribute('data-id'))")
    assert "model.jaffle_shop.orders" in ids
    assert "task.jaffle_shop_daily.dbt_run_marts" in ids
    assert "exposure.jaffle_shop.customer_dashboard" in ids
    assert not any(i.startswith(("test.", "dag.")) or "#" in i for i in ids)
    assert "5 model" in page.inner_text("#counts")

    page.click("[data-id='model.jaffle_shop.orders']")
    panel = page.locator("#panel")
    assert panel.is_visible()
    text = panel.inner_text()
    assert "jaffle_shop.main.orders" in text and "models/orders.sql" in text
    rows = page.eval_on_selector_all("#panel table tr td:first-child", "els => els.map(e => e.textContent)")
    assert rows[:3] == ["order_id", "customer_id", "order_date"]
    assert "not_null_orders_amount" in text

    page.get_by_role("button", name="amount", exact=True).click()
    lineage = panel.inner_text()
    assert "Lineage of orders.amount" in lineage
    assert "stg_payments.amount" in lineage and "raw_payments.amount transformed" in lineage
    assert "Downstream columns (0)" in lineage
    box = page.locator("[data-id='model.jaffle_shop.orders']").bounding_box()
    canvas = page.locator("#graph").bounding_box()
    assert box is not None and canvas is not None
    assert canvas["x"] <= box["x"] and box["x"] + box["width"] <= canvas["x"] + canvas["width"]

    page.keyboard.press("Escape")
    assert not panel.is_visible()


@pytest.mark.dbt
def test_search(jaffle_built_graph: Path, open_page: Any) -> None:
    page = open_page(jaffle_built_graph / "kerplunk-out" / "graph.html")
    page.fill("#search", "lifetime")
    count = page.inner_text("#match-count")
    assert count.startswith("2")
    page.press("#search", "Enter")
    assert page.locator("#panel").is_visible()
    page.fill("#search", "zzzz")
    assert page.inner_text("#match-count").startswith("0") or "No" in page.inner_text("#match-count")


def test_hostile_text_is_shown_as_text(tmp_path: Path, open_page: Any) -> None:
    graph = Graph()
    hostile = "</script><script>window.pwned = 1</script><img src=x onerror='window.pwned=2'>"
    node = graph.add_node(Node("model.p.evil", "model", "evil<b>", description=hostile, relation="db.s.evil"))
    graph.add_column(node, "c", description=hostile)
    graph.warn(hostile)
    page = open_page(write_html(graph, tmp_path / "graph.html"))
    page.click("[data-id='model.p.evil']")
    assert hostile in page.inner_text("#panel")
    assert page.evaluate("window.pwned") is None
    assert page.locator("#panel img").count() == 0


def test_a_large_graph_stays_responsive(tmp_path: Path, open_page: Any) -> None:
    graph = Graph()
    for i in range(400):
        node = graph.add_node(Node(f"model.p.m{i}", "model", f"m{i}", relation=f"db.s.m{i}"))
        for j in range(8):
            graph.add_column(node, f"c{j}")
        for parent in (i // 2, i // 3):
            if parent != i:
                graph.add_edge(f"model.p.m{parent}", node.id, "feeds")
                graph.add_edge(
                    column_id(node.id, "c0"), column_id(f"model.p.m{parent}", "c0"), "derived_from"
                )
    started = time.perf_counter()
    page = open_page(write_html(graph, tmp_path / "graph.html"))
    assert time.perf_counter() - started < 10
    assert page.locator("#nodes [data-id]").count() == 400
    clicked = time.perf_counter()
    page.click("#fit")
    page.locator("[data-id='model.p.m399']").click(force=True)
    page.get_by_role("button", name="c0", exact=True).click()
    assert "Upstream columns" in page.inner_text("#panel")
    assert time.perf_counter() - clicked < 5


def test_an_empty_graph(tmp_path: Path, browser: Any) -> None:
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(write_html(Graph(), tmp_path / "graph.html").as_uri())
    assert page.locator("#empty").inner_text().strip()
    assert errors == []
    page.close()
