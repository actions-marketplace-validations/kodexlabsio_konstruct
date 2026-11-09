#!/usr/bin/env bash
# Start (or stop) the Postgres and Iceberg REST catalog containers the integration tests use,
# and print the environment variables that point the tests at them.
#
#   eval "$(.kodex/scripts/services.sh up)"   then   uv run pytest
#   .kodex/scripts/services.sh down
set -euo pipefail

PG_PORT="${KONSTRUCT_PG_PORT:-55432}"
BIGQUERY_PORT="${KONSTRUCT_BIGQUERY_PORT:-59050}"
ICEBERG_PORT="${KONSTRUCT_ICEBERG_PORT:-58181}"

case "${1:-up}" in
  up)
    docker rm -f konstruct-pg konstruct-iceberg konstruct-bigquery >/dev/null 2>&1 || true
    docker run -d --name konstruct-pg -p "${PG_PORT}:5432" \
      -e POSTGRES_USER=konstruct -e POSTGRES_PASSWORD=konstruct -e POSTGRES_DB=konstruct \
      postgres:17 >/dev/null
    docker run -d --name konstruct-iceberg -p "${ICEBERG_PORT}:8181" apache/iceberg-rest-fixture:latest >/dev/null
    docker run -d --name konstruct-bigquery -p "${BIGQUERY_PORT}:9050" ghcr.io/goccy/bigquery-emulator:latest --project=test >/dev/null
    for _ in $(seq 1 30); do
      if docker exec konstruct-pg pg_isready -U konstruct >/dev/null 2>&1 \
        && curl -fs "http://localhost:${ICEBERG_PORT}/v1/config" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    echo "export KONSTRUCT_TEST_PG_DSN=postgresql://konstruct:konstruct@localhost:${PG_PORT}/konstruct"
    echo "export KONSTRUCT_TEST_ICEBERG_URI=http://localhost:${ICEBERG_PORT}"
    echo "export KONSTRUCT_TEST_BIGQUERY_ENDPOINT=http://localhost:${BIGQUERY_PORT}"
    ;;
  down)
    docker rm -f konstruct-pg konstruct-iceberg konstruct-bigquery >/dev/null
    ;;
  *)
    echo "usage: $0 [up|down]" >&2
    exit 2
    ;;
esac
