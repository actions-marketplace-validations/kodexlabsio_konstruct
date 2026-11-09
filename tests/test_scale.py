"""A generated project far larger than jaffle_shop: build time, --update time and correctness."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from konstruct import Project

pytestmark = pytest.mark.scale

MODELS = 300
COLUMNS = 15


def make_project(root: Path, models: int = MODELS, columns_per_model: int = COLUMNS) -> None:
    """A dbt project of ``models`` models in layers (each joins two earlier models through CTEs),
    its manifest and catalog, and Airflow DAGs that run it in groups of 50. Also used by
    .kodex/scripts/benchmark.py."""
    (root / 'models').mkdir(parents=True)
    (root / 'dbt_project.yml').write_text('name: big\nprofile: big\n')
    nodes: dict[str, Any] = {}
    catalog: dict[str, Any] = {}

    def relation(name: str) -> str:
        return f'"wh"."analytics"."{name}"'

    for index in range(models):
        name = f'm{index:03}'
        unique_id = f'model.big.{name}'
        columns = [f'c{j:02}' for j in range(columns_per_model)]
        if index < 10:
            sql = f'select {", ".join(columns)} from {relation("raw_" + name)}'
            depends: list[str] = []
        else:
            left, right = f'm{index // 2:03}', f'm{index // 3:03}'
            depends = [f'model.big.{left}', f'model.big.{right}']
            picks = ', '.join(
                f'a.{c}'
                if j % 3 == 0
                else (f'b.{c} * 2 as {c}' if j % 3 == 1 else f'coalesce(a.{c}, b.{c}) as {c}')
                for j, c in enumerate(columns)
            )
            sql = (
                f'with a as (select * from {relation(left)}), b as (select * from {relation(right)}), '
                f'joined as (select {picks} from a join b on a.c00 = b.c00 where b.c01 > 0) '
                'select * from joined'
            )
        nodes[unique_id] = {
            'unique_id': unique_id,
            'resource_type': 'model',
            'name': name,
            'alias': name,
            'package_name': 'big',
            'database': 'wh',
            'schema': 'analytics',
            'relation_name': relation(name),
            'original_file_path': f'models/{name}.sql',
            'fqn': ['big', f'group{index // 50}', name],
            'raw_code': sql,
            'compiled_code': sql,
            'language': 'sql',
            'depends_on': {'nodes': depends, 'macros': []},
            'config': {'materialized': 'table'},
            'tags': [f'group{index // 50}'],
            'columns': {},
            'checksum': {'checksum': str(index)},
        }
        catalog[unique_id] = {
            'unique_id': unique_id,
            'metadata': {},
            'columns': {c: {'name': c, 'type': 'INTEGER', 'index': j + 1} for j, c in enumerate(columns)},
        }
        (root / 'models' / f'{name}.sql').write_text(sql)
    target = root / 'target'
    target.mkdir()
    (target / 'manifest.json').write_text(
        json.dumps({'metadata': {'adapter_type': 'duckdb'}, 'nodes': nodes, 'sources': {}, 'exposures': {}})
    )
    (target / 'catalog.json').write_text(json.dumps({'nodes': catalog, 'sources': {}}))
    dags = root / 'dags'
    dags.mkdir()
    for group in range(max(1, models // 50)):
        (dags / f'group{group}.py').write_text(
            'from airflow.sdk import DAG\n'
            'from airflow.providers.standard.operators.bash import BashOperator\n'
            f"with DAG('group{group}'):\n"
            f"    run = BashOperator(task_id='run', bash_command='dbt run -s tag:group{group}')\n"
            f"    test = BashOperator(task_id='test', bash_command='dbt test -s tag:group{group}')\n"
            '    run >> test\n'
        )


def test_large_project(tmp_path: Path) -> None:
    make_project(tmp_path)
    project = Project(tmp_path)
    started = time.perf_counter()
    result = project.build(run_dbt=False, warehouses=False)
    full = time.perf_counter() - started
    graph = result.graph
    assert graph.warnings == []
    assert sum(1 for n in graph.nodes.values() if n.kind == 'model') == MODELS
    lineage = sum(1 for e in graph.edges if e.kind == 'derived_from')
    assert lineage >= (MODELS - 10) * COLUMNS
    assert full < 60, f'full build took {full:.1f}s'

    started = time.perf_counter()
    again = project.build(update=True, run_dbt=False, warehouses=False)
    incremental = time.perf_counter() - started
    assert again.cache_misses == 0
    assert incremental < full / 2, f'--update took {incremental:.1f}s against {full:.1f}s'

    walk = project.impact('m010.c01')
    assert walk.columns
    assert {t.label for t in walk.tasks} >= {'group0.run'}
    deepest = project.trace(f'm{MODELS - 1:03}.c00')
    assert any(c.label.startswith('raw_') for c in deepest.columns)
    print(f'\n{MODELS} models: full build {full:.1f}s, --update {incremental:.1f}s, {lineage} lineage edges')
