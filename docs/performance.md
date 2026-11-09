# Performance

On generated dbt projects (`.kodex/scripts/benchmark.py`): models in layers, each joining two earlier models through CTEs, 20 columns each, with a manifest, a catalog and Airflow DAGs. Measured on an Apple silicon laptop with Python 3.12. A cold build parses every model and writes every output, `graph.html` included with every column's composed formula. `--update` reuses the analysis of anything unchanged, and after an edit it recomputes only the formulas downstream of the change.

| Models | Columns | Column edges | Cold build | `--update`, no change | `--update`, one model edited | impact | trace | explain | graph.html |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 2,200 | 2,540 | 1.08 s | 0.1 s | 0.12 s | 0.2 ms | 0.2 ms | 1.5 ms | 1.4 MB |
| 500 | 10,200 | 12,940 | 6.14 s | 0.79 s | 0.63 s | 1.0 ms | 0.8 ms | 2.4 ms | 6.7 MB |
| 1,000 | 20,200 | 25,940 | 12.66 s | 1.67 s | 1.46 s | 2.1 ms | 1.6 ms | 3.8 ms | 13.4 MB |
| 2,000 | 40,200 | 51,940 | 26.44 s | 3.95 s | 3.1 s | 4.0 ms | 3.3 ms | 5.0 ms | 27.1 MB |

Time grows linearly with the size of the project. Query times are medians over five runs on the deepest model. In the browser, the 2,000-model `graph.html` opens in under half a second, and selecting a column isolates its lineage in about 0.2 seconds. Run `uv run .kodex/scripts/benchmark.py 300 3000` to measure other sizes on your machine.
