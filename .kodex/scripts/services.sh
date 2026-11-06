#!/usr/bin/env bash
# Start (or stop) the Postgres and Iceberg REST catalog containers the integration tests use,
# and print the environment variables that point the tests at them.
#
#   eval "$(.kodex/scripts/services.sh up)"   then   uv run pytest
#   .kodex/scripts/services.sh down
set -euo pipefail

PG_PORT="${KERPLUNK_PG_PORT:-55432}"
ICEBERG_PORT="${KERPLUNK_ICEBERG_PORT:-58181}"

case "${1:-up}" in
  up)
    docker rm -f kerplunk-pg kerplunk-iceberg >/dev/null 2>&1 || true
    docker run -d --name kerplunk-pg -p "${PG_PORT}:5432" \
      -e POSTGRES_USER=kerplunk -e POSTGRES_PASSWORD=kerplunk -e POSTGRES_DB=kerplunk \
      postgres:17 >/dev/null
    docker run -d --name kerplunk-iceberg -p "${ICEBERG_PORT}:8181" apache/iceberg-rest-fixture:latest >/dev/null
    for _ in $(seq 1 30); do
      if docker exec kerplunk-pg pg_isready -U kerplunk >/dev/null 2>&1 \
        && curl -fs "http://localhost:${ICEBERG_PORT}/v1/config" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    echo "export KERPLUNK_TEST_PG_DSN=postgresql://kerplunk:kerplunk@localhost:${PG_PORT}/kerplunk"
    echo "export KERPLUNK_TEST_ICEBERG_URI=http://localhost:${ICEBERG_PORT}"
    ;;
  down)
    docker rm -f kerplunk-pg kerplunk-iceberg >/dev/null
    ;;
  *)
    echo "usage: $0 [up|down]" >&2
    exit 2
    ;;
esac
