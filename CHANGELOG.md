## v0.1.1 (2026-11-06)

### Fix

- **ci**: Push the release tag by name

## v0.1.0 (2026-11-06)

### Feat

- **skill**: Tell agents about --refresh, status and list
- **cli**: Rework the command line and add a typed Python API
- **skill**: Install a /kerplunk skill for Claude Code and Cursor
- **mcp**: Add a stdio MCP server with impact, trace, search, describe and path
- **cli**: Add the kerplunk command line
- **query**: Add impact, trace, path, search, describe and query
- **html**: Add a single-file interactive graph viewer
- **build**: Build the graph, report and viewer from every input
- **warehouse**: Read DuckDB, Postgres and Iceberg catalog metadata
- **airflow**: Parse Airflow DAG files statically
- **dbt**: Load dbt projects from the manifest and catalog
- **examples**: Add the jaffle_shop example with an Airflow DAG
- **sql**: Add column-level lineage for SQL statements with sqlglot
- **graph**: Add the graph model with impact, trace and path traversal

### Fix

- **ci**: Install Chromium once and cache it, and time-limit every job
- **readme**: Format the Python example and check Markdown code blocks before commit
- **ci**: Pin setup-uv to v10.3.3 and use the publish action's release/v1 branch
- **airflow**: Link a TaskGroup at its first and last tasks, as Airflow does
- **html**: Keep the selected node in view, columns in table order and dbt source names
- **lineage**: Follow seeds into the sources over them and stop guessing star columns
- **query**: Shorten long descriptions in impact and trace headers
