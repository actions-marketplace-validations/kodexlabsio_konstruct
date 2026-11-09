"""Every way of declaring dependencies: >>, <<, lists, chain, cross_downstream, set_*."""

from datetime import datetime

from airflow.providers.standard.operators.empty import EmptyOperator as Empty
from airflow.sdk import DAG, chain, cross_downstream

with DAG('dependencies', schedule=None, start_date=datetime(2026, 1, 1)):
    a, b = Empty(task_id='a'), Empty(task_id='b')
    c = Empty(task_id='c')
    d = Empty(task_id='d')
    x = Empty(task_id='x')
    y = Empty(task_id='y')
    z = Empty(task_id='z')
    chain(a, [b, c], d)
    cross_downstream([a, b], [x, y])
    d << y
    c.set_upstream(x)
    z.set_downstream(d)
