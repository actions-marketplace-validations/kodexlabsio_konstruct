# kerplunk report

Built <time> from `<root>` by kerplunk <version> in <seconds>s. SQL dialect: duckdb.

## Summary

| Input | Count |
|-------|-------|
| dbt projects | 1 |
| warehouses | 1 |
| dbt models | 5 |
| airflow dags | 1 |
| dagster assets | 0 |
| dagster ops | 0 |
| sql files | 0 |

| Node kind | Count |
|-----------|-------|
| column | 44 |
| dag | 1 |
| exposure | 1 |
| model | 5 |
| seed | 3 |
| table | 1 |
| task | 6 |
| test | 20 |

| Edge kind | Count |
|-----------|-------|
| contains | 44 |
| derived_from | 36 |
| feeds | 21 |
| precedes | 5 |
| runs | 34 |
| tests | 21 |

Column lineage covers 32 of 32 columns of models and SQL-built tables (100%).

## Most used columns

Columns with the most direct dependants (derived columns and joins).

| Column | Dependants |
|--------|------------|
| `stg_payments.amount` | 6 |
| `stg_orders.order_id` | 4 |
| `stg_payments.payment_method` | 4 |
| `stg_orders.order_date` | 3 |
| `customers.customer_lifetime_value` | 2 |
| `customers.number_of_orders` | 2 |
| `stg_customers.customer_id` | 2 |
| `stg_orders.customer_id` | 2 |
| `stg_payments.order_id` | 2 |
| `customers.customer_id` | 1 |

## Orphans

Relations with nothing upstream or downstream, and no task that runs them.

None.

## Untested models

dbt models and snapshots with no test on the model or any of its columns.

None.

## Columns with no lineage

Columns of models and SQL-built tables with no upstream column: constants, columns kerplunk could not resolve, or SQL it could not parse (see Warnings).

None.

## Documented columns missing from the warehouse

Columns described in dbt YAML that the warehouse does not have.

- `customers.total_order_amount`

## Warnings

None.
