"""Dagster: the static parser, its place in the graph, and parity with Dagster itself."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from kerplunk import Project
from kerplunk.dagster import parse_file, parse_source

FIXTURE = Path(__file__).parent / "fixtures" / "dagster" / "definitions.py"


def test_fixture_assets_dependencies_jobs_and_schedule() -> None:
    parsed = parse_file(FIXTURE)
    assets = {a.key: a for a in parsed.assets}
    assert set(assets) == {
        "jaffle_shop_dbt",
        "reporting/customer_ltv",
        "loyal_customers",
        "order_counts",
        "order_totals",
        "crm_export",
    }
    assert assets["jaffle_shop_dbt"].dbt_select == ["staging", "customers", "orders"]
    assert assets["reporting/customer_ltv"].deps == ["customers"]
    assert assets["reporting/customer_ltv"].group == "reporting"
    assert assets["reporting/customer_ltv"].kinds == ["duckdb"]
    assert assets["reporting/customer_ltv"].sql and "main.customers" in assets["reporting/customer_ltv"].sql
    assert assets["loyal_customers"].deps == ["reporting/customer_ltv"]
    assert assets["order_totals"].description == "Total value per day"
    assert assets["order_counts"].deps == ["orders"]
    assert assets["crm_export"].deps == ["loyal_customers", "order_totals"]
    jobs = {j.name: j for j in parsed.jobs}
    assert jobs["reporting_job"].selection == ["group:reporting"]
    assert jobs["reporting_job"].schedule == "0 6 * * *"
    assert jobs["everything"].selection == ["*"]
    assert parsed.warnings == []


def test_keys_resources_and_selections(tmp_path: Path) -> None:
    source = textwrap.dedent(
        """
        from dagster import AssetKey, AssetSelection, asset, define_asset_job
        from dagster_duckdb import DuckDBResource

        PREFIX = "lake"

        @asset(key=AssetKey(["lake", "events"]))
        def events(warehouse: DuckDBResource):
            ...

        @asset(name="sessions", key_prefix=PREFIX, deps=[events, "lake/users", AssetKey("ref")])
        def build_sessions(context, warehouse: DuckDBResource):
            warehouse.get_connection().execute("insert into lake.sessions select * from lake.events")

        job = define_asset_job("lake_job", selection=AssetSelection.keys("lake/events", ["lake", "sessions"]))
        other = define_asset_job(f"job_{PREFIX}_{unknown}", selection="*")
        """
    )
    parsed = parse_source(tmp_path / "d.py", source)
    assets = {a.key: a for a in parsed.assets}
    assert set(assets) == {"lake/events", "lake/sessions"}
    assert assets["lake/events"].deps == []
    assert assets["lake/sessions"].deps == ["lake/events", "lake/users", "ref"]
    assert assets["lake/sessions"].sql == "insert into lake.sessions select * from lake.events"
    assert parsed.jobs[0].selection == ["lake/events", "lake/sessions"]
    assert any("not a constant" in w for w in parsed.warnings)


def test_round_trip() -> None:
    from kerplunk.dagster import DagsterFile

    parsed = parse_file(FIXTURE)
    assert DagsterFile.from_dict(parsed.to_dict()) == parsed


@pytest.mark.dbt
def test_dagster_assets_join_dbt_and_warehouse_lineage(built_jaffle: Path, tmp_path: Path) -> None:
    root = tmp_path / "shop"
    shutil.copytree(built_jaffle, root)
    shutil.rmtree(root / "dags")
    (root / "orchestration").mkdir()
    shutil.copy(FIXTURE, root / "orchestration" / "definitions.py")
    from .conftest import run_dbt

    run_dbt(root, "docs", "generate")
    project = Project(root)
    result = project.build(run_dbt=False)
    graph = result.graph
    assert result.inputs["dagster_assets"] == 6
    assert any(s["input"] == "dagster" and s["detail"] == "6 assets, 2 jobs" for s in graph.meta["sources"])

    dbt_task = graph.nodes["task.dagster.jaffle_shop_dbt"]
    runs = {graph.nodes[e.target].name for e in graph.out_edges(dbt_task.id, "runs")}
    assert runs == {"stg_customers", "stg_orders", "stg_payments", "customers", "orders"}

    ltv = graph.nodes["task.dagster.reporting/customer_ltv"]
    warehouse_table = graph.node_for_relation("jaffle_shop.reporting.customer_ltv")
    assert warehouse_table is not None
    assert (ltv.id, warehouse_table.id) in {(e.source, e.target) for e in graph.out_edges(ltv.id, "feeds")}
    trace = project.trace("customer_ltv.lifetime_value")
    assert [c.label for c in trace.columns][:1] == ["customers.customer_lifetime_value"]

    impact = project.impact("customers.customer_lifetime_value")
    assert "reporting/customer_ltv" in {t.name for t in impact.tasks}
    downstream = project.impact("reporting/customer_ltv")
    assert "loyal_customers" in {t.name for t in downstream.tasks}

    job = graph.nodes["dag.reporting_job"]
    assert job.attrs["schedule"] == "0 6 * * *"
    assert {graph.nodes[e.target].name for e in graph.out_edges(job.id, "runs")} == {
        "reporting/customer_ltv",
        "loyal_customers",
        "order_counts",
        "order_totals",
    }


@pytest.mark.dagster_parity
def test_static_parse_matches_dagster(built_jaffle: Path, tmp_path: Path) -> None:
    """Load the fixture with Dagster and compare asset keys, dependencies and job selections."""
    try:
        import importlib.util

        import dagster  # noqa: F401
    except ImportError:
        pytest.skip("Dagster is not installed")
    root = tmp_path / "shop"
    shutil.copytree(built_jaffle, root)
    shutil.copy(FIXTURE, root / "definitions.py")
    from .conftest import run_dbt

    run_dbt(root, "parse")
    spec = importlib.util.spec_from_file_location("kerplunk_fixture_definitions", root / "definitions.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    definitions = module.defs
    asset_graph = definitions.resolve_asset_graph()
    truth = {
        key.to_user_string(): sorted(p.to_user_string() for p in asset_graph.get(key).parent_keys)
        for key in asset_graph.get_all_asset_keys()
    }
    parsed = parse_file(root / "definitions.py")
    python_assets = {a.key: a.deps for a in parsed.assets if a.dbt_select is None}
    for key, deps in python_assets.items():
        assert sorted(deps) == truth[key], key

    project = Project(root)
    project.build(run_dbt=False)
    dbt_models = {
        project.graph.nodes[e.target].name
        for e in project.graph.out_edges("task.dagster.jaffle_shop_dbt", "runs")
    }
    dagster_dbt = {k for k in truth if k not in python_assets and truth[k] and not k.startswith("raw_")}
    assert dbt_models == dagster_dbt

    jobs = {job.name: job for job in definitions.resolve_all_job_defs()}
    reporting = {k.to_user_string() for k in jobs["reporting_job"].asset_layer.selected_asset_keys}
    ours = {
        graph_node.name
        for graph_node in (
            project.graph.nodes[e.target] for e in project.graph.out_edges("dag.reporting_job", "runs")
        )
    }
    assert ours == reporting


def test_multi_asset_prefixes_selections_and_schedules(tmp_path: Path) -> None:
    source = textwrap.dedent(
        """
        from dagster import (AssetDep, AssetKey, AssetOut, AssetSelection, asset, build_schedule_from_partitioned_job,
                             define_asset_job, multi_asset)

        @multi_asset(key_prefix=["mart"], group_name="marts", outs={"a": AssetOut(), "b": AssetOut(key=AssetKey(["x", "b"]))},
                     deps=[AssetDep("raw/events")])
        def both():
            ...

        @asset(key_prefix="mart", group_name="marts")
        def c(a):
            ...

        marts = define_asset_job("marts", selection=AssetSelection.groups("marts") | AssetSelection.key_prefixes(["x"]))
        downstream = define_asset_job(name="down", selection=AssetSelection.keys("mart/a").downstream())
        partitioned = build_schedule_from_partitioned_job(marts)
        """
    )
    parsed = parse_source(tmp_path / "d.py", source)
    assets = {a.key: a for a in parsed.assets}
    assert set(assets) == {"mart/a", "x/b", "mart/c"}
    assert assets["mart/a"].deps == ["raw/events"]
    assert assets["mart/a"].group == "marts"
    assert assets["mart/c"].deps == ["mart/a"]
    jobs = {j.name: j for j in parsed.jobs}
    assert jobs["marts"].selection == ["group:marts", "prefix:x"]
    assert jobs["marts"].schedule == "partitioned"
    assert jobs["down"].selection == ["mart/a"]


def test_dagster_in_the_build(tmp_path: Path) -> None:
    import duckdb

    con = duckdb.connect(str(tmp_path / "w.duckdb"))
    con.execute("create schema lake")
    con.execute("create table lake.events (id int, kind varchar)")
    con.close()
    pipelines = tmp_path / "pipelines"
    pipelines.mkdir()
    (pipelines / "assets.py").write_text(
        textwrap.dedent(
            """
            from dagster import AssetSelection, asset, define_asset_job

            @asset(key_prefix="lake", group_name="lake")
            def daily(events):
                conn.execute("create table lake.daily as select kind, count(*) as n from lake.events group by kind")

            @asset(key_prefix="lake", group_name="lake")
            def report(daily):
                ...

            job = define_asset_job("lake_job", selection=AssetSelection.groups("lake"))
            """
        )
    )
    (pipelines / "broken.py").write_text("from dagster import asset\n@asset\ndef x(:\n")
    (tmp_path / "kerplunk.toml").write_text('[dagster]\npaths = ["pipelines"]\n')
    project = Project(tmp_path)
    result = project.build()
    graph = result.graph
    assert result.inputs["dagster_assets"] == 2
    assert any("dagster: pipelines/broken.py: syntax error" in w for w in graph.warnings)
    daily_table = graph.node_for_relation("w.lake.daily")
    assert daily_table is not None
    assert [e.target for e in graph.out_edges(f"{daily_table.id}#kind", "derived_from")] == [
        "table.w.lake.events#kind"
    ]
    events = graph.node_for_relation("w.lake.events")
    assert events is not None
    assert "task.dagster.lake/daily" in {e.target for e in graph.out_edges(events.id, "feeds")}
    report = graph.nodes["task.dagster.lake/report"]
    assert {graph.nodes[e.source].name for e in graph.in_edges(report.id, "feeds")} == {"daily"}
    assert {graph.nodes[e.target].name for e in graph.out_edges("dag.lake_job", "runs")} == {
        "lake/daily",
        "lake/report",
    }
    assert project.describe("lake/daily").node.kind == "task"
