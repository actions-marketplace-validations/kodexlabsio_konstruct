<p align="center">
<a target="_blank" href="https://kodexlabs.io/"><img width="150" height="150" src="./docs/logo-icon.svg"></a>
</p>
<p align="center">
<a href="https://github.com/kodexlabsio/konstruct/actions/workflows/build-and-test.yml"><img src="https://github.com/kodexlabsio/konstruct/actions/workflows/build-and-test.yml/badge.svg" alt="Build and Test"></a>&nbsp;<a href="https://github.com/kodexlabsio/konstruct/actions/workflows/release.yml"><img src="https://github.com/kodexlabsio/konstruct/actions/workflows/release.yml/badge.svg" alt="Create Release"></a>&nbsp;<a href="./LICENSE"><img src="https://img.shields.io/badge/licence-MIT-blue" alt="MIT licence"></a>&nbsp;<img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue" alt="Python 3.11 to 3.13">
<br />
<a href="https://kodexlabs.io/" target="_blank">Kodex Labs</a> Ltd.
</p>

**Column-level lineage for dbt, SQL, Airflow and Dagster: explained as formulas, checked against the data, and reviewed on every pull request.**

Konstruct reads a data repository and its warehouse and builds a graph of every table, column, model, task, test and dashboard, with column-level lineage parsed from the SQL that connects them. It runs locally in one command, with no server and no account, and answers from the command line, a Python API, an MCP server and `/konstruct` skill for coding agents, a GitHub Action, and a single offline HTML page.

What it does that lineage tools usually leave out:

