"""Dagster definitions over the jaffle_shop example: dbt assets, Python assets, an op-based job,
schedules and sensors."""

from pathlib import Path

from dagster import (
    AssetExecutionContext,
    AssetIn,
    AssetKey,
    AssetOut,
    AssetSelection,
    DagsterRunStatus,
    Definitions,
    In,
    Nothing,
    ScheduleDefinition,
    asset,
    asset_sensor,
    define_asset_job,
    graph,
    job,
    multi_asset,
    op,
    run_failure_sensor,
    run_status_sensor,
    schedule,
)
from dagster_dbt import DbtCliResource, dbt_assets
from dagster_duckdb import DuckDBResource

MANIFEST = Path(__file__).parent / 'target' / 'manifest.json'


@dbt_assets(manifest=MANIFEST, select='staging customers orders')
def jaffle_shop_dbt(context: AssetExecutionContext, dbt: DbtCliResource):
    """Build the jaffle_shop dbt models."""
    yield from dbt.cli(['build'], context=context).stream()


@asset(key_prefix=['reporting'], group_name='reporting', deps=[AssetKey('customers')], kinds={'duckdb'})
def customer_ltv(duckdb: DuckDBResource) -> None:
    """Lifetime value per customer, from the customers model."""
    with duckdb.get_connection() as conn:
        conn.execute(
            'create or replace table reporting.customer_ltv as '
            'select customer_id, customer_lifetime_value as lifetime_value from main.customers'
        )


@asset(group_name='reporting', ins={'ltv': AssetIn(key=['reporting', 'customer_ltv'])})
def loyal_customers(ltv):
    """Customers worth more than 100."""
    return ltv


@multi_asset(
    outs={
        'order_counts': AssetOut(group_name='reporting'),
        'order_totals': AssetOut(group_name='reporting', description='Total value per day'),
    },
    deps=['orders'],
)
def daily_orders():
    return 1, 2


@asset(group_name='exports')
def crm_export(loyal_customers, order_totals):
    """Push loyal customers to the CRM."""


reporting_job = define_asset_job('reporting_job', selection=AssetSelection.groups('reporting'))
everything = define_asset_job('everything', selection='*')


# An op-based job: ops composed in a graph, the graph used twice in a job.


@op
def copy_orders(duckdb: DuckDBResource) -> None:
    """Copy the orders model into the archive."""
    with duckdb.get_connection() as conn:
        conn.execute(
            'create or replace table archive.orders as '
            'select order_id, customer_id, order_date, amount from main.orders'
        )


@op(name='total_by_day')
def summarise(context, duckdb: DuckDBResource, orders) -> None:
    """Order value per day, from the archive."""
    with duckdb.get_connection() as conn:
        conn.execute(
            'create or replace table archive.daily_totals as '
            'select order_date, sum(amount) as total from archive.orders group by order_date'
        )


@op
def notify(totals) -> None:
    """Tell someone the totals are ready."""


@op(ins={'start': In(Nothing)})
def refresh_customers(dbt: DbtCliResource) -> None:
    """Rebuild the customers model."""
    dbt.cli(['run', '--select', 'customers']).wait()


@graph
def archive():
    return summarise(copy_orders())


@job(name='archive_orders')
def archive_job():
    totals = archive()
    notify(totals)
    notify.alias('notify_finance')(totals)
    refresh_customers(start=[archive.alias('archive_again')(), totals])


@schedule(job=archive_job, cron_schedule='0 2 * * *')
def nightly_archive(context):
    return {}


@asset_sensor(asset_key=AssetKey(['reporting', 'customer_ltv']), job=archive_job)
def ltv_refreshed(context, asset_event):
    """Archive orders whenever lifetime value is recomputed."""


@run_status_sensor(run_status=DagsterRunStatus.SUCCESS, monitored_jobs=[archive_job], request_job=everything)
def after_archive(context):
    """Rebuild everything after the archive succeeds."""


@run_failure_sensor(monitored_jobs=[archive_job], request_job=everything)
def archive_failed(context):
    """Never ordered: a failure sensor adds no dependency."""


defs = Definitions(
    assets=[jaffle_shop_dbt, customer_ltv, loyal_customers, daily_orders, crm_export],
    jobs=[reporting_job, everything, archive_job],
    schedules=[ScheduleDefinition(job=reporting_job, cron_schedule='0 6 * * *'), nightly_archive],
    sensors=[ltv_refreshed, after_archive, archive_failed],
    resources={
        'dbt': DbtCliResource(project_dir=str(Path(__file__).parent)),
        'duckdb': DuckDBResource(database=str(Path(__file__).parent / 'jaffle_shop.duckdb')),
    },
)
