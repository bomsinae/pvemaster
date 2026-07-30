#!/usr/bin/env sh
set -eu

project_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

run_backend() {
  cd "$project_root/backend"
  .venv/bin/ruff format --check .
  .venv/bin/ruff check .
  .venv/bin/mypy app
  .venv/bin/pytest
  .venv/bin/alembic check
}

run_frontend() {
  cd "$project_root/frontend"
  npm run lint
  npm run typecheck
  npm run test:mock
  npm run build
  npm run test:browser
}

cd "$project_root"
./scripts/scan-secrets.sh
run_backend
run_frontend

if [ "${VERIFY_COMPOSE_CONFIG:-0}" = "1" ]; then
  docker compose config --quiet
fi

echo "Local release gates passed. Staging drills and operational approval remain separate gates."
