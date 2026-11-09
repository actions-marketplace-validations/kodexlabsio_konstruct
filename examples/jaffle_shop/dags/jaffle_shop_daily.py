"""Daily jaffle_shop run: load seeds, build the dbt models, test them, then refresh reporting."""

from datetime import datetime

from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Asset, task

DBT = 'cd /opt/airflow/jaffle_shop && dbt'

with DAG(
    dag_id='jaffle_shop_daily',
    schedule='@daily',
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=['jaffle_shop'],
    doc_md=__doc__,
) as dag:
    seed = BashOperator(task_id='dbt_seed', bash_command=f'{DBT} seed')
    staging = BashOperator(task_id='dbt_run_staging', bash_command=f'{DBT} run --select staging')
    marts = BashOperator(task_id='dbt_run_marts', bash_command=f'{DBT} run --select customers orders')
    test = BashOperator(task_id='dbt_test', bash_command=f'{DBT} test')

    customer_ltv = SQLExecuteQueryOperator(
        task_id='build_customer_ltv',
        conn_id='jaffle_shop_duckdb',
        sql='sql/customer_ltv.sql',
    )

    @task(outlets=[Asset('duckdb://jaffle_shop/reporting/customer_ltv')])
    def notify_crm() -> None:
        """Tell the CRM that lifetime values have been refreshed."""

    seed >> staging >> marts >> test >> customer_ltv >> notify_crm()
