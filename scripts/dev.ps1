# PDF Workstation - Windows startup (PowerShell 5.1+ / 7+).
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1            # start everything
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Setup     # first-time setup, then start
param([switch]$Setup)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$env:NEXT_TELEMETRY_DISABLED = "1"

function Info($m) { Write-Host "[pdfws] $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "[pdfws] ERROR: $m" -ForegroundColor Red; exit 1 }
function Need($cmd, $hint) { if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Fail "Missing '$cmd'. $hint" } }

# --- .env (generated with random local passwords on first run) ---
$EnvFile = Join-Path $Repo ".env"
if (-not (Test-Path $EnvFile)) {
  Info "Creating .env with generated local database passwords"
  $rnd = { -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ }) }
  (Get-Content (Join-Path $Repo ".env.example")) -replace "change-me-owner", (& $rnd) -replace "change-me-app", (& $rnd) |
    Set-Content -Encoding utf8 $EnvFile
}
Get-Content $EnvFile | Where-Object { $_ -match "^\s*[A-Z_]+=" } | ForEach-Object {
  $k, $v = $_ -split "=", 2; [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), "Process")
}
$DataDir = if ($env:PDFWS_DATA_DIR -and [IO.Path]::IsPathRooted($env:PDFWS_DATA_DIR)) { $env:PDFWS_DATA_DIR } else { Join-Path $Repo "data" }
$PgPort = if ($env:PDFWS_PG_PORT) { $env:PDFWS_PG_PORT } else { "54329" }
$FePort = if ($env:PDFWS_FRONTEND_PORT) { $env:PDFWS_FRONTEND_PORT } else { "3000" }
$BePort = if ($env:PDFWS_BACKEND_PORT) { $env:PDFWS_BACKEND_PORT } else { "8765" }
$Py = Join-Path $Repo "backend\.venv\Scripts\python.exe"

if ($Setup) {
  Need "py" "Install Python 3.12 from python.org (with the py launcher)."
  Need "node" "Install Node.js 20/22 LTS."
  if (-not (Test-Path $Py)) { py -3.12 -m venv (Join-Path $Repo "backend\.venv") }
  & $Py -m pip install -q --upgrade pip
  & $Py -m pip install -q -r (Join-Path $Repo "backend\requirements.txt")
  Push-Location (Join-Path $Repo "frontend"); npm ci --no-audit --no-fund; Pop-Location
}
if (-not (Test-Path $Py)) { Fail "Python environment missing. Run: scripts\dev.ps1 -Setup" }
Need "node" "Install Node.js 20/22 LTS."

# --- PostgreSQL ---
$Mode = if ($env:PDFWS_PG_MODE) { $env:PDFWS_PG_MODE } else { "native" }
if ($Mode -eq "docker") {
  Need "docker" "Install Docker Desktop or use PDFWS_PG_MODE=native."
  Push-Location $Repo; docker compose up -d --wait db; Pop-Location
} elseif ($Mode -eq "native") {
  Need "pg_ctl" "Install PostgreSQL 16 (EDB installer) and add its bin folder to PATH, or set PDFWS_PG_MODE=docker."
  $PgData = Join-Path $DataDir "postgres\cluster"
  if (-not (Test-Path (Join-Path $PgData "PG_VERSION"))) {
    Info "Initialising private PostgreSQL cluster"
    New-Item -ItemType Directory -Force $PgData | Out-Null
    $pw = Join-Path $DataDir "postgres\pw.txt"; Set-Content -NoNewline $pw $env:POSTGRES_OWNER_PASSWORD
    initdb -D $PgData --username=pdfws_owner --pwfile=$pw --auth=scram-sha-256 -E UTF8 | Out-Null
    Remove-Item $pw
    Add-Content (Join-Path $PgData "postgresql.conf") "`nlisten_addresses = '127.0.0.1'`nport = $PgPort`n"
  }
  pg_ctl -D $PgData status *> $null
  if ($LASTEXITCODE -ne 0) { pg_ctl -D $PgData -l (Join-Path $DataDir "postgres\postgres.log") -w start | Out-Null }
  $env:PGPASSWORD = $env:POSTGRES_OWNER_PASSWORD
  $exists = psql -h 127.0.0.1 -p $PgPort -U pdfws_owner -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='pdfws'"
  if ($exists -ne "1") { psql -h 127.0.0.1 -p $PgPort -U pdfws_owner -d postgres -c "CREATE DATABASE pdfws OWNER pdfws_owner" | Out-Null }
  psql -h 127.0.0.1 -p $PgPort -U pdfws_owner -d postgres -v ON_ERROR_STOP=1 -c "DO `$`$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='pdfws_app') THEN CREATE ROLE pdfws_app LOGIN PASSWORD '$($env:POSTGRES_APP_PASSWORD)'; END IF; END`$`$;" | Out-Null
  Remove-Item Env:PGPASSWORD
}

Info "Applying migrations"
Push-Location (Join-Path $Repo "backend"); $env:PYTHONPATH = "."
& $Py -m alembic upgrade head; if ($LASTEXITCODE -ne 0) { Fail "Migration failed" }
& $Py -m app.cli diagnostics; if ($LASTEXITCODE -ne 0) { Write-Host "[pdfws] Some tools are missing (see above); related features are unavailable." -ForegroundColor Yellow }
Remove-Item -ErrorAction SilentlyContinue (Join-Path $DataDir "run\session-token")
$Logs = Join-Path $DataDir "run"; New-Item -ItemType Directory -Force $Logs | Out-Null
$be = Start-Process -PassThru -NoNewWindow -FilePath $Py -ArgumentList "-m", "app.cli", "serve" `
  -RedirectStandardOutput (Join-Path $Logs "backend.log") -RedirectStandardError (Join-Path $Logs "backend.err.log")
Pop-Location
Start-Sleep -Seconds 3
Push-Location (Join-Path $Repo "frontend")
$fe = Start-Process -PassThru -NoNewWindow -FilePath "npx.cmd" -ArgumentList "next", "dev", "-H", "127.0.0.1", "-p", $FePort `
  -RedirectStandardOutput (Join-Path $Logs "frontend.log") -RedirectStandardError (Join-Path $Logs "frontend.err.log")
Pop-Location
Info "PDF Workstation is starting: http://127.0.0.1:$FePort  (backend on 127.0.0.1:$BePort). Press Ctrl+C to stop."
try { Wait-Process -Id $be.Id, $fe.Id } finally { Stop-Process -Id $be.Id, $fe.Id -ErrorAction SilentlyContinue }
