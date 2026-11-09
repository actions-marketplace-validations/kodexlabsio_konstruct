"""Nested TaskGroups and dependencies on a whole group."""

from datetime import datetime

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, TaskGroup

with DAG('grouped', schedule=None, start_date=datetime(2026, 1, 1)) as dag:
    start = EmptyOperator(task_id='start')
    with TaskGroup('load') as load:
        a = EmptyOperator(task_id='a')
        with TaskGroup('inner'):
            b = EmptyOperator(task_id='b')
        a >> b
    end = EmptyOperator(task_id='end')
    start >> load >> end
