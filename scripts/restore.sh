#!/usr/bin/env bash
# Restore a backup directory. Stop the application first. Usage:
#   ./scripts/restore.sh <backup-dir-or-name> --confirm RESTORE [--no-safety-backup]
# The backup is verified first; current files are moved to data/quarantine, not deleted.
source "$(dirname "$0")/lib/common.sh"; load_env
[[ $# -ge 1 ]] || fail "Usage: $0 <backup-dir> --confirm RESTORE"
if curl -fsS "http://127.0.0.1:$PDFWS_BACKEND_PORT/api/health" >/dev/null 2>&1; then
  fail "The backend is running. Stop ./scripts/dev.sh before restoring (or use Storage & backups in the UI)."
fi
"$REPO_ROOT/scripts/db.sh" start >/dev/null
"$REPO_ROOT/scripts/db.sh" migrate >/dev/null
cd "$REPO_ROOT/backend" && PYTHONPATH=. exec "$(venv_python)" -m app.cli restore "$@"
