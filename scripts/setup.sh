#!/usr/bin/env bash
# One-time setup of a development environment: Python venv, Node packages, .env, database, migrations.
#   ./scripts/setup.sh            (add --with-epubcheck to download EPUBCheck as well)
source "$(dirname "$0")/lib/common.sh"

info "Checking prerequisites"
PY="${PDFWS_PYTHON:-python3.12}"
command -v "$PY" >/dev/null 2>&1 || fail "Python 3.12 not found (set PDFWS_PYTHON). Install: apt install python3.12 python3.12-venv / brew install python@3.12 / python.org"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)' || fail "$PY is not Python 3.12"
require_cmd node "Install Node.js 20 LTS or 22 LTS (https://nodejs.org)."
node -e 'process.exit(+process.versions.node.split(".")[0] >= 18.18 ? 0 : 1)' || fail "Node.js >= 18.18 required for Next.js 15 (20/22 LTS recommended)."
require_cmd npm "Install npm (bundled with Node.js)."

ensure_env_file

info "Creating Python virtual environment (backend/.venv)"
[[ -d "$REPO_ROOT/backend/.venv" ]] || "$PY" -m venv "$REPO_ROOT/backend/.venv"
"$REPO_ROOT/backend/.venv/bin/pip" install -q --upgrade pip
"$REPO_ROOT/backend/.venv/bin/pip" install -q -r "$REPO_ROOT/backend/requirements.txt"

info "Installing frontend dependencies (npm ci)"
(cd "$REPO_ROOT/frontend" && NEXT_TELEMETRY_DISABLED=1 npm ci --no-audit --no-fund)

"$REPO_ROOT/scripts/db.sh" start
"$REPO_ROOT/scripts/db.sh" migrate

if [[ "${1:-}" == "--with-epubcheck" ]]; then "$REPO_ROOT/scripts/install-epubcheck.sh"; fi

info "Native tool diagnostics:"
(cd "$REPO_ROOT/backend" && PYTHONPATH=. .venv/bin/python -m app.cli diagnostics) || \
  warn "Some native tools are missing; the features listed above stay unavailable until installed."
info "Setup complete. Start the app with ./scripts/dev.sh"
