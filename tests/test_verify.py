"""Perturbation-based verification: it must confirm true lineage and catch false lineage."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from konstruct import Project
from konstruct.graph import Graph, column_id
from konstruct.verify import Verification, annotate, claims, verify

REPORT_SQL = """
create table main.report as
with base as (
    select o.id, o.customer_id, o.amount, o.status, c.region, o.placed_at
    from raw.orders o join raw.customers c on c.id = o.customer_id
    where o.status <> 'void'
)
select
    id,
    amount * 2 as doubled,
    upper(region) as region,
    case when amount > 50 then 'big' else 'small' end as size,
    coalesce(amount, customer_id) as amount_or_customer,
    row_number() over (partition by customer_id order by placed_at) as order_number
from base
"""


def make_project(root: Path, sql: str = REPORT_SQL) -> Project:
    con = duckdb.connect(str(root / 'shop.duckdb'))
    con.execute('create schema raw')
    con.execute(
        "create table raw.customers as select i as id, ['north','south','east'][1 + i % 3] as region from range(1, 21) t(i)"
    )
    con.execute(
        'create table raw.orders as select i as id, 1 + i % 20 as customer_id, (i * 37) % 100 + 1 as amount, '
        "case when i % 9 = 0 then 'void' else 'ok' end as status, "
        "timestamp '2026-01-01' + to_hours(i * 5) as placed_at from range(1, 201) t(i)"
    )
    con.close()
    (root / 'report.sql').write_text(sql)
    project = Project(root)
    project.build()
    return project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return make_project(tmp_path)


def run(project: Project, *, seed: int = 1729) -> Verification:
    return verify(project.graph, project.root, project.root / 'shop.duckdb', seed=seed)


def test_true_lineage_is_confirmed(project: Project) -> None:
    result = run(project)
    [model] = result.models
    assert model.skipped is None
    assert model.rows == 178
    totals = result.totals()
    assert totals['unexplained'] == 0
    confirmed = set(model.confirmed)
    assert ('doubled', 'shop.raw.orders.amount') in confirmed
    assert ('region', 'shop.raw.customers.region') in confirmed
    assert ('order_number', 'shop.raw.orders.placed_at') in confirmed
    assert ('order_number', 'shop.raw.orders.customer_id') in confirmed
    references = {s for _, s in model.through_references}
    assert {'shop.raw.orders.status', 'shop.raw.orders.customer_id', 'shop.raw.customers.id'} <= references


def test_masked_dependencies_are_not_exercised(project: Project) -> None:
    [model] = run(project).models
    # amount is never null, so customer_id never reaches amount_or_customer on this data.
    assert ('amount_or_customer', 'shop.raw.orders.customer_id') in model.not_exercised


def test_a_missing_edge_is_unexplained(project: Project) -> None:
    graph = project.graph
    node = graph.node_for_relation('shop.main.report')
    assert node is not None
    edge = next(
        e
        for e in graph.out_edges(column_id(node.id, 'doubled'), 'derived_from')
        if e.target.endswith('#amount')
    )
    graph.remove_edge(edge)
    [model] = verify(graph, project.root, project.root / 'shop.duckdb').models
    assert ('doubled', 'shop.raw.orders.amount') in model.unexplained


def test_a_false_edge_is_not_exercised(project: Project) -> None:
    graph = project.graph
    node = graph.node_for_relation('shop.main.report')
    orders = graph.node_for_relation('shop.raw.orders')
    assert node is not None and orders is not None
    graph.add_edge(column_id(node.id, 'region'), column_id(orders.id, 'amount'), 'derived_from')
    [model] = verify(graph, project.root, project.root / 'shop.duckdb').models
    assert ('region', 'shop.raw.orders.amount') in model.not_exercised


def test_a_missing_filter_reference_is_unexplained(project: Project) -> None:
    graph = project.graph
    node = graph.node_for_relation('shop.main.report')
    assert node is not None
    for edge in list(graph.in_edges(node.id, 'feeds')):
        if edge.attrs.get('role') == 'reference' and edge.source.endswith('#status'):
            graph.remove_edge(edge)
    [model] = verify(graph, project.root, project.root / 'shop.duckdb').models
    assert ('(row count)', 'shop.raw.orders.status') in model.unexplained


def test_runs_are_reproducible_and_seeded(project: Project) -> None:
    first = run(project, seed=7).to_dict()
    second = run(project, seed=7).to_dict()
    for data in (first, second):
        data.pop('seconds')
        for model in data['models']:
            model.pop('seconds')
    assert first == second
    assert first['seed'] == 7


def test_nondeterministic_and_failing_models_are_skipped(tmp_path: Path) -> None:
    project = make_project(tmp_path, 'create table main.noisy as select id, random() as r from raw.orders')
    [model] = run(project).models
    assert model.skipped and 'not deterministic' in model.skipped


def test_annotate_marks_edges(project: Project) -> None:
    result = run(project)
    graph = project.graph
    annotate(graph, result)
    states = {e.attrs.get('verified') for e in graph.edges if e.kind == 'derived_from'}
    assert {'confirmed', 'not exercised'} <= states
    assert graph.meta['verification']['unexplained'] == 0


def test_project_verify_writes_its_results_into_the_outputs(project: Project) -> None:
    project.verify(warehouse=project.root / 'shop.duckdb')
    out = project.out_dir
    assert (out / 'VERIFY.md').is_file() and (out / 'verify.json').is_file()
    assert '"verified":"confirmed"' in (out / 'graph.json').read_text().replace(' ', '')
    page = (out / 'graph.html').read_text()
    assert '"verified":"confirmed"' in page
    assert '"verification":' in page


def test_claims_ignore_relations_without_names(project: Project) -> None:
    node = project.graph.node_for_relation('shop.main.report')
    assert node is not None
    value, references = claims(project.graph, node)
    assert ('doubled', 'shop.raw.orders.amount') in value
    assert 'shop.raw.orders.status' in references


def test_text_and_markdown(project: Project) -> None:
    result = run(project)
    text = str(result)
    assert 'claimed column edges confirmed by perturbation' in text
    assert 'report' in text
    markdown = result.markdown()
    assert markdown.startswith('# Konstruct lineage verification')
    assert '| `report` | 178 |' in markdown


# Differential testing: generated queries run on generated data must agree with static lineage.

COLUMNS = ['a', 'b', 'c', 'd']


@st.composite
def queries(draw: st.DrawFn) -> str:
    expressions = []
    for index in range(draw(st.integers(1, 4))):
        kind = draw(st.sampled_from(['column', 'arith', 'case', 'window', 'concat', 'coalesce']))
        x, y = draw(st.sampled_from(COLUMNS)), draw(st.sampled_from(COLUMNS))
        expressions.append(
            {
                'column': f'{x}',
                'arith': f'{x} * 3 + {y}',
                'case': f'case when {x} > 5 then {y} else 0 end',
                'window': f'sum({x}) over (partition by {y} order by k)',
                'concat': f"cast({x} as varchar) || '-' || cast({y} as varchar)",
                'coalesce': f'coalesce(nullif({x}, {x}), {y})',
            }[kind]
            + f' as o{index}'
        )
    where = draw(st.sampled_from(['', 'where a > 2', 'where d <> 3']))
    layered = draw(st.booleans())
    inner = f'select k, {", ".join(COLUMNS)} from raw.t {where}'
    if layered:
        return f'create table main.out as with x as ({inner}) select {", ".join(expressions)} from x'
    return f'create table main.out as select {", ".join(expressions)} from raw.t {where}'


@settings(
    max_examples=int(os.environ.get('KONSTRUCT_DIFFERENTIAL_EXAMPLES', '25')),
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(queries())
def test_static_lineage_agrees_with_execution(tmp_path_factory: pytest.TempPathFactory, sql: str) -> None:
    root = tmp_path_factory.mktemp('differential')
    con = duckdb.connect(str(root / 'w.duckdb'))
    con.execute('create schema raw')
    con.execute(
        'create table raw.t as select i as k, i % 7 as a, (i * 3) % 11 as b, i % 4 as c, (i * 5) % 9 as d '
        'from range(1, 61) t(i)'
    )
    con.close()
    (root / 'q.sql').write_text(sql)
    project = Project(root)
    project.build()
    graph: Graph = project.graph
    result = verify(graph, root, root / 'w.duckdb', seed=3)
    [model] = result.models
    assert model.skipped is None, model.skipped
    assert model.unexplained == [], sql


@pytest.mark.postgres
def test_verify_on_postgres_with_dbt(tmp_path: Path) -> None:
    """The jaffle_shop example built on Postgres with dbt-postgres, verified in a rolled-back
    transaction: every claimed edge confirmed, nothing unexplained, nothing left behind."""
    import shutil
    import uuid
    from urllib.parse import urlparse

    import psycopg

    from .conftest import EXAMPLE, run_dbt
    from .test_warehouse import require

    dsn = require('KONSTRUCT_TEST_PG_DSN')
    url = urlparse(dsn)
    schema = f'kp_{uuid.uuid4().hex[:8]}'
    root = tmp_path / 'shop'
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns('target', 'logs', 'konstruct-out', '*.duckdb', 'dags')
    )
    (root / 'profiles.yml').write_text(
        'jaffle_shop:\n  target: dev\n  outputs:\n    dev:\n      type: postgres\n'
        f'      host: {url.hostname}\n      port: {url.port or 5432}\n      user: {url.username}\n'
        f'      password: {url.password}\n      dbname: {url.path.lstrip("/")}\n      schema: {schema}\n      threads: 4\n'
    )
    try:
        run_dbt(root, 'build')
        run_dbt(root, 'docs', 'generate')
        project = Project(root)
        project.build(run_dbt=False)
        result = project.verify(warehouse=dsn)
        totals = result.totals()
        assert result.warehouse.startswith('postgres database')
        assert totals['models'] == 5 and totals['skipped'] == 0
        assert totals['confirmed'] == totals['claimed'] == 31
        assert totals['unexplained'] == 0
        with psycopg.connect(dsn) as con:
            leftovers = con.execute("select count(*) from pg_class where relname like 'kp_%'").fetchone()
        assert leftovers == (0,)
    finally:
        with psycopg.connect(dsn, autocommit=True) as con:
            con.execute(f'drop schema if exists {schema} cascade')
