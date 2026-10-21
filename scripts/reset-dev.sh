#!/usr/bin/env bash
# Reset the development environment WITHOUT touching user data by default.
#   ./scripts/reset-dev.sh               remove build caches, temp files, render cache, venv/node_modules kept
#   ./scripts/reset-dev.sh --deps        also remove backend/.venv and frontend/node_modules
#   ./scripts/reset-dev.sh --wipe-data --i-understand-this-deletes-everything
#                                        delete ALL documents, database, backups (makes a final backup first)
source "$(dirname "$0")/lib/common.sh"; load_env
info "Removing caches and temporary files"
rm -rf "$REPO_ROOT/frontend/.next" "$PDFWS_DATA_DIR/tmp" "$PDFWS_DATA_DIR/cache"
find "$REPO_ROOT/backend" -name __pycache__ -type d -prune -exec rm -rf {} +
if [[ " $* " == *" --deps "* ]]; then
  info "Removing dependency folders"; rm -rf "$REPO_ROOT/backend/.venv" "$REPO_ROOT/frontend/node_modules"
fi
if [[ " $* " == *" --wipe-data "* ]]; then
  [[ " $* " == *" --i-understand-this-deletes-everything "* ]] || fail "Add --i-understand-this-deletes-everything to wipe data."
  if [[ -x "$REPO_ROOT/backend/.venv/bin/python" ]]; then
    "$REPO_ROOT/scripts/backup.sh" || fail "Final backup failed; refusing to wipe."
    dest="$HOME/pdfws-final-backup-$(date +%Y%m%d%H%M%S)"; cp -R "$PDFWS_DATA_DIR/backups" "$dest"
    info "Final backups copied to $dest"
  fi
  "$REPO_ROOT/scripts/db.sh" stop || true
  rm -rf "$PDFWS_DATA_DIR"
  info "All application data deleted. Run ./scripts/setup.sh to start fresh."
fi
info "Done"
