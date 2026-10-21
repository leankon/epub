#!/usr/bin/env bash
# Shared helpers for the PDF Workstation scripts. Source this file; do not execute it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export REPO_ROOT

info()  { printf '\033[1;34m[pdfws]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[pdfws] WARNING:\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m[pdfws] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Create .env from .env.example with freshly generated local secrets (never overwrites).
ensure_env_file() {
  if [[ ! -f "$REPO_ROOT/.env" ]]; then
    info "Creating .env from .env.example with generated local database passwords"
    local owner_pw app_pw
    owner_pw="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
    app_pw="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
    sed -e "s/change-me-owner/${owner_pw}/g" -e "s/change-me-app/${app_pw}/g" \
      "$REPO_ROOT/.env.example" > "$REPO_ROOT/.env"
    chmod 600 "$REPO_ROOT/.env"
  fi
}

load_env() {
  ensure_env_file
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
  PDFWS_DATA_DIR="${PDFWS_DATA_DIR:-./data}"
  [[ "$PDFWS_DATA_DIR" = /* ]] || PDFWS_DATA_DIR="$REPO_ROOT/${PDFWS_DATA_DIR#./}"
  export PDFWS_DATA_DIR
  export PDFWS_PG_MODE="${PDFWS_PG_MODE:-native}"
  export PDFWS_PG_PORT="${PDFWS_PG_PORT:-54329}"
  export PDFWS_BACKEND_PORT="${PDFWS_BACKEND_PORT:-8765}"
  export PDFWS_FRONTEND_PORT="${PDFWS_FRONTEND_PORT:-3000}"
  export PDFWS_HOST="${PDFWS_HOST:-127.0.0.1}"
}

venv_python() {
  if [[ -x "$REPO_ROOT/backend/.venv/bin/python" ]]; then
    echo "$REPO_ROOT/backend/.venv/bin/python"
  else
    fail "Python virtual environment missing. Run ./scripts/setup.sh first."
  fi
}

require_cmd() {
  local cmd="$1" hint="$2"
  command -v "$cmd" >/dev/null 2>&1 || fail "Missing executable '$cmd'. $hint"
}

# Locate PostgreSQL server binaries (Debian keeps them outside PATH).
pg_bindir() {
  if command -v pg_ctl >/dev/null 2>&1; then dirname "$(command -v pg_ctl)"; return; fi
  local d
  for d in /usr/lib/postgresql/*/bin /opt/homebrew/opt/postgresql@*/bin /usr/local/opt/postgresql@*/bin \
           /Applications/Postgres.app/Contents/Versions/latest/bin; do
    [[ -x "$d/pg_ctl" ]] && { echo "$d"; return; }
  done
  return 1
}
