"""Measure Konstruct on generated dbt projects of increasing size.

Each project is the one tests/test_scale.py builds: models in layers, each joining two earlier
models through CTEs, with a manifest, a catalog and Airflow DAGs. For each size it records:

* a cold build (no cache), which parses every model and writes graph.json, REPORT.md and
  graph.html (including every column's composed formula);
* --update with nothing changed, and --update after editing one model in the middle;
* the median time of an impact, a trace and an explain on the deepest model;
* the size of graph.json and graph.html.

Usage:
    uv run .kodex/scripts/benchmark.py                  # 100, 500, 1000 and 2000 models
    uv run .kodex/scripts/benchmark.py 300 3000 --json results.json
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from konstruct import Project, __version__  # noqa: E402
from tests.test_scale import make_project  # noqa: E402

COLUMNS = 20


def timed(func: Any, *args: Any, **kwargs: Any) -> tuple[float, Any]:
    started = time.perf_counter()
    result = func(*args, **kwargs)
    return time.perf_counter() - started, result


def median_time(func: Any, *args: Any, repeat: int = 5) -> float:
    times = []
    for _ in range(repeat):
        elapsed, _ = timed(func, *args)
        times.append(elapsed)
    return statistics.median(times)


def measure(models: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix='konstruct-bench-') as directory:
        root = Path(directory)
        make_project(root, models=models, columns_per_model=COLUMNS)
        project = Project(root)
        cold, result = timed(project.build, run_dbt=False, warehouses=False)
        graph = result.graph
        unchanged, _ = timed(project.build, update=True, run_dbt=False, warehouses=False)
        middle = root / 'models' / f'm{models // 2:03}.sql'
        middle.write_text(middle.read_text().replace('where b.c01 > 0', 'where b.c01 > 1'))
        manifest = root / 'target' / 'manifest.json'
        data = json.loads(manifest.read_text())
        node = data['nodes'][f'model.big.m{models // 2:03}']
        node['compiled_code'] = node['raw_code'] = middle.read_text()
        node['checksum']['checksum'] = 'edited'
        manifest.write_text(json.dumps(data))
        one_edit, _ = timed(project.build, update=True, run_dbt=False, warehouses=False)
        deepest = f'm{models - 1:03}'
        return {
            'models': models,
            'columns': sum(1 for n in graph.nodes.values() if n.kind == 'column'),
            'column_edges': sum(1 for e in graph.edges if e.kind == 'derived_from'),
            'warnings': len(graph.warnings),
            'cold_build_s': round(cold, 2),
            'update_unchanged_s': round(unchanged, 2),
            'update_one_model_s': round(one_edit, 2),
            'impact_ms': round(1000 * median_time(project.impact, 'm010.c01'), 1),
            'trace_ms': round(1000 * median_time(project.trace, f'{deepest}.c00'), 1),
            'explain_ms': round(1000 * median_time(project.explain, f'{deepest}.c01'), 1),
            'graph_json_mb': round((project.out_dir / 'graph.json').stat().st_size / 1e6, 1),
            'graph_html_mb': round((project.out_dir / 'graph.html').stat().st_size / 1e6, 1),
        }


def markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        '| Models | Columns | Column edges | Cold build | `--update`, no change | `--update`, one model edited '
        '| impact | trace | explain | graph.html |',
        '|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for r in rows:
        lines.append(
            f'| {r["models"]:,} | {r["columns"]:,} | {r["column_edges"]:,} | {r["cold_build_s"]} s '
            f'| {r["update_unchanged_s"]} s | {r["update_one_model_s"]} s | {r["impact_ms"]} ms '
            f'| {r["trace_ms"]} ms | {r["explain_ms"]} ms | {r["graph_html_mb"]} MB |'
        )
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('sizes', nargs='*', type=int, default=[100, 500, 1000, 2000])
    parser.add_argument('--json', type=Path, help='also write the results as JSON')
    args = parser.parse_args()
    rows = []
    for size in args.sizes:
        row = measure(size)
        rows.append(row)
        print(f'{size} models: {row}', file=sys.stderr)
    machine = (
        f'{platform.machine()}, {platform.system()} {platform.release()}, Python {platform.python_version()}'
    )
    print(f'konstruct {__version__} on {machine}, {COLUMNS} columns per model\n')
    print(markdown(rows))
    if args.json:
        args.json.write_text(
            json.dumps({'konstruct': __version__, 'machine': machine, 'results': rows}, indent=1)
        )
    return 0


if __name__ == '__main__':
    sys.exit(main())
