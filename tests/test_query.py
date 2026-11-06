from __future__ import annotations

from pathlib import Path

import pytest

from kerplunk import query
from kerplunk.graph import Graph

pytestmark = pytest.mark.dbt


@pytest.fixture
def graph(jaffle_built_graph: Path) -> Graph:
    return query.load_graph(jaffle_built_graph / "kerplunk-out")


def labels(items: list[dict[str, object]]) -> list[object]:
    return [item["label"] for item in items]


def test_impact_of_a_staging_column(graph: Graph) -> None:
    result = query.impact(graph, "stg_payments.amount")
    assert result["target"]["id"] == "model.jaffle_shop.stg_payments#amount"
    assert set(labels(result["columns"])) == {
        "customers.customer_lifetime_value",
        "orders.amount",
        "orders.credit_card_amount",
        "orders.coupon_amount",
        "orders.bank_transfer_amount",
        "orders.gift_card_amount",
        "customer_ltv.lifetime_value",
    }
    assert set(labels(result["relations"])) == {"customers", "orders"}
    assert labels(result["joins_and_filters"]) == ["customer_ltv"]
    assert labels(result["exposures"]) == ["customer_dashboard"]
    assert set(labels(result["tasks"])) == {
        "jaffle_shop_daily.dbt_run_marts",
        "jaffle_shop_daily.build_customer_ltv",
    }
    assert "not_null_orders_amount" in labels(result["tests"])
    ltv = next(c for c in result["columns"] if c["label"] == "customer_ltv.lifetime_value")
    assert ltv["depth"] == 2 and ltv["transform"] == "direct"
    assert ltv["through"] == "jaffle_shop_daily.build_customer_ltv"


def test_impact_of_a_join_key(graph: Graph) -> None:
    result = query.impact(graph, "stg_orders.customer_id")
    assert "customers" in labels(result["joins_and_filters"])
    assert "orders.customer_id" in labels(result["columns"])


def test_impact_of_a_relation_and_depth(graph: Graph) -> None:
    result = query.impact(graph, "stg_orders")
    assert {"orders", "customers", "customer_ltv"} <= set(labels(result["relations"]))
    shallow = query.impact(graph, "stg_orders", max_depth=1)
    assert "customer_ltv" not in labels(shallow["relations"])


def test_trace_to_the_seed(graph: Graph) -> None:
    result = query.trace(graph, "customer_ltv.lifetime_value")
    assert labels(result["columns"]) == [
        "customers.customer_lifetime_value",
        "stg_payments.amount",
        "raw_payments.amount",
    ]
    assert [c["transform"] for c in result["columns"]] == ["direct", "transformed", "transformed"]
    assert "jaffle_shop_daily.build_customer_ltv" in labels(result["tasks"])
    assert labels(result["filtered_by"]) == ["customers.customer_lifetime_value"]


def test_path_search_and_describe(graph: Graph) -> None:
    result = query.path(graph, "raw_orders.id", "customers.number_of_orders")
    assert result["found"]
    assert labels(result["steps"]) == [
        "raw_orders.id",
        "stg_orders.order_id",
        "customers.number_of_orders",
    ]
    assert query.search(graph, "lifetime value")["results"][0]["label"] == "customers.customer_lifetime_value"
    assert query.search(graph, "payments", kinds=["seed"])["results"][0]["label"] == "raw_payments"
    described = query.describe(graph, "orders")
    assert described["relation"] == "jaffle_shop.main.orders"
    assert [c["label"] for c in described["columns"]][:2] == ["orders.order_id", "orders.customer_id"]
    assert "unique_orders_order_id" in described["tests"]
    assert "customer_dashboard (exposure)" in described["downstream"]
    task = query.describe(graph, "dbt_run_marts")
    assert task["runs"] == ["customers", "orders"]
    assert task["after"] == ["dbt_run_staging"] and task["before"] == ["dbt_test"]
    assert task["upstream"] == [] and task["downstream"] == []


def test_query_finds_nodes_and_follows_intent(graph: Graph) -> None:
    result = query.query(graph, "what breaks if I change the payment method in stg_payments?")
    assert result["matches"][0]["label"] == "stg_payments.payment_method"
    assert "impact" in result
    lineage = query.query(graph, "where does customer lifetime value come from")
    assert "lineage" in lineage
    assert lineage["lineage"]["target"]["label"] == "customers.customer_lifetime_value"
    assert query.query(graph, "zzz qqq")["matches"] == []


def test_errors_suggest_and_disambiguate(graph: Graph) -> None:
    with pytest.raises(query.QueryError, match="did you mean"):
        query.impact(graph, "payment")
    with pytest.raises(query.QueryError, match="nothing in the graph"):
        query.trace(graph, "zzz")
    graph.add_node(
        graph.nodes["model.jaffle_shop.orders"].__class__(
            "table.x.orders", "table", "orders", relation="x.y.orders"
        )
    )
    with pytest.raises(query.QueryError, match="ambiguous"):
        query.describe(graph, "orders")


def test_text_rendering(graph: Graph) -> None:
    impact = query.render_walk(query.impact(graph, "stg_payments.amount"))
    assert impact.startswith("Downstream of stg_payments.amount (model)")
    assert "Relations that join, filter or group on it (1)" in impact
    assert (
        "customer_ltv.lifetime_value  [table, DOUBLE, direct, via jaffle_shop_daily.build_customer_ltv]"
        in impact
    )
    trace = query.render_walk(query.trace(graph, "raw_payments.amount"))
    assert trace.splitlines()[2:] == [
        "Orchestration (1)",
        "   1  jaffle_shop_daily.dbt_seed  [task, seed]  dags/jaffle_shop_daily.py",
    ]
    path = query.render_path(query.path(graph, "raw_payments", "customer_dashboard"))
    assert path.splitlines()[1:] == [
        "  raw_payments (seed)",
        "  -feeds-> stg_payments (model)",
        "  -feeds-> customers (model)",
        "  -feeds-> customer_dashboard (exposure)",
    ]
    assert "Columns (9)" in query.render_describe(query.describe(graph, "orders"))
    assert query.render_search(query.search(graph, "zzz")) == "Nothing matches 'zzz'."
    rendered = query.render_query(graph, query.query(graph, "stg_payments payment method"))
    assert rendered.startswith("Best matches")


def test_load_graph_without_a_build(tmp_path: Path) -> None:
    with pytest.raises(query.QueryError, match="run `kerplunk build` first"):
        query.load_graph(tmp_path)
