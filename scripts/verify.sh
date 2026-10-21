#!/usr/bin/env bash
# Integrity checks: stored file hashes, audit-log hash chain, and (optionally) a backup.
#   ./scripts/verify.sh [backup-dir]
source "$(dirname "$0")/lib/common.sh"; load_env
"$REPO_ROOT/scripts/db.sh" start >/dev/null
cd "$REPO_ROOT/backend"; PY="$(venv_python)"; rc=0
info "Verifying stored file hashes";  PYTHONPATH=. "$PY" -m app.cli verify-hashes || rc=1
info "Verifying audit-log hash chain"; PYTHONPATH=. "$PY" -m app.cli verify-audit || rc=1
if [[ $# -ge 1 ]]; then info "Verifying backup $1"; PYTHONPATH=. "$PY" -m app.cli verify-backup "$1" || rc=1; fi
exit $rc
