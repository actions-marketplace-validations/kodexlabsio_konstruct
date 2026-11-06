"""Dagster definitions over the jaffle_shop example: dbt assets, Python assets and a schedule."""

from pathlib import Path

from dagster import (
    AssetExecutionContext,
    AssetIn,
    AssetKey,
    AssetOut,
    AssetSelection,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
    multi_asset,
)
from dagster_dbt import DbtCliResource, dbt_assets
from dagster_duckdb import DuckDBResource

MANIFEST = Path(__file__).parent / "target" / "manifest.json"


@dbt_assets(manifest=MANIFEST, select="staging customers orders")
def jaffle_shop_dbt(context: AssetExecutionContext, dbt: DbtCliResource):
    """Build the jaffle_shop dbt models."""
    yield from dbt.cli(["build"], context=context).stream()


@asset(key_prefix=["reporting"], group_name="reporting", deps=[AssetKey("customers")], kinds={"duckdb"})
def customer_ltv(duckdb: DuckDBResource) -> None:
    """Lifetime value per customer, from the customers model."""
    with duckdb.get_connection() as conn:
        conn.execute(
            "create or replace table reporting.customer_ltv as "
            "select customer_id, customer_lifetime_value as lifetime_value from main.customers"
        )


@asset(group_name="reporting", ins={"ltv": AssetIn(key=["reporting", "customer_ltv"])})
def loyal_customers(ltv):
    """Customers worth more than 100."""
    return ltv


@multi_asset(
    outs={
        "order_counts": AssetOut(group_name="reporting"),
        "order_totals": AssetOut(group_name="reporting", description="Total value per day"),
    },
    deps=["orders"],
)
def daily_orders():
    return 1, 2


@asset(group_name="exports")
def crm_export(loyal_customers, order_totals):
    """Push loyal customers to the CRM."""


reporting_job = define_asset_job("reporting_job", selection=AssetSelection.groups("reporting"))
everything = define_asset_job("everything", selection="*")

defs = Definitions(
    assets=[jaffle_shop_dbt, customer_ltv, loyal_customers, daily_orders, crm_export],
    jobs=[reporting_job, everything],
    schedules=[ScheduleDefinition(job=reporting_job, cron_schedule="0 6 * * *")],
    resources={
        "dbt": DbtCliResource(project_dir=str(Path(__file__).parent)),
        "duckdb": DuckDBResource(database=str(Path(__file__).parent / "jaffle_shop.duckdb")),
    },
)
