"""TaskFlow tasks, expand, override and @task.bash in a @dag function."""

from datetime import datetime

from airflow.sdk import dag, task


@dag(schedule=None, start_date=datetime(2026, 1, 1), catchup=False, tags=['etl'])
def taskflow_pipeline():
    @task
    def extract():
        return [1, 2]

    @task(task_id='transform_rows')
    def transform(x):
        return x

    @task.bash
    def run_dbt():
        return 'dbt run --select marts'

    rows = extract()
    done = transform.expand(x=rows)
    again = transform.override(task_id='transform_again')(rows)
    rows >> [done, again] >> run_dbt()


taskflow_pipeline()
