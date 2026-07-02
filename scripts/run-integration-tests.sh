#!/usr/bin/env bash
# Run the integration test suite against real MariaDB + Valkey containers.
# Usage: scripts/run-integration-tests.sh [extra unittest args]
set -euo pipefail

cd "$(dirname "$0")/.."

compose() {
  docker compose -f docker-compose.test.yml "$@"
}

cleanup() {
  compose down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

compose up -d --wait

export RUN_INTEGRATION_TESTS=1
export SECRET_KEY="${SECRET_KEY:-integration-test-secret}"
export SITE_URL="${SITE_URL:-http://localhost}"
export DB_HOST=127.0.0.1
export DB_PORT=3307
export DB_USER=firephenix
export DB_PASSWORD=test-password
export VALKEY_HOST=127.0.0.1
export VALKEY_PORT=6380
export LIMITER_STORAGE_URI=memory://
export ADMIN_STEAM_IDS="${ADMIN_STEAM_IDS:-76561198000000001}"
export TS3_HOST=teamspeak-disabled
export TS3_PASSWORD=disabled

uv run python -m unittest discover -s tests/integration -t . -v "$@"
