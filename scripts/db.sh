#!/usr/bin/env bash
# Manage the local PostgreSQL instance.
#   ./scripts/db.sh init|start|stop|status|migrate|psql
# PDFWS_PG_MODE=native (default): private cluster in data/postgres bound to 127.0.0.1.
# PDFWS_PG_MODE=docker: the `db` service from docker-compose.yml.
# PDFWS_PG_MODE=external: an already running server (DATABASE_URL / DATABASE_ADMIN_URL).
source "$(dirname "$0")/lib/common.sh"
load_env

PGROOT="$PDFWS_DATA_DIR/postgres"
PGDATA="$PGROOT/cluster"
PGRUN="$PGROOT/run"
PGLOG="$PGROOT/postgres.log"
DB_NAME="${PDFWS_DB_NAME:-pdfws}"
OWNER_ROLE="${PDFWS_DB_OWNER_ROLE:-pdfws_owner}"
APP_ROLE="${PDFWS_DB_APP_ROLE:-pdfws_app}"

# PostgreSQL refuses to run as root; when root (e.g. containers) use the 'postgres' OS account.
as_pg() {
  if [[ "$(id -u)" == "0" ]]; then
    local user="${PDFWS_PG_OS_USER:-postgres}"
    id "$user" >/dev/null 2>&1 || fail "Running as root requires an OS user '$user' for PostgreSQL."
    runuser -u "$user" -- "$@"
  else
    "$@"
  fi
}

native_bins() {
  BIN="$(pg_bindir)" || fail "PostgreSQL server binaries not found. Install PostgreSQL 15+ (apt install postgresql, brew install postgresql@16) or set PDFWS_PG_MODE=docker."
}

native_running() { [[ -f "$PGDATA/postmaster.pid" ]] && as_pg "$BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; }

native_init() {
  native_bins
  if [[ -f "$PGDATA/PG_VERSION" ]]; then info "PostgreSQL cluster already initialised at $PGDATA"; return; fi
  info "Initialising private PostgreSQL cluster in $PGDATA"
  mkdir -p "$PGDATA" "$PGRUN"
  chmod 700 "$PGROOT" "$PGDATA"
  [[ "$(id -u)" == "0" ]] && chown -R "${PDFWS_PG_OS_USER:-postgres}" "$PGROOT"
  local pwfile; pwfile="$PGROOT/.pwfile"
  printf '%s\n' "$POSTGRES_OWNER_PASSWORD" > "$pwfile"; chmod 600 "$pwfile"
  [[ "$(id -u)" == "0" ]] && chown "${PDFWS_PG_OS_USER:-postgres}" "$pwfile"
  as_pg "$BIN/initdb" -D "$PGDATA" --username="$OWNER_ROLE" --pwfile="$pwfile" \
    --auth-local=scram-sha-256 --auth-host=scram-sha-256 -E UTF8 --locale=C.UTF-8 >/dev/null
  rm -f "$pwfile"
  cat >> "$PGDATA/postgresql.conf" <<CONF
# --- pdfws: local-only server ---
listen_addresses = '127.0.0.1'
port = $PDFWS_PG_PORT
unix_socket_directories = '$PGRUN'
CONF
}

native_start() {
  native_bins
  [[ -f "$PGDATA/PG_VERSION" ]] || native_init
  if native_running; then info "PostgreSQL already running on 127.0.0.1:$PDFWS_PG_PORT"; else
    info "Starting PostgreSQL on 127.0.0.1:$PDFWS_PG_PORT"
    as_pg "$BIN/pg_ctl" -D "$PGDATA" -l "$PGLOG" -w -t 60 start >/dev/null || { tail -20 "$PGLOG"; fail "PostgreSQL failed to start"; }
  fi
  ensure_database
}

ensure_database() {
  local psql_bin="${BIN:-$(dirname "$(command -v psql)")}/psql"
  export PGPASSWORD="$POSTGRES_OWNER_PASSWORD"
  local conn=(-h 127.0.0.1 -p "$PDFWS_PG_PORT" -U "$OWNER_ROLE" -v ON_ERROR_STOP=1 -q)
  if ! "$psql_bin" "${conn[@]}" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
    info "Creating database $DB_NAME"
    "$psql_bin" "${conn[@]}" -d postgres -c "CREATE DATABASE $DB_NAME OWNER $OWNER_ROLE ENCODING 'UTF8'"
  fi
  "$psql_bin" "${conn[@]}" -d postgres -v app_role="$APP_ROLE" -v app_pw="$POSTGRES_APP_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE', :'app_role', :'app_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'app_role', :'app_pw') \gexec
SQL
  unset PGPASSWORD
}

native_stop() {
  native_bins
  if native_running; then info "Stopping PostgreSQL"; as_pg "$BIN/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null; else info "PostgreSQL not running"; fi
}

docker_start() {
  require_cmd docker "Install Docker Desktop / Docker Engine, or use PDFWS_PG_MODE=native."
  info "Starting PostgreSQL container (docker compose service 'db')"
  (cd "$REPO_ROOT" && docker compose up -d --wait db)
}

migrate() {
  local py; py="$(venv_python)"
  info "Applying database migrations"
  (cd "$REPO_ROOT/backend" && "$py" -m alembic upgrade head)
}

cmd="${1:-status}"
case "$PDFWS_PG_MODE:$cmd" in
  native:init) native_init ;;
  native:start) native_start ;;
  native:stop) native_stop ;;
  native:status) native_bins; native_running && info "running (native) on 127.0.0.1:$PDFWS_PG_PORT" || info "stopped" ;;
  docker:init|docker:start) docker_start ;;
  docker:stop) (cd "$REPO_ROOT" && docker compose stop db) ;;
  docker:status) (cd "$REPO_ROOT" && docker compose ps db) ;;
  external:init|external:start|external:stop|external:status) info "External PostgreSQL: managed outside this project" ;;
  *:migrate) migrate ;;
  *:psql) export PGPASSWORD="$POSTGRES_OWNER_PASSWORD"; exec psql -h 127.0.0.1 -p "$PDFWS_PG_PORT" -U "$OWNER_ROLE" "$DB_NAME" ;;
  *) fail "Unknown command '$cmd' (mode $PDFWS_PG_MODE). Use init|start|stop|status|migrate|psql" ;;
esac
