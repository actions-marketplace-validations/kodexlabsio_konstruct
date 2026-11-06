<p align="center">
<a target="_blank" href="https://kodexlabs.io/"><img width="150" height="150" src="./docs/logo-icon.svg"></a>
</p>
<p align="center">
<a href="https://github.com/kodexlabsio/kerplunk/actions/workflows/build-and-test.yml"><img src="https://github.com/kodexlabsio/kerplunk/actions/workflows/build-and-test.yml/badge.svg" alt="Build and Test"></a>&nbsp;<a href="https://github.com/kodexlabsio/kerplunk/actions/workflows/release.yml"><img src="https://github.com/kodexlabsio/kerplunk/actions/workflows/release.yml/badge.svg" alt="Create Release"></a>&nbsp;<a href="./LICENSE"><img src="https://img.shields.io/badge/licence-MIT-blue" alt="MIT licence"></a>&nbsp;<img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue" alt="Python 3.11 to 3.13">
<br />
<a href="https://kodexlabs.io/" target="_blank">Kodex Labs</a> Ltd.
</p>

kerplunk turns a data stack into a graph that coding agents can query: dbt models, sources, seeds, tests and exposures; warehouse and Iceberg tables with their columns; Airflow DAGs and tasks; and column-level lineage parsed from the SQL that connects them. One command builds it locally. Agents then ask what a change will break, where a value comes from, or how two tables connect, from the command line, a `/kerplunk` skill in Claude Code and Cursor, or an MCP server.

Two things set it apart from other lineage tools:

- **The lineage is tested against the data.** `kerplunk verify` perturbs each input column of each model (shifting, nulling and shuffling it with a fixed seed), reruns the SQL on a scratch copy and records which outputs actually change. On dbt Labs' jaffle-shop, every one of the 87 claimed column edges is confirmed, and every observed influence was predicted. See [How Much to Trust the Lineage](#how-much-to-trust-the-lineage).
- **It reviews changes before they merge.** `kerplunk diff` compares a branch with main column by column, ignoring formatting and renamed CTEs, and names the downstream models that a removed column will break. It can post the result as a pull request comment and fail the check.

![kerplunk impact analysis on the jaffle_shop example](docs/demo-cli.gif)

## Quick Start

```bash
uv tool install kerplunk        # until it is on PyPI: uv tool install git+https://github.com/kodexlabsio/kerplunk
cd your-data-repo
kerplunk status                 # what kerplunk finds here, before building anything
kerplunk build                  # writes kerplunk-out/graph.json, REPORT.md and graph.html
kerplunk impact orders.amount   # everything downstream of a column
kerplunk open                   # the interactive graph in a browser
kerplunk install                # adds the /kerplunk skill to Claude Code and Cursor
```

To try it on the bundled example (jaffle_shop on DuckDB, with an Airflow DAG), from a clone of this repository:

```bash
uv sync
uv run .kodex/scripts/prepare_example.py      # builds the example's DuckDB warehouse with dbt
uv run kerplunk build examples/jaffle_shop    # about a second
uv run kerplunk impact stg_payments.amount -C examples/jaffle_shop
```

`kerplunk build` needs no configuration: it finds dbt projects, Airflow DAG files, SQL files, DuckDB files and the warehouse each dbt profile points at, and prints what it found and what it did with each. If a dbt project's `target/manifest.json` is missing or older than the project files and `dbt` is on `PATH`, it runs `dbt docs generate` first. `kerplunk init` writes a commented `kerplunk.toml` recording what discovery found, for when you want to pin or change it.

```text
$ kerplunk build
kerplunk  /src/jaffle_shop

  dbt        jaffle_shop (.)  5 models, 3 seeds, 20 tests, 1 exposure  (artefacts up to date)
  duckdb     duckdb jaffle_shop.duckdb  9 relations  (dbt profile jaffle_shop)
  airflow    dags/jaffle_shop_daily.py  1 DAG, 6 tasks

  graph      1 dag, 1 exposure, 5 models, 3 seeds, 1 table, 6 tasks, 20 tests; 44 columns
  lineage    32 of 32 columns of models and built tables (100%)
  warnings   none
  wrote      graph.json, REPORT.md, graph.html to kerplunk-out in 0.7s
```

