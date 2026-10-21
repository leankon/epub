#!/usr/bin/env bash
# Create a verified backup (database metadata + document files + SHA-256 manifest) in data/backups.
source "$(dirname "$0")/lib/common.sh"; load_env
"$REPO_ROOT/scripts/db.sh" start >/dev/null
cd "$REPO_ROOT/backend" && PYTHONPATH=. exec "$(venv_python)" -m app.cli backup
