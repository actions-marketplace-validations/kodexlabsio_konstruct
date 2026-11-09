# Column Usage from Query History

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