- **It shows how a column is calculated.** `konstruct explain` composes a column's SQL through every model upstream, so `customer_ltv.lifetime_value` reads as `SUM(raw_payments.amount / 100)`, with the joins and filters of each step listed beside it. A test evaluates composed formulas on the data and checks they give the model's values row for row. [Explaining a Column](#explaining-a-column)
- **It checks its lineage against the data.** `konstruct verify` perturbs each input column, reruns each model and records which outputs change. On dbt Labs' jaffle-shop all 87 claimed column edges are confirmed, and nothing changed that the graph did not predict. [How Much to Trust the Lineage](#how-much-to-trust-the-lineage)
- **It reviews pull requests column by column.** `uses: kodexlabsio/konstruct@v0` comments with what a change does to each column, ignoring formatting and renamed CTEs, and fails the check when a removed column is still read downstream. It also prints a `dbt build --select` for only what the change affects. [Reviewing Changes](#reviewing-changes)
- **One chain across tools.** A column computed in dbt, copied by an Airflow SQL task, written by a Dagster asset and shown on a dashboard is one lineage chain, through DuckDB, Postgres, Snowflake, BigQuery, Databricks and Iceberg.
- **It handles large projects.** 2,000 models and 52,000 column edges build in 26 seconds and update in 4; queries answer in milliseconds. [Performance](#performance)
- **It exports open formats.** OpenLineage events validated against the specification, Mermaid, Graphviz and CSV. [Exporting](#exporting)

![graph.html showing the lineage of customer_ltv.lifetime_value: four columns across an Airflow SQL table, two dbt models and a seed, each verified, with the formula SUM(raw_payments.amount / 100)](docs/graph-html.png)

## Quick Start

Konstruct is not on PyPI yet, so install it from GitHub with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/kodexlabsio/konstruct
```

Or run it without installing anything, each time:

```bash
uvx --from git+https://github.com/kodexlabsio/konstruct konstruct build
```

For a warehouse extra, name it in the requirement, for example
`uv tool install "konstruct[snowflake] @ git+https://github.com/kodexlabsio/konstruct"` (or `[bigquery]`, `[databricks]`, `[all]`).
To pin a release, add its tag: `git+https://github.com/kodexlabsio/konstruct@v0.8.0`. Each [release](https://github.com/kodexlabsio/konstruct/releases) also has the wheel attached.

Then, in a repository with dbt models, SQL, DAGs or a warehouse:

```bash
cd your-data-repo
konstruct status                 # what konstruct finds here, before building anything
konstruct build                  # writes konstruct-out/graph.json, REPORT.md and graph.html
konstruct impact orders.amount   # everything downstream of a column
konstruct open                   # the interactive graph in a browser
konstruct install                # adds the /konstruct skill to Claude Code and Cursor
```

![konstruct impact analysis on the jaffle_shop example](docs/demo-cli.gif)

To try it on the bundled example (jaffle_shop on DuckDB, with an Airflow DAG), from a clone of this repository:

```bash
uv sync
uv run .kodex/scripts/prepare_example.py      # builds the example's DuckDB warehouse with dbt
uv run konstruct build examples/jaffle_shop    # about a second
uv run konstruct impact stg_payments.amount -C examples/jaffle_shop
```

`konstruct build` needs no configuration: it finds dbt projects, Airflow DAG files, Dagster definitions, SQL files, DuckDB files and the warehouse each dbt profile points at (DuckDB, Postgres, Snowflake, BigQuery or Databricks), and prints what it found and what it did with each. If a dbt project's `target/manifest.json` is missing or older than the project files and `dbt` is on `PATH`, it runs `dbt docs generate` first. `konstruct init` writes a commented `konstruct.toml` recording what discovery found, for when you want to pin or change it.

```text
$ konstruct build
konstruct  /src/jaffle_shop

  dbt        jaffle_shop (.)  5 models, 3 seeds, 20 tests, 1 exposure  (artefacts up to date)
  duckdb     duckdb jaffle_shop.duckdb  9 relations  (dbt profile jaffle_shop)
  airflow    dags/jaffle_shop_daily.py  1 DAG, 6 tasks

  graph      1 dag, 1 exposure, 5 models, 3 seeds, 1 table, 6 tasks, 20 tests; 44 columns
  lineage    32 of 32 columns of models and built tables (100%)
  warnings   none
  wrote      graph.json, REPORT.md, graph.html to konstruct-out in 0.7s
```

## Supported Inputs

| Input | What Konstruct reads | Status |
|-------|---------------------|--------|
| **dbt** | `target/manifest.json` and `catalog.json`: models, seeds, snapshots, sources, tests (attached to columns), exposures, descriptions, column types. Compiled SQL for lineage; `ref()` and `source()` are resolved from the manifest when only raw SQL is available | v0 |
| **SQL** | Column-level lineage with [sqlglot](https://github.com/tobymao/sqlglot) for dbt models, warehouse views, Airflow SQL and standalone `.sql` files (`CREATE ... AS`, `INSERT ... SELECT`, `MERGE`, `UPDATE`, `DELETE`, `COPY`). Dialect from the dbt adapter or configuration | v0 |
| **Airflow** | DAG files parsed statically (never imported): `with DAG`, assigned DAGs, `@dag`, TaskFlow, TaskGroups, `>>`, `<<`, `chain`, `cross_downstream`. Per task: dbt commands (BashOperator, `@task.bash`, Cosmos, dbt operators) resolved to models with dbt's selection syntax; SQL inline or from files; target and source tables; inlet and outlet assets | v0 |
| **DuckDB** | Tables, views, columns, types, comments and view definitions, read only | v0 |
| **Postgres** | Tables, views, materialised views, partitioned tables, columns, types, comments and view definitions, read only | v0 |
| **Iceberg** | Any PyIceberg catalog, REST included: namespaces, tables, columns (nested struct fields as `parent.child`), field ids, identifier fields, partitioning, current snapshot. Metadata only; no data files are read | v0 |
| **Dagster** | Definition files parsed statically (never imported): `@asset`, `@multi_asset` and `@dbt_assets` (with its dbt selection), keys and prefixes, dependencies from parameters, `ins=` and `deps=`, groups, asset jobs with their selections, and schedules. An asset's data is the dbt model, warehouse table or SQL target of the same name, and SQL an asset runs gets column lineage. Op-based jobs: `@op`, `@graph` and `@job` (nested graphs, `.alias()`, fan-in, `.map()`/`.collect()`, `graph.to_job()`), one task per op invocation named by its Dagster handle, with column lineage from each op's SQL and dbt runs from `dbt.cli([...])`. `@schedule` and sensors (`@sensor`, `@asset_sensor`, `@multi_asset_sensor`, `@run_status_sensor`, `@run_failure_sensor`): an asset sensor orders its job after the asset, a success sensor after the monitored job | v0 |
| **Business definitions** | `definitions.yml` files: each term's name, synonyms, definition, owner, status (draft, agreed, approved, retired), approval date, grain, bound columns, required filters, unit, currency, related terms and exceptions. Terms become `term` nodes with `defines` edges to their columns, and the build warns when a bound column or filter column no longer exists (see [Business Definitions](#business-definitions)) | v0 |
| **Snowflake** | Tables, views, columns, types, comments and view definitions from each database's `information_schema`, through `konstruct[snowflake]`. Tested against fakesnow, an emulator of Snowflake's Python connector | v0 |
| **BigQuery** | Datasets, tables, views and view queries through the client API, with nested `RECORD` fields and their descriptions, through `konstruct[bigquery]`. Tested against the BigQuery emulator | v0 |
| **Databricks** | Unity Catalog `information_schema` for each catalogue, including struct columns, through `konstruct[databricks]`. Tested against a stub driver only: there is no Databricks emulator | v0 |

## Commands

| Command | Purpose |
|---------|---------|
| `konstruct build [path] [-u]` | Build `konstruct-out/`. `-u/--update` reuses cached analyses of anything unchanged (keyed by file hashes, compiled SQL, manifest checksums and the upstream columns each model sees). `--no-dbt`, `--no-warehouse`, `--dialect`, `--out`, `--json` (a machine-readable summary), `--strict` (exit 1 on warnings) |
| `konstruct status` | What Konstruct finds (configuration, dbt projects and whether their artefacts are current, warehouses, DAG and SQL files), whether the graph is out of date and which inputs changed, and whether the skill is installed |
| `konstruct init` | Write a commented `konstruct.toml` from what discovery found (`--stdout` to print it) |
| `konstruct impact <table.column>` | Everything downstream: derived columns, relations, models that join, filter or group on it, tests, exposures and the DAG tasks that run them |
| `konstruct trace <table.column>` | Everything upstream, each step marked `direct` (passed through or renamed) or `transformed` |
| `konstruct path <a> <b>` | Shortest connection between two tables or columns |
| `konstruct search <words>` | Find nodes by name or description, optionally `--kind model` |
| `konstruct conflicts` | Columns that compute the same business idea in different ways, with the sources, filters, joins and aggregations that differ and the file each is in, ranked by downstream reach. Writes `CONFLICTS.md` and `conflicts.json`. `--format markdown`, `--fail-on-conflict`, `-n N` (see [Finding Conflicting Definitions](#finding-conflicting-definitions)) |
| `konstruct define <term>` | What a business term means: its definition, owner, status, grain, required filters and the columns that hold it. Takes a term, a synonym or a column (to list the terms that define it). Exit status 3 when no term matches |
| `konstruct explain <table.column>` | How a column is calculated: its expression as SQL with CTEs and aliases inlined, the same formula composed down to source columns, and step by step each column on the way with the joins, filters and grouping that shape its rows (see [Explaining a Column](#explaining-a-column)) |
| `konstruct describe <name>` | Columns, types, descriptions, tests and neighbours of one node; for a task, what it runs and its neighbours in the DAG |
| `konstruct list [kind ...]` | Every node of the given kinds (`models`, `tables`, `sources`, `tasks`, ...; default all relations) |
| `konstruct open` | Open `graph.html` in a browser |
| `konstruct export <format>` | The graph as OpenLineage events, Mermaid, Graphviz DOT or CSV (see [Exporting](#exporting)). `-s NAME` for the lineage of one table or column, `--columns` to draw columns, `-o FILE` |
| `konstruct diff [--base REF]` | What the working tree (or `--head REF`) changes compared with the base (default: the merge base with main): columns added, removed or changed with their expressions before and after, changed join and filter conditions, breaking changes, DAG changes, dbt tests, sources and exposures added or removed in YAML, the downstream impact of each, and a column-aware `dbt build --select` that rebuilds only what the change affects. `--format markdown` for a pull request comment, `--fail-on-breaking` for CI |
| `konstruct verify` | Test the column lineage against the data in a local DuckDB warehouse by perturbation; writes `VERIFY.md` and `verify.json` and marks confirmed edges in the graph. `--seed`, `--rows`, `--model`, `--strict` |
| `konstruct query "<question>"` | Free-text question: ranks nodes by its words and returns them with their connections, plus impact or lineage when the question asks for it. There is no language model in Konstruct; the agent reads the result |
| `konstruct install` | Register the `/konstruct` skill (see Agent Integration) |
| `konstruct mcp` | Run the MCP server on stdio |

Tables and columns can be named as `orders`, `main.orders`, `orders.amount`, `jaffle_shop.main.orders.amount`, `ecom.raw_orders` for a dbt source, `dag_id.task_id` for a task, or by dbt unique id. When nothing matches, Konstruct suggests close names; when a name is ambiguous, it lists the candidates.

Every command takes `-C DIR` (before or after the command name) to work on another directory; query commands also find `konstruct-out` in any parent directory. Query commands build the graph if there is none, warn when inputs changed since the last build, and take `--refresh` to update it first, `--json` for structured output and `--graph FILE` to read a specific graph. Output is coloured on a terminal unless `NO_COLOR` is set or `--no-color` is given. Exit status is 0 on success, 1 on an error, 2 on a usage error and 3 when `path` finds no connection.

## Python API

```python
import konstruct

project = konstruct.Project('path/to/repo')
project.build(update=True)

walk = project.impact('stg_payments.amount')
print(walk.summary())  # 7 columns, 3 relations, 5 tests, 1 exposure and 2 tasks downstream.
for column in walk.columns:
    print(column.label, column.transform, column.path)

print(project.trace('customer_ltv.lifetime_value'))  # the same text the CLI prints
project.describe('orders').columns  # typed results
project.explain('orders.amount').composed  # "SUM(raw_payments.amount / 100)"
project.conflicts().conflicts[0].differences  # what two revenue columns disagree on
project.define('lifetime value').terms[0].owner  # {"name": "Priya Shah", "role": "Head of Finance"}
project.path_between('raw_payments', 'customer_dashboard').found
project.search('lifetime value', kinds=['column']).results
project.list(['model']).items
project.changed_inputs()  # files changed since the last build
project.diff(base='origin/main').breaking  # removed columns still read downstream
project.verify(seed=7).totals()  # {"claimed": 36, "confirmed": 36, "unexplained": 0, ...}
```

Results are dataclasses (`Walk`, `PathResult`, `SearchResult`, `Description`, `Listing`, `QueryResult` made of `Item`s, plus `Definitions` of `TermDefinition`s, `Explanation`, `Diff` and `Verification`). `str(result)` gives the CLI's text and `result.to_dict()` the JSON the CLI and MCP server return. A name that matches nothing, or several things, raises `konstruct.QueryError` with suggestions. `project.graph` is the underlying `konstruct.Graph` for anything the API does not cover.

## Outputs

| File | Contents |
|------|----------|
| `graph.json` | Nodes (`source`, `table`, `column`, `model`, `seed`, `snapshot`, `dag`, `task`, `test`, `exposure`, `term`) and edges (below), with descriptions, types, file paths and the inputs each came from |
| `REPORT.md` | Summary and coverage, the most queried columns (with usage on), business terms, the most used columns, orphans, untested models, columns with no lineage, columns documented in dbt but missing from the warehouse, and warnings |
| `graph.html` | A single offline file (open it with `konstruct open`): tables, models and tasks in dependency order; cards that expand to their columns; column lineage drawn between columns, isolated and fitted to the screen; each column's formula in terms of its sources; the conditions that shape each model; verification results; search, deep links to any table or column, dark and light themes |

| Edge | Direction | Meaning |
|------|-----------|---------|
| `contains` | relation to column | The column belongs to the relation |
| `feeds` | upstream to downstream | Relation to relation, task to the table it writes, table to the task that reads it. With `role: reference`, a column the target uses in a JOIN, WHERE, GROUP BY, HAVING or QUALIFY |
| `derived_from` | column to the column it comes from | Column-level lineage, with `transform: direct` or `transformed` |
| `runs` | DAG to task, task to dbt node | What a DAG or task executes |
| `precedes` | task to task | Airflow ordering, Dagster op and asset ordering, and sensors that start one job after another |
| `tests` | test to relation or column | What a dbt test checks |
| `defines` | term to column or relation | The column that holds a business term |
| `relates_to` | term to term | Related business terms |

With **Columns** on, graph.html is a column-level map of the whole project:

![graph.html with every table expanded to its columns and column lineage drawn between them](docs/graph-columns.png)

## Exporting

`konstruct export` hands the lineage to other tools:

| Format | What it writes | Use |
|--------|----------------|-----|
| `openlineage` | One OpenLineage `JobEvent` per built table, as JSON lines: inputs, outputs with schema and column lineage facets, each column's input fields with `DIRECT` transformations (`IDENTITY`, `TRANSFORMATION` or `AGGREGATION`, with the expression), and the columns used in joins, filters and grouping as dataset-wide `INDIRECT` dependencies (`JOIN`, `FILTER`, `GROUP_BY`) | Load into Marquez, DataHub, OpenMetadata or any OpenLineage consumer |
| `mermaid` | A `flowchart` of tables, models and tasks, or of columns with `--columns` | Documentation, pull requests, GitHub renders it inline |
| `dot` | A Graphviz graph, with column ports under `--columns` | Large static diagrams |
| `csv` | One row per column edge: relation, column, upstream relation and column, transform, verification and expression | Spreadsheets, or a lineage table in the warehouse |

```bash
konstruct export openlineage -o lineage.jsonl --namespace duckdb://analytics
konstruct export mermaid -s orders.amount --columns > docs/orders-amount.mmd
```

The tests validate every OpenLineage event, and its column lineage and schema facets, against the OpenLineage 2-0-2 JSON schemas.

## Agent Integration

`konstruct install` writes `~/.claude/skills/konstruct/SKILL.md`. Claude Code and Cursor both read that location, so `/konstruct what breaks if I change stg_payments.amount?` works in either. The skill tells the agent to keep the graph current with `--refresh`, to run `impact` before changing a model or column, and how to read the output. It pre-approves `konstruct` shell commands for the turn it runs in. `konstruct status` shows where it is installed.

| Option | Effect |
|--------|--------|
| `--agent claude\|cursor\|both` | `cursor` writes `~/.cursor/skills` instead. Default `both` |
| `--target <dir>` | Install into a repository (`.claude/skills`) rather than the home directory, so the team shares it |
| `--mcp` | With `--target`, also register `konstruct mcp` in `.mcp.json` (Claude Code) and `.cursor/mcp.json` (Cursor), keeping any other servers |
| `--force` | Replace a skill that was edited by hand; otherwise a reinstall only replaces konstruct's own earlier versions |

The MCP server exposes `impact`, `trace`, `search`, `describe`, `conflicts`, `define`, `explain`, `path`, `query` and `diff`, returns the same structured results as `--json`, and rereads `graph.json` when a build changes it.

## Configuration

Configuration is optional. Put it in `konstruct.toml` at the project root, or under `[tool.konstruct]` in `pyproject.toml`. `${VAR}` and `${VAR:-default}` read the environment, so credentials stay out of the file.

```toml
dialect = "postgres"              # default: the dbt adapter, then the first warehouse
default_catalog = "analytics"     # for unqualified tables in Airflow and SQL files
default_schema = "public"
exclude = ["scratch/**"]
out_dir = "konstruct-out"

[dbt]
projects = ["transform"]          # default: every dbt_project.yml found
run = true                        # run `dbt docs generate` when artefacts are missing or stale

[airflow]
dags = ["orchestration/dags"]     # default: Python files that look like DAGs

[sql]
paths = ["sql/**/*.sql"]          # default: .sql files outside dbt folders and not used by DAGs

[definitions]
paths = ["definitions.yml"]       # business terms; default: every definitions.yml

[usage]                           # column usage from query history; off by default
enabled = true
days = 90
exclude_users = ["DBT_SERVICE"]

[[warehouse]]                     # setting any warehouse turns discovery off
type = "postgres"
dsn = "${WAREHOUSE_DSN}"
schemas = ["raw", "analytics"]

[[warehouse]]
type = "duckdb"
path = "data/local.duckdb"

[[warehouse]]                     # needs konstruct[snowflake]; options go to snowflake.connector.connect
type = "snowflake"
account = "acme-xy12345"
user = "${SNOWFLAKE_USER}"
password = "${SNOWFLAKE_PASSWORD}"
database = "ANALYTICS"
databases = ["RAW", "ANALYTICS"]  # optional: more than one database

[[warehouse]]                     # needs konstruct[bigquery]; uses application default credentials
type = "bigquery"
project = "my-project"
datasets = ["shop", "marts"]

[[warehouse]]                     # needs konstruct[databricks]
type = "databricks"
server_hostname = "${DATABRICKS_HOST}"
http_path = "${DATABRICKS_HTTP_PATH}"
access_token = "${DATABRICKS_TOKEN}"
catalogs = ["main"]

[[warehouse]]
type = "iceberg"
name = "lake"                     # the catalogue name SQL uses, e.g. lake.sales.orders
uri = "http://localhost:8181"
namespaces = ["sales"]
[warehouse.properties]            # passed to pyiceberg.catalog.load_catalog
credential = "${ICEBERG_CREDENTIAL}"
```

## Business Definitions

Lineage says where a number comes from; it does not say what the business means by it. `definitions.yml` records that in the repository, next to the models, reviewed in pull requests like code:

```yaml
version: 1
terms:
  - name: Customer lifetime value
    synonyms: [lifetime value, LTV, CLV]
    definition: Total value of every payment a customer has made, in Australian dollars.
    owner: {name: Priya Shah, role: Head of Finance}
    status: approved              # draft, agreed, approved or retired
    approved: 2026-10-30
    grain: one row per customer
    columns: [customers.customer_lifetime_value, customer_ltv.lifetime_value]
    filters: ["orders.status <> 'returned'"]   # filters any answer must apply
    unit: currency
    currency: AUD
    related: [Order amount]
    exceptions: Refunds are not netted off.
```

The build attaches each term to the columns it names, so the definition travels with the lineage:

```text
$ konstruct define lifetime value
Customer lifetime value  [approved, Priya Shah (Head of Finance), approved 2026-10-30]
  Total value of every payment a customer has made, across all orders and payment methods, in Australian dollars.
  also called: lifetime value, LTV, CLV
  grain: one row per customer
  unit: currency AUD
  exceptions: Refunds are not netted off; returned orders still count.
  related: Order amount, Customer
  defined in: definitions.yml:5
  columns:
    customer_ltv.lifetime_value  DOUBLE
    customers.customer_lifetime_value  DOUBLE
```

- **Agents get the agreed answer.** The `define` MCP tool returns the approved definition, owner, required filters and columns for a term or a column, and `query` puts definitions first, so an agent asked "what is our lifetime value?" starts from the wording the business approved.
- **Changes show what they touch.** `impact` lists the business terms a change reaches, so changing `stg_payments.amount` names *Customer lifetime value* and *Order amount*. `describe` shows the terms that define a column. graph.html searches terms by name and synonym and highlights their columns.
- **Definitions cannot drift silently.** The build warns, and fails under `--strict`, when a bound column or a column in a required filter no longer exists (naming the likely rename), when an approved term has no owner or approval date, when an agreed or approved term is bound to nothing, when two terms share a name or synonym, and when a related term is not defined. `REPORT.md` lists every term with its status, owner and columns.
- **Pull requests review them.** `konstruct diff` reports terms added, removed and changed field by field, and flags an approved term whose definition, grain, columns or filters changed while it stayed approved with the same date.

Konstruct reads every `definitions.yml` (or `.yaml`) in the repository; `[definitions] paths` in `konstruct.toml` pins the files. The example project has five terms in [`examples/jaffle_shop/definitions.yml`](examples/jaffle_shop/definitions.yml).

## Column Usage from Query History

The graph knows what depends on what; it does not know what people actually read. With usage on, Konstruct reads the warehouse's query history, counts the queries and distinct users that read each column over a window, and adds that to the graph: a usage score on each column (0 to 100 on a log scale), a "Most queried columns" table in `REPORT.md`, a line in `describe`, and a tag in graph.html. It reads metadata only, once a day, and nothing is sent anywhere.

```toml
[usage]
enabled = true                  # off by default
days = 90
exclude_users = ["DBT_SERVICE"] # leave out pipeline and orchestrator accounts
# objects = "base"              # Snowflake: count underlying tables rather than what queries named
```

| Warehouse | Source | Columns from | History | Access needed |
|-----------|--------|--------------|---------|---------------|
| Snowflake | [`SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY`](https://docs.snowflake.com/en/sql-reference/account-usage/access_history) | The columns recorded for each object the query named (`DIRECT_OBJECTS_ACCESSED`), or with `objects = "base"` the underlying tables' columns, including those used only in filters and joins | 365 days; up to 3 hours behind; Enterprise Edition or higher | The `SNOWFLAKE.GOVERNANCE_VIEWER` database role, or `IMPORTED PRIVILEGES` on the SNOWFLAKE database ([access](https://docs.snowflake.com/en/sql-reference/account-usage)) |
| BigQuery | [`region-REGION.INFORMATION_SCHEMA.JOBS`](https://cloud.google.com/bigquery/docs/information-schema-jobs) | The view records tables but not columns, so Konstruct parses each distinct query text with its own SQL analysis, counting output columns' sources and the columns used in joins, filters and grouping | 180 days | `bigquery.jobs.listAll` on the project (BigQuery Resource Viewer) |
| Databricks | [`system.access.column_lineage`](https://docs.databricks.com/aws/en/admin/system-tables/lineage) | Every column a query read, including reads with no target table | 395 days | `USE CATALOG` on `system`, `USE SCHEMA` and `SELECT` on `system.access` ([system tables](https://docs.databricks.com/aws/en/admin/system-tables/)) |

The view names, retention and permissions above were checked against each vendor's documentation in November 2026. Without the access, the build carries on and warns `cannot read query history` with the warehouse's message. BigQuery takes the region from the warehouse's `location` (default `us`).

## Finding Conflicting Definitions

Most data stacks compute their key measures more than once: revenue in a finance mart, again in a marketing table, again in a dashboard extract. `konstruct conflicts` finds those columns and shows exactly how they disagree, before the numbers do.

```text
$ konstruct conflicts
1 conflicting definition among 5 computed columns.

revenue
  daily_revenue.revenue  vs  daily_performance.total_revenue  (have names that mean the same thing; downstream weight 6)
    a: daily_revenue.revenue = SUM(orders.amount)
       in finance/revenue.sql; feeds 1 columns
    b: daily_performance.total_revenue = SUM(orders.amount)
       in marketing/daily.sql; feeds 1 tasks, 2 columns
       carried into weekly.revenue
    - filter only in daily_revenue.revenue: WHERE: orders.status = 'completed'  (finance/revenue.sql)
```

- **Which columns are compared.** Computed columns whose names mean the same thing once qualifiers and synonyms are folded together (`revenue`, `total_revenue`, `sales_amount`), columns named like a business term or its synonyms in `definitions.yml`, and columns bound to the same term. Keys, plain copies and columns derived from one another are left out: a column built from another inherits its definition and is listed as *carried into* it.
- **What is compared.** Each column's formula in terms of source columns (from `explain`), the source columns themselves, the filters and joins on its whole route back to source with the file each is in, and the aggregation at the top. Pairs that agree on all of these are not reported.
- **What comes first.** Conflicts are ranked by what depends on each side: exposures and dashboards, approved business terms, orchestration tasks and downstream columns.

`CONFLICTS.md` (one section per conflict, with a table of both formulas and a list of differences) and `conflicts.json` are written beside the graph, ready to take into a meeting where the definition gets agreed and recorded in `definitions.yml`. The `conflicts` MCP tool gives an agent the same list, so it can check whether a measure already exists before adding another.

## Explaining a Column

Lineage says which columns a value comes from. `konstruct explain` says how it is calculated. Each column's SQL is kept with its CTEs, subqueries and aliases inlined, then composed through every model and SQL task upstream, so the formula is stated in terms of the source columns:

```text
$ konstruct explain customer_ltv.lifetime_value
customer_ltv.lifetime_value  (DOUBLE)

  = customers.customer_lifetime_value

In terms of sources
  = SUM(raw_payments.amount / 100)

Sources (1)
  raw_payments.amount

Step by step
  customer_ltv.lifetime_value = customers.customer_lifetime_value  [verified]
      WHERE: customers.customer_lifetime_value > 0
  customers.customer_lifetime_value = SUM(stg_payments.amount)  [verified]
      GROUP BY: stg_orders.customer_id
      LEFT JOIN ON: stg_customers.customer_id = stg_orders.customer_id
      LEFT JOIN ON: stg_payments.order_id = stg_orders.order_id
  stg_payments.amount = raw_payments.amount / 100  [verified]
```

That chain crosses an Airflow SQL file, a dbt mart and a dbt staging model. The composed formula describes the value; which rows feed it is decided by the joins, filters and grouping listed under each step, which is why they are listed rather than folded in. For chains without joins, filters or aggregation, a test evaluates the composed formula directly on the source table and checks it gives the final model's values row for row. `describe` shows a column's expression and a model's conditions too, and the `explain` MCP tool answers "how is this number calculated?" for an agent.

## Reviewing Changes

`konstruct diff` answers "what does this branch do to the data?" For each dbt model, SQL file and DAG file that changed, it analyses the base and current versions and compares each output column's canonical expression: the expression with every CTE, subquery and alias inlined down to physical columns. Reformatting, renaming a CTE or reordering columns is therefore not a change, and a real change is shown in full. Join, filter and grouping conditions are compared the same way. A removed column is breaking when a downstream model's current SQL still reads it, so a change that also updates its consumers is not flagged.

![konstruct diff on a branch of the example project](docs/demo-diff.gif)

```text
$ konstruct diff
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

dbt models are compared by rendering their Jinja without dbt (see How Lineage Works), so `diff` needs no warehouse and no credentials. `--json-file` writes the result as JSON beside any format, including the list of breaking changes, for scripts.

### On Pull Requests

Konstruct is also a GitHub Action. It reviews every pull request column by column, posts the review as a comment (updated in place on each push), writes it to the job summary, annotates each breaking change, and fails the check when a change removes a column that something downstream still reads:

```yaml
# .github/workflows/konstruct.yml
name: konstruct
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  lineage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: kodexlabsio/konstruct@v0
```

| Input | Default | Purpose |
|-------|---------|---------|
| `working-directory` | `.` | Where the dbt project, SQL or DAGs are |
| `base` | the pull request's base branch | Commit to compare with |
| `comment` | `true` | Post the review on the pull request |
| `fail-on-breaking` | `true` | Fail the check on a breaking change |
| `build-args` | `--no-warehouse --no-dbt` | Arguments for `konstruct build`. The default needs no credentials and never runs the pull request's dbt macros |
| `extras` | none | Konstruct extras, such as `snowflake` |

The outputs `breaking`, `summary`, `report` (markdown) and `json` let later steps act on the result. CI runs the action against a breaking change and a safe one on every commit. Pin a release (`@v0.7.0`) for a fixed version; `@v0` moves to each new 0.x release. [`docs/konstruct-pull-request.yml`](docs/konstruct-pull-request.yml) shows the same steps written out, for adding a `dbt parse` or other setup first.

## How Much to Trust the Lineage

Static column lineage is a claim about SQL that is rarely checked. `konstruct verify` checks it against the data, using perturbation (the idea behind perturbation-based provenance): for every dbt model and SQL-built table in a local DuckDB file or a Postgres database it copies the model's inputs into memory, perturbs one input column at a time, reruns the model's SQL and records which output columns change.

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

- **Rendering dbt without dbt:** on jaffle-shop, lineage from konstruct's own Jinja rendering (used when there is no compiled SQL, and by `diff`) is identical to lineage from dbt's compiled SQL: 113 of 113 column edges and 9 of 9 join and filter references.
- **Airflow parsing:** the static parser reports exactly the same DAGs, tasks and dependencies as Airflow's own DagBag on every fixture DAG, including TaskGroups, TaskFlow and every dependency syntax.
- **Dagster parsing:** the static parser gives the same asset keys, dependencies, dbt selection and job selections as Dagster's own asset graph for the fixture definitions, the same op handles and op-to-op dependencies as Dagster's job graph (nested graphs and aliases included), and the same sensor and schedule targets.

Each result is a test in the suite (`test_verify`, `test_realworld`, `test_airflow_parity`, `test_dagster`), so CI checks them on every change.

## Performance

On generated dbt projects (`.kodex/scripts/benchmark.py`): models in layers, each joining two earlier models through CTEs, 20 columns each, with a manifest, a catalog and Airflow DAGs. Measured on an Apple silicon laptop with Python 3.12. A cold build parses every model and writes every output, `graph.html` included with every column's composed formula. `--update` reuses the analysis of anything unchanged, and after an edit it recomputes only the formulas downstream of the change.

| Models | Columns | Column edges | Cold build | `--update`, no change | `--update`, one model edited | impact | trace | explain | graph.html |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 2,200 | 2,540 | 1.08 s | 0.1 s | 0.12 s | 0.2 ms | 0.2 ms | 1.5 ms | 1.4 MB |
| 500 | 10,200 | 12,940 | 6.14 s | 0.79 s | 0.63 s | 1.0 ms | 0.8 ms | 2.4 ms | 6.7 MB |
| 1,000 | 20,200 | 25,940 | 12.66 s | 1.67 s | 1.46 s | 2.1 ms | 1.6 ms | 3.8 ms | 13.4 MB |
| 2,000 | 40,200 | 51,940 | 26.44 s | 3.95 s | 3.1 s | 4.0 ms | 3.3 ms | 5.0 ms | 27.1 MB |

Time grows linearly with the size of the project. Query times are medians over five runs on the deepest model. In the browser, the 2,000-model `graph.html` opens in under half a second, and selecting a column isolates its lineage in about 0.2 seconds. Run `uv run .kodex/scripts/benchmark.py 300 3000` to measure other sizes on your machine.

## How Lineage Works

- Konstruct qualifies each statement against the columns it knows (from the dbt catalog, the warehouse, or the lineage of upstream models, processed in dependency order), so `SELECT *` expands. sqlglot then follows every output column through CTEs, subqueries, aliases, unions and window functions to physical columns.
- A path made only of column references and renames is `direct`; anything else (arithmetic, functions, aggregates, `CASE`, window functions) is `transformed`.
- Columns that change which rows a model returns, without being selected (join keys, filters, GROUP BY, HAVING, QUALIFY), are recorded as `feeds` edges with `role: reference`, traced through CTEs. Impact reports the relations that use them but does not then mark every downstream column, which would bury the answer.
- Nested data is followed: `o.customer.address.city`, `customer['country']`, `items[1].sku`, `unnest(items)` and `FROM t, UNNEST(t.items) AS i` with `i.sku` trace to the nested column (`orders.items.sku`), in outputs and in conditions, through CTEs; reading a field or unnesting a list counts as a direct pass-through. Struct columns, and the structs inside lists and map values, are listed field by field for every engine, named the way SQL reads them.
- Without compiled SQL, dbt models are rendered by Konstruct in a sandbox: loops, variables, `ref`, `source`, `config`, the project's and packages' macros (`dbt_utils.*`), `adapter.dispatch` and dbt's cross-database macros (`dbt.date_trunc`, `dbt.cast`, `dbt.concat`, `dbt.hash`, `dbt.date_spine` and others). An unknown macro becomes a function call that keeps its arguments, so the columns passed to it stay in the lineage.
- dbt models, warehouse tables with the same relation name, Airflow SQL targets and Iceberg tables merge into one node, so lineage crosses tools: a column computed in dbt, copied into a reporting table by an Airflow task, and used by a dashboard is one chain.

## Limitations

- Lineage is static. SQL built at runtime, Jinja that dbt has not compiled, Python models, stored procedures and UDF internals are not followed; each gap is listed under Warnings in `REPORT.md`.
- Without compiled SQL, macros that query the warehouse while compiling (`run_query`, introspective `dbt_utils.get_column_values`) render as empty results, so models that depend on them may differ from dbt's compiled SQL.
- `SELECT *` over a relation whose columns Konstruct cannot learn (no warehouse, no catalog) still gets lineage for the columns the query goes on to use, and is reported as a warning; columns passed straight through by the star to the output are not known.
- Dagster ops compose statically only: a graph body built in a loop is counted once (with a warning), ops created by factories are not seen, and sensors that choose a job at run time (`RunRequest(job_name=...)` without `job=`) are not linked. Op inputs and outputs are not tracked as data; lineage comes from the SQL an op runs.
- Airflow parsing covers the common ways of writing DAGs. Task ids or SQL built in loops or from runtime values, dynamic task mapping over computed lists, and DAG factories are skipped with a warning. Selections by `--selector`, `state:`, `result:` and `source_status:` are not resolved.
- Identifiers are matched case-insensitively. Two relations whose names differ only by case are treated as one.
- `query` ranks by words, not meaning. It finds `customer_lifetime_value` from "lifetime value" but not from "how much a customer is worth"; the agent is expected to follow up with `search` or `describe`.
- Map keys are read at run time, so `attrs['colour']` on a map traces to the `attrs` column, not to a key. Positional `UNNEST ... AS t(a, b)` over rows, without field names, traces to the list column.
- `verify` runs against a local DuckDB file or a Postgres database (in a transaction that is rolled back); other warehouses are not supported yet. It perturbs columns one at a time, so influences that need two inputs to change together are not observed.
- `diff` reads dbt YAML for tests, descriptions, sources and exposures, but not for other configuration (materialisation, contracts, meta).

### Not Yet (v1)

- Testing the Databricks connector against a real workspace. The tests are written (`tests/test_databricks_live.py`: metadata, struct fields, view lineage and system-table usage, in a scratch schema that is dropped afterwards) and a CI job runs them when the `KONSTRUCT_TEST_DATABRICKS_*` repository secrets are set; they have not yet run against a workspace.
- Column-level lineage for dbt Python models and through UDFs.
- `verify` against sampled remote warehouses (Snowflake, BigQuery, Databricks).
- Incremental warehouse metadata (it is re-read on every build; it is fast for local warehouses but not for large remote ones).

## CI/CD

Build and Test runs on every push to `main` and on pull requests: Ruff and mypy, a commit message check on pull requests, the test suite on Python 3.11, 3.12 and 3.13 against Postgres and Iceberg REST catalog service containers, an end-to-end build of the jaffle_shop example that fails if it takes a minute or more, an install of the built wheel with `uv tool install`, an install from GitHub exactly as this README describes, the real-world jaffle-shop test, and the Airflow parity test. The test job also runs the browser tests in Chromium and fails below 90% branch coverage. Create Release runs automatically after Build and Test passes on a push to `main`, and releases the commit that was tested. commitizen reads the Conventional Commits since the last tag (`fix` gives a patch release, `feat` a minor one, a breaking change the next major, or minor while the version is 0.x), updates `pyproject.toml`, `konstruct/__init__.py`, `uv.lock` and `CHANGELOG.md`, commits `chore(release): Release X.Y.Z`, tags `vX.Y.Z` and publishes a GitHub release with the changelog, wheel and sdist. A push with nothing releasable (only `docs`, `ci`, `test` or `chore` commits) makes no release. It can also be run by hand from the Actions tab to force a patch, minor or major release. Publishing to PyPI is a separate job that stays off until the `PUBLISH_TO_PYPI` repository variable is `true` and a `pypi` environment with trusted publishing exists.

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
- Node.js 24 (see `.nvmrc`) and [pnpm](https://pnpm.io/), only to work on the graph viewer
- [vhs](https://github.com/charmbracelet/vhs), only to re-record the demo GIFs

### Installation

1. **Clone the repository**
   ```bash
   git clone git@github.com:kodexlabsio/konstruct.git
   cd konstruct
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
| Integration | `test_warehouse`, `test_build`, `test_cloud_warehouses` | DuckDB, Postgres and Iceberg (SQL and REST catalogs) for real; Snowflake against fakesnow and BigQuery against its emulator; Databricks against a stub; builds of projects with and without dbt |
| End to end | `test_build`, `test_cli`, `test_query`, `test_mcp` | The example built with real dbt, every CLI command, the Python API, and the MCP server in process and over stdio |
| Golden files | `test_golden`, `tests/golden/` | The exact text of the CLI output and REPORT.md; regenerate with `KONSTRUCT_UPDATE_GOLDEN=1` and review the diff |
| Browser | `test_browser` | graph.html in Chromium: selection, column rows and edges, formulas, deep links, isolate, direction, filters, themes, search, hostile text, a 400-relation graph |
| Usage | `test_usage` | The SQL sent to Snowflake, BigQuery and Databricks, query text parsing for BigQuery, usage on columns, in the report and describe, the daily cache, off by default, and missing permissions as a warning, against stub drivers in each vendor's row shapes |
| Conflicts | `test_conflicts` | Two revenue definitions that differ by one filter (the pair, the filter and its file), different sources, ranking, grouping by business terms, copies left out, no false conflicts on the example |
| Definitions | `test_definitions` | The definitions.yml format and its errors, terms in the build, `define`, describe, search, query and impact, the glossary, warnings for missing columns and filters (including a renamed bound column in the example), and `diff` on definitions |
| Formulas | `test_explain` | `explain` on dbt and Airflow SQL, and composed formulas evaluated on the data against the final model, row for row; the viewer's formula cache |
| Export | `test_export` | OpenLineage events validated against the OpenLineage JSON schemas; Mermaid, DOT and CSV |
| Scale | `test_scale` | A generated 300-model project: full build and `--update` times |
| Real world | `test_realworld` | dbt Labs' jaffle-shop at a pinned commit (needs network; `KONSTRUCT_REALWORLD=1`) |
| Lineage against data | `test_verify` | `verify` confirms true lineage and catches removed edges, false edges and dropped filters; a differential property test runs generated queries on generated data and requires the static lineage to agree with execution |
| Change review | `test_diff` | `diff` on real git repositories: formatting and CTE renames are not changes, removed columns still read downstream are breaking, conditions, DAGs, two commits, all output formats, and dbt Jinja rendering |
| Dagster | `test_dagster` | The Dagster parser, assets and op jobs joining dbt and warehouse lineage, sensors ordering work, and parity with Dagster's own asset and job graphs (needs Dagster; see the CI job) |
| Databricks | `test_databricks_live` | The connector against a real workspace with Unity Catalog, compared with the stub results; runs only with workspace credentials (see the file) |
| Parity | `test_airflow_parity` | The static Airflow parser against Airflow's own DagBag (needs Airflow; see the file for the command) |

The dev dependencies include dbt-core and dbt-duckdb, so the dbt and end-to-end tests build a copy of the example project for real. In CI, `KONSTRUCT_REQUIRE_SERVICES=1` turns a missing service or browser into a failure rather than a skip, and `HYPOTHESIS_PROFILE=ci` runs more generated examples.

### The Graph Viewer

graph.html is a Svelte 5 and TypeScript app in `viewer/`, built with Vite into one self-contained file, `konstruct/viewer.html`, with its script, styles and fonts inlined (the font licences are in `licenses/`). That file is committed, so installing Konstruct needs no Node.js; Konstruct fills its two data placeholders when it writes graph.html.

```bash
cd viewer
pnpm install
mkdir -p dev && cp ../examples/jaffle_shop/konstruct-out/graph.json dev/   # data for the dev server
pnpm dev                     # http://localhost:5173/viewer.html, reloading as you edit
pnpm check                   # svelte-check (strict TypeScript)
pnpm test                    # Vitest: layout, focus, search and the graph model
pnpm build                   # writes ../konstruct/viewer.html; commit it with the source
```

Pre-commit hooks check formatting and types and rebuild the page when `viewer/` changes, and a CI job fails if the committed build does not match the source. The browser tests (`tests/test_browser.py`) drive the built page in Chromium and fail on any network request.

### Other Commands

- `uv run ruff check`, `uv run ruff format`, `uv run mypy`: lint, format and type check (strict)
- `uv run .kodex/scripts/prepare_example.py`: build the example warehouse
- `vhs .kodex/scripts/demo-cli.tape`: re-record `docs/demo-cli.gif`
- `.kodex/scripts/demo_diff_setup.sh && vhs .kodex/scripts/demo-diff.tape`: re-record `docs/demo-diff.gif`
- `vhs .kodex/scripts/demo-claude-code.tape`: record the same question through `/konstruct` in Claude Code (needs Claude Code signed in)

### Commits

Commits follow [Conventional Commits](https://www.conventionalcommits.org/), checked by commitizen and by `.kodex/scripts/check_commit_message.py`, which applies the Kodex Labs rules: a fixed list of scopes, sentence-case subjects, lines of at most 100 characters, and no AI attribution lines. The scopes are `airflow`, `dagster`, `dbt`, `definitions`, `iceberg`, `sql`, `warehouse`, `build`, `graph`, `html`, `lineage`, `query`, `report`, `cli`, `mcp`, `skill`, `examples`, `readme`, `tests`, `ci`, `config`, `deps`, `deps-dev` and `release`.

### Repository Layout

| Path | Contents |
|------|----------|
| `konstruct/graph.py` | Graph model, name resolution, impact, trace and path |
| `konstruct/sql.py` | sqlglot column lineage, canonical expressions and conditions |
| `konstruct/dbt.py`, `jinja.py`, `airflow.py`, `dagster.py`, `warehouse.py`, `definitions.py` | Inputs, the sandboxed dbt renderer and business definitions |
| `konstruct/config.py`, `build.py`, `usage.py` | Configuration, discovery, the build and its cache, and usage from query history |
| `konstruct/query.py`, `explain.py`, `conflicts.py`, `diff.py`, `verify.py` | Questions over the graph: impact and the rest, formulas, conflicting definitions, change review, lineage against data |
| `konstruct/report.py`, `html.py`, `export.py` | REPORT.md, writing graph.html, and the export formats |
| `konstruct/viewer.html` | The built graph viewer (generated by `pnpm build`; do not edit) |
| `viewer/` | The graph viewer's source: Svelte 5 and TypeScript, built with Vite |
| `konstruct/project.py` | The Python API |
| `konstruct/cli.py`, `console.py`, `status.py`, `mcp_server.py`, `skill.py` | Interfaces |
| `action.yml` | The GitHub Action |
| `examples/jaffle_shop` | dbt Labs' jaffle_shop_duckdb (Apache-2.0, see its NOTICE) with an Airflow DAG |
| `.kodex/scripts` | Project scripts: example preparation, test services, commit rules, benchmark, demo tapes |

## Contributing and Security

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to report a problem or propose a change, and [SECURITY.md](SECURITY.md) for how to report a vulnerability and what Konstruct executes, reads and writes (it never imports DAG code, renders dbt Jinja in a sandbox and collects no telemetry).

## Licence

MIT. See [LICENSE](LICENSE). The jaffle_shop example keeps its own Apache-2.0 licence.
