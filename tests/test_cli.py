from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kerplunk import __version__
from kerplunk.cli import find_out_dir, main


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.dbt
def test_build_and_query_commands(
    jaffle: Path, dbt_on_path: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "build", str(jaffle))
    assert code == 0
    assert "kerplunk: 44 columns, 1 dag, 1 exposure, 5 models, 3 seeds, 1 table, 6 tasks, 20 tests" in out
    assert "graph.json" in out and "REPORT.md" in out and "graph.html" in out

    code, out, _ = run(capsys, "build", str(jaffle), "--update")
    assert code == 0
    assert "reused 7 of 7 cached analyses" in out

    path = str(jaffle)
    code, out, _ = run(capsys, "impact", "stg_payments.amount", "--path", path)
    assert code == 0 and out.startswith("Downstream of stg_payments.amount")
    code, out, _ = run(capsys, "trace", "orders.amount", "--path", path, "--json")
    assert code == 0 and json.loads(out)["direction"] == "upstream"
    code, out, _ = run(capsys, "path", "raw_orders", "orders", "--path", path)
    assert code == 0 and "-feeds-> orders (model)" in out
    code, out, _ = run(capsys, "search", "lifetime", "value", "--path", path, "--limit", "1")
    assert code == 0 and out.startswith("customers.customer_lifetime_value")
    code, out, _ = run(
        capsys, "describe", "customers", "--graph", str(jaffle / "kerplunk-out" / "graph.json")
    )
    assert code == 0 and out.startswith("customers (model)")
    code, out, _ = run(capsys, "query", "which", "models", "use", "payments", "--path", path)
    assert code == 0 and "stg_payments" in out
    code, _, err = run(capsys, "impact", "nope", "--path", path)
    assert code == 1 and "nothing in the graph matches 'nope'" in err


def test_build_options_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "x.sql").write_text("create table t2 as select a from t1")
    code, _, _ = run(
        capsys,
        "build",
        str(tmp_path),
        "--no-dbt",
        "--no-warehouse",
        "--dialect",
        "postgres",
        "--out",
        str(tmp_path / "o"),
    )
    assert code == 0
    assert (tmp_path / "o" / "graph.json").is_file()
    graph = json.loads((tmp_path / "o" / "graph.json").read_text())
    assert graph["meta"]["dialect"] == "postgres"
    code, _, err = run(capsys, "build", str(tmp_path / "missing"))
    assert code == 1 and "is not a directory" in err
    (tmp_path / "kerplunk.toml").write_text("[[warehouse]]\ntype = 'oracle'\n")
    code, _, err = run(capsys, "build", str(tmp_path))
    assert code == 1 and "unknown warehouse type 'oracle'" in err
    (tmp_path / "kerplunk.toml").write_text("not = [toml")
    code, _, err = run(capsys, "build", str(tmp_path))
    assert code == 1 and "cannot read" in err


def test_query_without_a_graph(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "impact", "x", "--path", str(tmp_path))
    assert code == 1 and "run `kerplunk build` first" in err


def test_find_out_dir_looks_upwards(tmp_path: Path) -> None:
    (tmp_path / "kerplunk-out").mkdir()
    (tmp_path / "kerplunk-out" / "graph.json").write_text("{}")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_out_dir(nested) == tmp_path / "kerplunk-out"
    assert find_out_dir(tmp_path / "kerplunk-out") == tmp_path / "kerplunk-out"


def test_version_and_help() -> None:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "kerplunk.cli", "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert result.stdout.strip() == f"kerplunk {__version__}"
    result = subprocess.run(
        [sys.executable, "-m", "kerplunk.cli", "--help"], capture_output=True, text=True, env=env, check=True
    )
    for command in ("build", "query", "impact", "trace", "path", "search", "describe", "install", "mcp"):
        assert command in result.stdout
