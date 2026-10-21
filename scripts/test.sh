#!/usr/bin/env bash
# Run all checks: backend lint + tests (unit, integration, e2e), frontend typecheck, lint, tests, build.
#   ./scripts/test.sh [--backend-only|--frontend-only|--e2e]
source "$(dirname "$0")/lib/common.sh"; load_env
export NEXT_TELEMETRY_DISABLED=1
mode="${1:-all}"
if [[ "$mode" != "--frontend-only" ]]; then
  "$REPO_ROOT/scripts/db.sh" start >/dev/null
  cd "$REPO_ROOT/backend"
  info "Backend lint (ruff)"; .venv/bin/ruff check app tests
  if [[ "$mode" == "--e2e" ]]; then
    info "End-to-end test"; .venv/bin/python -m pytest -rs -m e2e
  else
    info "Backend tests (pytest)"; .venv/bin/python -m pytest -rs
  fi
fi
if [[ "$mode" == "all" || "$mode" == "--frontend-only" ]]; then
  cd "$REPO_ROOT/frontend"
  info "Frontend typecheck"; npx tsc --noEmit
  info "Frontend lint"; npx eslint .
  info "Frontend tests (vitest)"; npx vitest run
  info "Frontend build"; npx next build
fi
info "All checks passed"