## Supported Inputs

| Input | What kerplunk reads | Status |
|-------|---------------------|--------|
| **dbt** | `target/manifest.json` and `catalog.json`: models, seeds, snapshots, sources, tests (attached to columns), exposures, descriptions, column types. Compiled SQL for lineage; `ref()` and `source()` are resolved from the manifest when only raw SQL is available | v0 |
| **SQL** | Column-level lineage with [sqlglot](https://github.com/tobymao/sqlglot) for dbt models, warehouse views, Airflow SQL and standalone `.sql` files (`CREATE ... AS`, `INSERT ... SELECT`, `MERGE`, `UPDATE`, `DELETE`, `COPY`). Dialect from the dbt adapter or configuration | v0 |
| **Airflow** | DAG files parsed statically (never imported): `with DAG`, assigned DAGs, `@dag`, TaskFlow, TaskGroups, `>>`, `<<`, `chain`, `cross_downstream`. Per task: dbt commands (BashOperator, `@task.bash`, Cosmos, dbt operators) resolved to models with dbt's selection syntax; SQL inline or from files; target and source tables; inlet and outlet assets | v0 |
| **DuckDB** | Tables, views, columns, types, comments and view definitions, read only | v0 |
| **Postgres** | Tables, views, materialised views, partitioned tables, columns, types, comments and view definitions, read only | v0 |
| **Iceberg** | Any PyIceberg catalog, REST included: namespaces, tables, columns (nested struct fields as `parent.child`), field ids, identifier fields, partitioning, current snapshot. Metadata only; no data files are read | v0 |
| **Dagster** | Assets and jobs | v1 |
| **Snowflake, BigQuery, Databricks** | Warehouse metadata, behind optional extras. Their SQL dialects already work for lineage | v1 |

## Commands

| Command | Purpose |
|---------|---------|
| `kerplunk build [path] [-u]` | Build `kerplunk-out/`. `-u/--update` reuses cached analyses of anything unchanged (keyed by file hashes, compiled SQL, manifest checksums and the upstream columns each model sees). `--no-dbt`, `--no-warehouse`, `--dialect`, `--out`, `--json` (a machine-readable summary), `--strict` (exit 1 on warnings) |
| `kerplunk status` | What kerplunk finds (configuration, dbt projects and whether their artefacts are current, warehouses, DAG and SQL files), whether the graph is out of date and which inputs changed, and whether the skill is installed |
| `kerplunk init` | Write a commented `kerplunk.toml` from what discovery found (`--stdout` to print it) |
| `kerplunk impact <table.column>` | Everything downstream: derived columns, relations, models that join, filter or group on it, tests, exposures and the DAG tasks that run them |
| `kerplunk trace <table.column>` | Everything upstream, each step marked `direct` (passed through or renamed) or `transformed` |
| `kerplunk path <a> <b>` | Shortest connection between two tables or columns |
| `kerplunk search <words>` | Find nodes by name or description, optionally `--kind model` |
| `kerplunk describe <name>` | Columns, types, descriptions, tests and neighbours of one node; for a task, what it runs and its neighbours in the DAG |
| `kerplunk list [kind ...]` | Every node of the given kinds (`models`, `tables`, `sources`, `tasks`, ...; default all relations) |
| `kerplunk open` | Open `graph.html` in a browser |
| `kerplunk diff [--base REF]` | What the working tree (or `--head REF`) changes compared with the base (default: the merge base with main): columns added, removed or changed with their expressions before and after, changed join and filter conditions, breaking changes, DAG changes, dbt tests, sources and exposures added or removed in YAML, the downstream impact of each, and a column-aware `dbt build --select` that rebuilds only what the change affects. `--format markdown` for a pull request comment, `--fail-on-breaking` for CI |
| `kerplunk verify` | Test the column lineage against the data in a local DuckDB warehouse by perturbation; writes `VERIFY.md` and `verify.json` and marks confirmed edges in the graph. `--seed`, `--rows`, `--model`, `--strict` |
| `kerplunk query "<question>"` | Free-text question: ranks nodes by its words and returns them with their connections, plus impact or lineage when the question asks for it. There is no language model in kerplunk; the agent reads the result |
| `kerplunk install` | Register the `/kerplunk` skill (see Agent Integration) |
| `kerplunk mcp` | Run the MCP server on stdio |

Tables and columns can be named as `orders`, `main.orders`, `orders.amount`, `jaffle_shop.main.orders.amount`, `ecom.raw_orders` for a dbt source, `dag_id.task_id` for a task, or by dbt unique id. When nothing matches, kerplunk suggests close names; when a name is ambiguous, it lists the candidates.

Every command takes `-C DIR` (before or after the command name) to work on another directory; query commands also find `kerplunk-out` in any parent directory. Query commands build the graph if there is none, warn when inputs changed since the last build, and take `--refresh` to update it first, `--json` for structured output and `--graph FILE` to read a specific graph. Output is coloured on a terminal unless `NO_COLOR` is set or `--no-color` is given. Exit status is 0 on success, 1 on an error, 2 on a usage error and 3 when `path` finds no connection.

## Python API

```python
import kerplunk

project = kerplunk.Project("path/to/repo")
project.build(update=True)

walk = project.impact("stg_payments.amount")
print(walk.summary())  # 7 columns, 3 relations, 5 tests, 1 exposure and 2 tasks downstream.
for column in walk.columns:
    print(column.label, column.transform, column.path)

print(project.trace("customer_ltv.lifetime_value"))  # the same text the CLI prints
project.describe("orders").columns  # typed results
project.path_between("raw_payments", "customer_dashboard").found
project.search("lifetime value", kinds=["column"]).results
project.list(["model"]).items
project.changed_inputs()  # files changed since the last build
project.diff(base="origin/main").breaking  # removed columns still read downstream
project.verify(seed=7).totals()  # {"claimed": 36, "confirmed": 36, "unexplained": 0, ...}
```

Results are dataclasses (`Walk`, `PathResult`, `SearchResult`, `Description`, `Listing`, `QueryResult` made of `Item`s, plus `Diff` and `Verification`). `str(result)` gives the CLI's text and `result.to_dict()` the JSON the CLI and MCP server return. A name that matches nothing, or several things, raises `kerplunk.QueryError` with suggestions. `project.graph` is the underlying `kerplunk.Graph` for anything the API does not cover.

## Outputs

| File | Contents |
|------|----------|
| `graph.json` | Nodes (`source`, `table`, `column`, `model`, `seed`, `snapshot`, `dag`, `task`, `test`, `exposure`) and edges (below), with descriptions, types, file paths and the inputs each came from |
| `REPORT.md` | Summary and coverage, the most used columns, orphans, untested models, columns with no lineage, columns documented in dbt but missing from the warehouse, and warnings |
| `graph.html` | A single offline file: relations and tasks in dependency order, search, a details panel and column lineage in both directions |

| Edge | Direction | Meaning |
|------|-----------|---------|
| `contains` | relation to column | The column belongs to the relation |
| `feeds` | upstream to downstream | Relation to relation, task to the table it writes, table to the task that reads it. With `role: reference`, a column the target uses in a JOIN, WHERE, GROUP BY, HAVING or QUALIFY |
| `derived_from` | column to the column it comes from | Column-level lineage, with `transform: direct` or `transformed` |
| `runs` | DAG to task, task to dbt node | What a DAG or task executes |
| `precedes` | task to task | Airflow ordering |
| `tests` | test to relation or column | What a dbt test checks |

![graph.html for the jaffle_shop example](docs/graph-html.png)

## Agent Integration

`kerplunk install` writes `~/.claude/skills/kerplunk/SKILL.md`. Claude Code and Cursor both read that location, so `/kerplunk what breaks if I change stg_payments.amount?` works in either. The skill tells the agent to keep the graph current with `--refresh`, to run `impact` before changing a model or column, and how to read the output. It pre-approves `kerplunk` shell commands for the turn it runs in. `kerplunk status` shows where it is installed.

| Option | Effect |
|--------|--------|
| `--agent claude\|cursor\|both` | `cursor` writes `~/.cursor/skills` instead. Default `both` |
| `--target <dir>` | Install into a repository (`.claude/skills`) rather than the home directory, so the team shares it |
| `--mcp` | With `--target`, also register `kerplunk mcp` in `.mcp.json` (Claude Code) and `.cursor/mcp.json` (Cursor), keeping any other servers |
| `--force` | Replace a skill that was edited by hand; otherwise a reinstall only replaces kerplunk's own earlier versions |

The MCP server exposes `impact`, `trace`, `search`, `describe`, `path` and `query`, returns the same structured results as `--json`, and rereads `graph.json` when a build changes it.

## Configuration

Configuration is optional. Put it in `kerplunk.toml` at the project root, or under `[tool.kerplunk]` in `pyproject.toml`. `${VAR}` and `${VAR:-default}` read the environment, so credentials stay out of the file.

```toml
dialect = "postgres"              # default: the dbt adapter, then the first warehouse
default_catalog = "analytics"     # for unqualified tables in Airflow and SQL files
default_schema = "public"
exclude = ["scratch/**"]
out_dir = "kerplunk-out"

[dbt]
projects = ["transform"]          # default: every dbt_project.yml found
run = true                        # run `dbt docs generate` when artefacts are missing or stale

[airflow]
dags = ["orchestration/dags"]     # default: Python files that look like DAGs

[sql]
paths = ["sql/**/*.sql"]          # default: .sql files outside dbt folders and not used by DAGs

[[warehouse]]                     # setting any warehouse turns discovery off
type = "postgres"
dsn = "${WAREHOUSE_DSN}"
schemas = ["raw", "analytics"]

[[warehouse]]
type = "duckdb"
path = "data/local.duckdb"

[[warehouse]]
type = "iceberg"
name = "lake"                     # the catalogue name SQL uses, e.g. lake.sales.orders
uri = "http://localhost:8181"
namespaces = ["sales"]
[warehouse.properties]            # passed to pyiceberg.catalog.load_catalog
credential = "${ICEBERG_CREDENTIAL}"
```

## Reviewing Changes

`kerplunk diff` answers "what does this branch do to the data?" For each dbt model, SQL file and DAG file that changed, it analyses the base and current versions and compares each output column's canonical expression: the expression with every CTE, subquery and alias inlined down to physical columns. Reformatting, renaming a CTE or reordering columns is therefore not a change, and a real change is shown in full. Join, filter and grouping conditions are compared the same way. A removed column is breaking when a downstream model's current SQL still reads it, so a change that also updates its consumers is not flagged.

```text
$ kerplunk diff
1 breaking change, 1 column changed, 1 column removed, 1 column added, row logic changed in 1 relation, 1 DAG changed, 1 exposure affected.

models/orders.sql  (model orders, modified)
  rows     + WHERE: jaffle_shop.main.stg_orders.status <> 'returned'
           affects 10 tests, 1 exposure and 1 task downstream (exposures: customer_dashboard)

models/staging/stg_payments.sql  (model stg_payments, modified)
  added    method
  changed  amount
             was  jaffle_shop.main.raw_payments.amount / 100
             now  jaffle_shop.main.raw_payments.amount / 100.0 * 1.2
           affects 7 columns, 3 relations, 5 tests, 1 exposure and 2 tasks downstream (exposures: customer_dashboard)
  removed  payment_method
           BREAKS orders, which still reads it
           affects 4 columns, 1 relation, 5 tests, 1 exposure and 1 task downstream (exposures: customer_dashboard)

dags/jaffle_shop_daily.py  (DAG jaffle_shop_daily, modified)
  runs     task `dbt_run_marts`: no longer runs orders
```

`diff` also works out what dbt has to rebuild: the changed models, the models whose columns, joins or filters are downstream of a changed column, and the tests on them. It prints that as a `dbt build --select` command (or just the selection, with `--format selector`) and compares it with what `state:modified+` would build: on the example above it builds 3 of the 4 models, because the reformatted `stg_customers` needs nothing. A test builds the project with dbt before and after a change and checks that every model whose data changed is in the selection. It also reads dbt YAML, reporting tests, sources and exposures added or removed and descriptions changed.

dbt models are compared by rendering their Jinja without dbt (see How Lineage Works), so `diff` needs only the manifest from `dbt parse`, not a warehouse. [`docs/kerplunk-pull-request.yml`](docs/kerplunk-pull-request.yml) is a ready-made GitHub Actions workflow that posts the markdown version as a pull request comment and fails the check on breaking changes.

## How Much to Trust the Lineage

Static column lineage is a claim about SQL that is rarely checked. `kerplunk verify` checks it against the data, using perturbation (the idea behind perturbation-based provenance): for every dbt model and SQL-built table in a local DuckDB file or a Postgres database it copies the model's inputs into memory, perturbs one input column at a time, reruns the model's SQL and records which output columns change.

- **Perturbations:** each input column, and each field of a struct column, is shifted (`x * a + b`, a suffix on strings, days added to dates), set to NULL, and shuffled between rows with a seeded permutation. The seed is recorded, so a run can be repeated exactly. The warehouse is read but never written.
- **Detecting change:** an output column changes if its multiset of values changes; if values only moved between rows (a `ROW_NUMBER()` ordered by the input still yields 1..n), it is compared paired with the columns claimed not to depend on that input; and when a column identifies rows, rows are matched across runs, so changes are seen even when a perturbation also adds or removes rows.
- **Classification:** a claimed edge is *confirmed* when its output changed, or *not exercised* when no perturbation moved it on this data (genuine but masked, as with the second argument of a `COALESCE` whose first is never null). An observed influence the graph did not claim is either *via joins and filters* (the graph recorded it as a reference) or *unexplained*, a gap in the static analysis.

Results (seed 1729):

| Project | Models | Claimed column edges | Confirmed | Influences via joins and filters | Unexplained |
|---------|--------|----------------------|-----------|----------------------------------|-------------|
| `examples/jaffle_shop` | 6 | 36 | 36 (100%) | 33 | 0 |
| dbt Labs' jaffle-shop (pinned commit) | 13 | 87 | 87 (100%) | 61 | 0 |
| `examples/jaffle_shop` on Postgres (built with dbt-postgres) | 5 | 31 | 31 (100%) | not counted | 0 |
| 300 generated queries on generated data (differential test) | 300 | not counted | not counted | not counted | 0 |

The tests also make sure the verifier catches wrong lineage, not just confirms right lineage: a deleted edge is reported as unexplained, a fabricated edge as not exercised, and a deleted filter reference as an unexplained change in row count. Two further results:

- **Rendering dbt without dbt:** on jaffle-shop, lineage from kerplunk's own Jinja rendering (used when there is no compiled SQL, and by `diff`) is identical to lineage from dbt's compiled SQL: 113 of 113 column edges and 9 of 9 join and filter references.
- **Airflow parsing:** the static parser reports exactly the same DAGs, tasks and dependencies as Airflow's own DagBag on every fixture DAG, including TaskGroups, TaskFlow and every dependency syntax.

Each result is a test in the suite (`test_verify`, `test_realworld`, `test_airflow_parity`), so CI checks them on every change.

## How Lineage Works

- kerplunk qualifies each statement against the columns it knows (from the dbt catalog, the warehouse, or the lineage of upstream models, processed in dependency order), so `SELECT *` expands. sqlglot then follows every output column through CTEs, subqueries, aliases, unions and window functions to physical columns.
- A path made only of column references and renames is `direct`; anything else (arithmetic, functions, aggregates, `CASE`, window functions) is `transformed`.
- Columns that change which rows a model returns, without being selected (join keys, filters, GROUP BY, HAVING, QUALIFY), are recorded as `feeds` edges with `role: reference`, traced through CTEs. Impact reports the relations that use them but does not then mark every downstream column, which would bury the answer.
- Struct fields are followed: `o.customer.address.city`, `customer['country']` and fields of structs passed through CTEs trace to the nested column, in outputs and in conditions, and DuckDB and Iceberg struct columns are listed field by field.
- Without compiled SQL, dbt models are rendered by kerplunk in a sandbox: loops, variables, `ref`, `source`, `config`, the project's and packages' macros (`dbt_utils.*`), `adapter.dispatch` and dbt's cross-database macros (`dbt.date_trunc`, `dbt.cast`, `dbt.concat`, `dbt.hash`, `dbt.date_spine` and others). An unknown macro becomes a function call that keeps its arguments, so the columns passed to it stay in the lineage.
- dbt models, warehouse tables with the same relation name, Airflow SQL targets and Iceberg tables merge into one node, so lineage crosses tools: a column computed in dbt, copied into a reporting table by an Airflow task, and used by a dashboard is one chain.

## Limitations

- Lineage is static. SQL built at runtime, Jinja that dbt has not compiled, Python models, stored procedures and UDF internals are not followed; each gap is listed under Warnings in `REPORT.md`.
- Without compiled SQL, macros that query the warehouse while compiling (`run_query`, introspective `dbt_utils.get_column_values`) render as empty results, so models that depend on them may differ from dbt's compiled SQL.
- `SELECT *` over a relation whose columns kerplunk cannot learn (no warehouse, no catalog) still gets lineage for the columns the query goes on to use, and is reported as a warning; columns passed straight through by the star to the output are not known.
- Airflow parsing covers the common ways of writing DAGs. Task ids or SQL built in loops or from runtime values, dynamic task mapping over computed lists, and DAG factories are skipped with a warning. Selections by `--selector`, `state:`, `result:` and `source_status:` are not resolved.
- Identifiers are matched case-insensitively. Two relations whose names differ only by case are treated as one.
- `query` ranks by words, not meaning. It finds `customer_lifetime_value` from "lifetime value" but not from "how much a customer is worth"; the agent is expected to follow up with `search` or `describe`.
- Struct fields are followed, but not elements of lists or values of maps: `items[1].sku` traces to `items`.
- `verify` runs against a local DuckDB file or a Postgres database (in a transaction that is rolled back); other warehouses are not supported yet. It perturbs columns one at a time, so influences that need two inputs to change together are not observed.
- `diff` reads dbt YAML for tests, descriptions, sources and exposures, but not for other configuration (materialisation, contracts, meta).

### Not Yet (v1)

- Dagster assets and jobs.
- Snowflake, BigQuery and Databricks metadata connectors as optional extras.
- Column-level lineage for dbt Python models and through UDFs.
- `verify` against sampled remote warehouses (Snowflake, BigQuery, Databricks).
- Incremental warehouse metadata (it is re-read on every build; it is fast for local warehouses but not for large remote ones).

## CI/CD

Build and Test runs on every push to `main` and on pull requests: Ruff and mypy, a commit message check on pull requests, the test suite on Python 3.11, 3.12 and 3.13 against Postgres and Iceberg REST catalog service containers, an end-to-end build of the jaffle_shop example that fails if it takes a minute or more, an install of the built wheel with `uv tool install`, the real-world jaffle-shop test, and the Airflow parity test. The test job also runs the browser tests in Chromium and fails below 90% branch coverage. Create Release runs automatically after Build and Test passes on a push to `main`, and releases the commit that was tested. commitizen reads the Conventional Commits since the last tag (`fix` gives a patch release, `feat` a minor one, a breaking change the next major, or minor while the version is 0.x), updates `pyproject.toml`, `src/kerplunk/__init__.py`, `uv.lock` and `CHANGELOG.md`, commits `chore(release): Release X.Y.Z`, tags `vX.Y.Z` and publishes a GitHub release with the changelog, wheel and sdist. A push with nothing releasable (only `docs`, `ci`, `test` or `chore` commits) makes no release. It can also be run by hand from the Actions tab to force a patch, minor or major release. Publishing to PyPI is a separate job that stays off until the `PUBLISH_TO_PYPI` repository variable is `true` and a `pypi` environment with trusted publishing exists.

```mermaid
graph LR
    subgraph "Source Control"
        GitHub[GitHub<br/>Repository]
    end

    subgraph "Build & Test Workflow"
        Lint[Ruff and mypy]
        Commits[Commit messages]
        Tests[Tests<br/>Python 3.11 to 3.13]
        E2E[End to end<br/>jaffle_shop]
        Package[Wheel install]
    end

    subgraph "Services"
        Postgres[Postgres 17]
        Iceberg[Iceberg REST<br/>catalog]
    end

    subgraph "Release Workflow"
        Bump[commitizen bump<br/>and changelog]
        Release[GitHub release]
        PyPI[PyPI<br/>off by default]
    end

    GitHub -->|Push/PR| Lint
    GitHub -->|PR| Commits
    GitHub -->|Push/PR| Tests
    GitHub -->|Push/PR| E2E
    GitHub -->|Push/PR| Package
    Postgres --> Tests
    Iceberg --> Tests
    E2E -->|All pass on main| Bump
    Bump --> Release
    Release --> PyPI

    style GitHub fill:#24292e,stroke:#333,stroke-width:2px,color:#fff
    style Lint fill:#2088ff,stroke:#333,stroke-width:2px,color:#fff
    style Commits fill:#2088ff,stroke:#333,stroke-width:2px,color:#fff
    style Tests fill:#2088ff,stroke:#333,stroke-width:2px,color:#fff
    style E2E fill:#2088ff,stroke:#333,stroke-width:2px,color:#fff
    style Package fill:#2088ff,stroke:#333,stroke-width:2px,color:#fff
    style Postgres fill:#336791,stroke:#333,stroke-width:2px,color:#fff
    style Iceberg fill:#4a5568,stroke:#333,stroke-width:2px,color:#fff
    style Bump fill:#6f42c1,stroke:#333,stroke-width:2px,color:#fff
    style Release fill:#6f42c1,stroke:#333,stroke-width:2px,color:#fff
    style PyPI fill:#f96,stroke:#333,stroke-width:2px
```

## Development

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for the project)
- Docker, for the Postgres and Iceberg integration tests
- [vhs](https://github.com/charmbracelet/vhs), only to re-record the demo GIFs

### Installation

1. **Clone the repository**
   ```bash
   git clone git@github.com:kodexlabsio/kerplunk.git
   cd kerplunk
   ```

2. **Install dependencies and the git hooks**
   ```bash
   uv sync
   uv run pre-commit install
   ```
   The hooks run Ruff and mypy before each commit, check the commit message, and run the tests before each push.

### Tests

```bash
uv run playwright install chromium              # once, for the browser tests
uv run pytest                                   # Postgres and Iceberg REST tests skip without services
eval "$(.kodex/scripts/services.sh up)"         # start them in Docker and export their addresses
uv run pytest --cov                             # everything, with branch coverage (fails under 90%)
.kodex/scripts/services.sh down
```

| Kind | Where | What it checks |
|------|-------|----------------|
| Unit | `test_graph`, `test_sql`, `test_dbt`, `test_airflow`, `test_report`, `test_skill`, ... | Each module, including lineage through CTEs, joins, aliases, `SELECT *`, unions and window functions |
| Property-based | `test_properties` | Invariants on generated inputs with Hypothesis: impact and trace are inverse, lineage matches generated CTE chains in seven dialects, Airflow dependencies match generated DAGs, no text crashes the SQL analysis |
| Integration | `test_warehouse`, `test_build` | DuckDB, Postgres and Iceberg (SQL and REST catalogs) for real; builds of projects with and without dbt |
| End to end | `test_build`, `test_cli`, `test_query`, `test_mcp` | The example built with real dbt, every CLI command, the Python API, and the MCP server in process and over stdio |
| Golden files | `test_golden`, `tests/golden/` | The exact text of the CLI output and REPORT.md; regenerate with `KERPLUNK_UPDATE_GOLDEN=1` and review the diff |
| Browser | `test_browser` | graph.html in Chromium: selection, column lineage, search, hostile text, a 400-relation graph |
| Scale | `test_scale` | A generated 300-model project: full build and `--update` times |
| Real world | `test_realworld` | dbt Labs' jaffle-shop at a pinned commit (needs network; `KERPLUNK_REALWORLD=1`) |
| Lineage against data | `test_verify` | `verify` confirms true lineage and catches removed edges, false edges and dropped filters; a differential property test runs generated queries on generated data and requires the static lineage to agree with execution |
| Change review | `test_diff` | `diff` on real git repositories: formatting and CTE renames are not changes, removed columns still read downstream are breaking, conditions, DAGs, two commits, all output formats, and dbt Jinja rendering |
| Parity | `test_airflow_parity` | The static Airflow parser against Airflow's own DagBag (needs Airflow; see the file for the command) |

The dev dependencies include dbt-core and dbt-duckdb, so the dbt and end-to-end tests build a copy of the example project for real. In CI, `KERPLUNK_REQUIRE_SERVICES=1` turns a missing service or browser into a failure rather than a skip, and `HYPOTHESIS_PROFILE=ci` runs more generated examples.

### Other Commands

- `uv run ruff check`, `uv run ruff format`, `uv run mypy`: lint, format and type check (strict)
- `uv run .kodex/scripts/prepare_example.py`: build the example warehouse
- `vhs .kodex/scripts/demo-cli.tape`: re-record `docs/demo-cli.gif`
- `vhs .kodex/scripts/demo-claude-code.tape`: record the same question through `/kerplunk` in Claude Code (needs Claude Code signed in)

### Commits

Commits follow [Conventional Commits](https://www.conventionalcommits.org/), checked by commitizen and by `.kodex/scripts/check_commit_message.py`, which applies the Kodex Labs rules: a fixed list of scopes, sentence-case subjects, lines of at most 100 characters, and no AI attribution lines. The scopes are `airflow`, `dbt`, `iceberg`, `sql`, `warehouse`, `build`, `graph`, `html`, `lineage`, `query`, `report`, `cli`, `mcp`, `skill`, `examples`, `readme`, `tests`, `ci`, `config`, `deps`, `deps-dev` and `release`.

### Repository Layout

| Path | Contents |
|------|----------|
| `src/kerplunk/graph.py` | Graph model, name resolution, impact, trace and path |
| `src/kerplunk/sql.py` | sqlglot column lineage |
| `src/kerplunk/dbt.py`, `airflow.py`, `warehouse.py` | Inputs |
| `src/kerplunk/config.py`, `build.py` | Configuration, discovery, the build and its cache |
| `src/kerplunk/query.py`, `report.py`, `html.py` | Questions over the graph, REPORT.md and graph.html |
| `src/kerplunk/project.py` | The Python API |
| `src/kerplunk/cli.py`, `console.py`, `status.py`, `mcp_server.py`, `skill.py` | Interfaces |
| `examples/jaffle_shop` | dbt Labs' jaffle_shop_duckdb (Apache-2.0, see its NOTICE) with an Airflow DAG |
| `.kodex/scripts` | Project scripts: example preparation, test services, commit rules, demo tapes |

## Licence

MIT. See [LICENSE](LICENSE). The jaffle_shop example keeps its own Apache-2.0 licence.
