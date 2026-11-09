"""DAGs assigned to variables, tasks attached with dag=, constants in commands and SQL."""

from datetime import datetime

from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.providers.standard.operators.bash import BashOperator

SCHEMA = 'analytics'
first = DAG('first', schedule='0 3 * * *', start_date=datetime(2026, 1, 1))
second = DAG(dag_id='second', schedule=None, start_date=datetime(2026, 1, 1))

load = SQLExecuteQueryOperator(
    task_id='load',
    conn_id='warehouse',
    sql=f'insert into {SCHEMA}.events select * from raw.events',
    dag=first,
)
other = BashOperator(task_id='other', bash_command='echo hi', dag=second)
report = BashOperator(task_id='report', bash_command='dbt build -s +events --exclude tag:slow', dag=first)
load.set_downstream(report)
