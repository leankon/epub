#!/usr/bin/env bash
# Start the complete local application: checks prerequisites, starts PostgreSQL, applies
# migrations, validates configuration, starts backend + frontend and prints the local URL.
#   ./scripts/dev.sh               (development mode, Next.js dev server)
#   PDFWS_FRONTEND_MODE=prod ./scripts/dev.sh   (production build + next start)
source "$(dirname "$0")/lib/common.sh"
load_env
export NEXT_TELEMETRY_DISABLED=1   # no Next.js telemetry, ever

PY="$(venv_python)"
[[ -d "$REPO_ROOT/frontend/node_modules" ]] || fail "Frontend dependencies missing. Run ./scripts/setup.sh first."
require_cmd node "Install Node.js 20/22 LTS."

info "Validating configuration"
(cd "$REPO_ROOT/backend" && PYTHONPATH=. "$PY" -c 'from app.core.config import get_settings; s=get_settings(); s.ensure_dirs(); print(f"  bind {s.host}:{s.backend_port}  data {s.data_dir}")') \
  || fail "Invalid configuration in .env (see message above)."

"$REPO_ROOT/scripts/db.sh" start
"$REPO_ROOT/scripts/db.sh" migrate

info "Checking native tools"
(cd "$REPO_ROOT/backend" && PYTHONPATH=. "$PY" -m app.cli diagnostics) || \
  warn "Missing tools are listed above with install instructions; their features are disabled until installed."

# Fresh session token for this run (read by the frontend proxy; never sent to the browser).
rm -f "$PDFWS_DATA_DIR/run/session-token"
LOGDIR="$PDFWS_DATA_DIR/run"; mkdir -p "$LOGDIR"
pids=()
cleanup() { info "Stopping"; for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

info "Starting backend on http://$PDFWS_HOST:$PDFWS_BACKEND_PORT"
(cd "$REPO_ROOT/backend" && PYTHONPATH=. exec "$PY" -m app.cli serve) > "$LOGDIR/backend.log" 2>&1 &
pids+=($!)
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$PDFWS_BACKEND_PORT/api/health" >/dev/null 2>&1 && break
  kill -0 "${pids[0]}" 2>/dev/null || { tail -30 "$LOGDIR/backend.log"; fail "Backend failed to start (log: $LOGDIR/backend.log)"; }
  sleep 0.5
done

cd "$REPO_ROOT/frontend"
if [[ "${PDFWS_FRONTEND_MODE:-dev}" == "prod" ]]; then
  info "Building frontend (production)"
  npx next build > "$LOGDIR/frontend-build.log" 2>&1 || { tail -30 "$LOGDIR/frontend-build.log"; fail "Frontend build failed"; }
  npx next start -H 127.0.0.1 -p "$PDFWS_FRONTEND_PORT" > "$LOGDIR/frontend.log" 2>&1 &
else
  npx next dev -H 127.0.0.1 -p "$PDFWS_FRONTEND_PORT" > "$LOGDIR/frontend.log" 2>&1 &
fi
pids+=($!)
for _ in $(seq 1 120); do
  curl -fsS -o /dev/null "http://127.0.0.1:$PDFWS_FRONTEND_PORT/" 2>/dev/null && break
  kill -0 "${pids[1]}" 2>/dev/null || { tail -30 "$LOGDIR/frontend.log"; fail "Frontend failed to start"; }
  sleep 1
done
info "PDF Workstation is running:  http://127.0.0.1:$PDFWS_FRONTEND_PORT"
info "Logs: $LOGDIR/backend.log, $LOGDIR/frontend.log  —  press Ctrl+C to stop"
wait -n "${pids[@]}"
