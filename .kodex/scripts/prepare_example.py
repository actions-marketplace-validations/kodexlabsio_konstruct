# /// script
# requires-python = ">=3.11"
# dependencies = ["dbt-duckdb>=1.10"]
# ///
"""Build the jaffle_shop example's DuckDB warehouse, as its Airflow DAG would.

Runs `dbt build` and `dbt docs generate`, then the DAG's reporting SQL, so that
`kerplunk build examples/jaffle_shop` has a populated warehouse and fresh dbt artefacts.

    uv run .kodex/scripts/prepare_example.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "jaffle_shop"


def dbt_executable() -> str:
    beside = Path(sys.executable).parent / "dbt"
    found = str(beside) if beside.is_file() else shutil.which("dbt")
    if not found:
        sys.exit("dbt is not installed; run this script with `uv run`, which installs dbt-duckdb")
    return found


def main() -> None:
    dbt = dbt_executable()
    env = {**os.environ, "DBT_PROFILES_DIR": str(EXAMPLE), "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    for args in (["build"], ["docs", "generate"]):
        subprocess.run([dbt, *args, "--quiet"], cwd=EXAMPLE, env=env, check=True)

    import duckdb

    con = duckdb.connect(str(EXAMPLE / "jaffle_shop.duckdb"))
    try:
        con.execute((EXAMPLE / "dags" / "sql" / "customer_ltv.sql").read_text(encoding="utf-8"))
    finally:
        con.close()
    print(f"prepared {EXAMPLE.relative_to(Path.cwd()) if EXAMPLE.is_relative_to(Path.cwd()) else EXAMPLE}")


if __name__ == "__main__":
    main()
