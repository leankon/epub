# Troubleshooting

Start with the diagnostics: `cd backend && PYTHONPATH=. .venv/bin/python -m app.cli diagnostics` (also shown on
**Settings** and at the top of **Home**). Logs: `data/run/backend.log`, `data/run/frontend.log`,
`data/postgres/postgres.log`.

| Symptom | Cause / fix |
|---|---|
| `Missing executable 'tesseract'` / OCR features disabled | Install Tesseract and language data: Debian/Ubuntu `sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa tesseract-ocr-osd`; macOS `brew install tesseract tesseract-lang`; Windows: UB Mannheim installer, add to PATH or set `PDFWS_TESSERACT_CMD`. |
| `missing: spa` in diagnostics | Language pack missing; install `tesseract-ocr-spa` or copy `spa.traineddata` into the tessdata directory (`TESSDATA_PREFIX`). |
| Pages not auto-rotated | `osd.traineddata` missing (`tesseract-ocr-osd`). |
| Office conversion unavailable | Install LibreOffice (`libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress`), or set `PDFWS_SOFFICE_CMD` to `soffice`/`soffice.exe`. Close other LibreOffice instances if conversions hang. |
| "EPUBCheck was not executed" | Install Java 11+ and run `./scripts/install-epubcheck.sh`. Without it only internal checks run (and the report says so). |
| EPUB→PDF unavailable (`WeasyPrint (Pango)` missing) | Install Pango: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`; macOS `brew install pango`; Windows: GTK3 runtime. |
| `PostgreSQL server binaries not found` | Install PostgreSQL 15/16 server (`apt install postgresql`, `brew install postgresql@16`, EDB installer on Windows) or set `PDFWS_PG_MODE=docker`. |
| `PostgreSQL refuses to run as root` / permission denied on `data/postgres` | Run as a normal user. In containers running as root, the scripts use the `postgres` OS account; `data/` must stay traversable (mode 0711, set automatically). |
| HTTP 503 `database_unavailable` | Start PostgreSQL: `./scripts/db.sh start`. |
| HTTP 401 / 502 from `/api/backend` | Backend not running or restarted with a new token; restart with `./scripts/dev.sh`. |
| HTTP 421 `host_not_allowed` | You opened the app via a hostname other than `127.0.0.1`/`localhost`. Add it to `PDFWS_EXTRA_ALLOWED_HOSTS` (and only bind beyond loopback with `PDFWS_ALLOW_NETWORK=true`). |
| HTTP 429 | Queue or upload slots full; wait or raise `PDFWS_MAX_QUEUED_JOBS`. |
| HTTP 413 | File exceeds `PDFWS_MAX_UPLOAD_MB` / `PDFWS_MAX_PAGES`. |
| Job "interrupted" after a crash/restart | Open **Processing history** → Resume. Jobs needing passwords/terms ask for them again. OCR resumes from the last completed page. |
| OCR is slow | Lower DPI (200–250 for clean scans), keep `PDFWS_MAX_CONCURRENT_JOBS` ≤ CPU cores / 2. |
| Out of memory on huge pages | Lower `PDFWS_OCR_DPI`; raise `PDFWS_SUBPROCESS_MEMORY_MB`. |
| "Verification failed: redacted content is still extractable" | The document uses structures the redactor cannot rewrite precisely. Try marking larger areas, or rasterise the pages (PDF → images → images to PDF) before redacting. |
| Startup reports orphans quarantined | A crash happened between writing a file and registering it. Files are in `data/quarantine/orphans/`; review and delete them manually. |
| Backup verification fails | Files in the backup were altered or lost; do not restore from it. Create a new backup. |
| Next.js dev server port in use | Set `PDFWS_FRONTEND_PORT` (and restart). |
