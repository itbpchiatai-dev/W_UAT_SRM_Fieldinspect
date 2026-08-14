#!/usr/bin/env bash
# Build + start the production Dockerfiles locally and verify both
# /health endpoints respond. Tears the stack down whether or not the
# checks pass.
#
# Run from repo root:  ./scripts/smoke-prod.sh
# CI:                  same command, executed by .github/workflows/ci.yml
set -euo pipefail

COMPOSE_FILE="docker-compose.smoke.yml"

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "ERROR: $COMPOSE_FILE not found (run from repo root)" >&2
  exit 1
fi

cleanup() {
  echo
  echo "--- Tearing down ---"
  docker compose -f "$COMPOSE_FILE" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "--- Building production Dockerfiles ---"
docker compose -f "$COMPOSE_FILE" build

echo
echo "--- Starting stack (waiting for healthchecks) ---"
docker compose -f "$COMPOSE_FILE" up -d --wait

echo
echo "--- Pinging backend ---"
curl -fsS http://localhost:8000/health
echo

echo "--- Pinging frontend ---"
curl -fsS http://localhost:8080/health
echo

echo
echo "OK — production stack starts and responds."
