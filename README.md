# PDF Workstation — a private, local-first PDF, OCR and EPUB workstation

A personal replacement for online PDF suites that runs **entirely on your computer**. Merge, split, organize,
compress, watermark, number, redact (permanently), protect/unlock, edit metadata and bookmarks, extract images,
convert Office files, and convert **scanned or photographed books into EPUB 3 files with real, selectable text
and their illustrations preserved**. Every operation creates a new version with a SHA-256 hash, and every step is
recorded in an append-only audit log.

> Files are processed locally. Nothing is uploaded, and no account, subscription, cloud API or telemetry is used.
> Local processing does **not** protect against malware, a compromised computer, other users of your account,
> or an unencrypted disk (see [docs/security-privacy.md](docs/security-privacy.md)). The app does not claim to be
> absolutely secure, and it does not claim perfect document fidelity.

## Contents

[Features](#features-and-status) · [Platforms](#supported-platforms) · [Architecture](#architecture) ·
[Dependencies](#dependencies) · [Installation](#installation) · [Configuration](#configuration-env) ·
[Running](#running) · [OCR languages](#ocr-language-data) · [LibreOffice](#libreoffice) ·
[EPUB validation](#epub-validation-epubcheck) · [Storage](#storage-locations-and-file-permissions) ·
[Privacy & security](#privacy-and-security-model) · [Backup & restore](#backup-and-restore) ·
[Export & deletion](#data-export-and-deletion) · [Tests](#tests-and-quality-checks) · [Commands](#command-reference) ·
[Troubleshooting](#troubleshooting) · [Limitations](#known-limitations) · [Updating](#updating-dependencies) ·
[Resetting](#resetting-the-development-environment) · [Verification log](#verification-log-commands-actually-executed)

## Features and status

Full table with notes: [docs/operations.md](docs/operations.md).

**Implemented end to end**
* Import with content-based type detection: PDF, EPUB, Office (DOCX/ODT/DOC/RTF, XLSX/ODS/XLS, PPTX/ODP/PPT) and
  images. Encryption, signatures, forms, JavaScript and incremental revisions are detected and warned about.
* PDF tools: merge (bookmarks kept), split (ranges, extraction, every page, bookmarks), drag-and-drop page
  organizer (reorder, rotate 90/180/270, duplicate, delete), compression presets with real size reports,
  text/image watermarks and page numbers with live preview, metadata, bookmarks editor, AES-256
  protection/unlock (owner password required to lift restrictions), image extraction (original encoding),
  PDF→PNG/JPEG, images→PDF, inspection and integrity report.
* **Permanent redaction**: glyph-level removal from content streams, image pixels, vector paths, form XObjects
  and annotations, plus sanitising of metadata, attachments, JavaScript, tags and history. The output is verified
  with two independent extractors and the job fails if anything is still extractable.
* **OCR for scanned and photographed pages** (Tesseract spa+eng + OpenCV): per-page classification so good text
  layers are not re-OCR'd; orientation, deskew, perspective correction, contrast, denoising, adaptive
  thresholding and resolution normalisation; layout analysis (headings, paragraphs, captions, footnotes,
  headers/footers/page numbers, marginal notes, lists, figures, tables, formulas); and column-aware reading order.
* **OCR review workspace**: page image with region overlays, zoom, original vs processed view, confidence badges,
  "next low-confidence region", text/role editing, exclusion, reprocessing with other settings, saved review state,
  book metadata and chapter boundaries, and approval. Corrections never touch the PDF.
* **Searchable PDF**: an invisible OCR text layer over the original page images.
* **PDF → EPUB 3** in three modes: *intelligent hybrid* (default), *reflowable* and *fixed layout*. You get real
  XHTML text, illustrations as separate full-resolution colour assets with captions, chapters, a navigation TOC,
  footnotes, visual fallbacks only where needed (all listed), a conversion report, and **mandatory validation**
  (internal checks + EPUBCheck when installed). Invalid EPUBs are never registered or offered for download.
* EPUB → PDF (WeasyPrint, network-sandboxed) and Office → PDF (LibreOffice headless, macros disabled).
* Batch processing, version history with comparison, downloads with delivery auditing, jobs that survive
  restarts (resume, cancel, OCR checkpoints), append-only hash-chained audit log with JSON/CSV export, storage
  usage, conservative expiration policies, verified backups, restore, full export and deletion.

**Not implemented / excluded**: table reconstruction as HTML tables and MathML (both kept as images), digital
signing and cryptographic signature validation, the optional personal visual-signature workflow,
qualified/regulated e-signatures, identity verification, Acrobat-style text editing, and enterprise governance.

## Supported platforms

| Platform | Status |
|---|---|
| Linux (Ubuntu 22.04/24.04, Debian 12) | Primary; fully tested (the verification log below was run on Ubuntu 24.04) |
| macOS 13+ (Intel/Apple Silicon) | Supported with Homebrew packages; `./scripts/dev.sh` |
| Windows 10/11 | Supported with `scripts\dev.ps1` (native PostgreSQL or Docker). Subprocess memory limits (`RLIMIT_AS`) are POSIX-only; timeouts still apply. WSL2 + the Linux instructions is the most tested route |

## Architecture

```
Browser ─▶ Next.js 15 (127.0.0.1:3000) ─▶ /api/backend proxy (adds secret token) ─▶ FastAPI (127.0.0.1:8765)
                                                                                     ├─ PostgreSQL (metadata, jobs, audit)
                                                                                     ├─ data/ (immutable files)
                                                                                     └─ Tesseract · OpenCV · PDFium · pikepdf
                                                                                        EPUBCheck · WeasyPrint · LibreOffice
```

Repository layout:

```
frontend/   Next.js 15 app (app/ pages, components/, lib/ API client & pure helpers, tests/ vitest)
backend/    FastAPI app (app/api routers, app/services engines, app/db models), migrations/ (Alembic), tests/ (pytest)
scripts/    setup.sh, dev.sh, dev.ps1, db.sh, backup.sh, restore.sh, verify.sh, test.sh, reset-dev.sh, install-epubcheck.sh
docs/       architecture.md, security-privacy.md, operations.md, troubleshooting.md
data/       created at runtime (documents, versions, OCR workspaces, backups, database cluster) — git-ignored
docker-compose.yml   optional PostgreSQL service
```

Details: [docs/architecture.md](docs/architecture.md). It covers the data model, job system, OCR pipeline, the
OCR/layout options evaluated (PaddleOCR and others) and why they were not adopted, and the EPUB construction.

## Dependencies

Mandatory core: **Next.js 15**, **TypeScript (strict)**, **Python 3.12**, **FastAPI**, **pikepdf**,
**LibreOffice headless**, **PostgreSQL**.

| Area | Packages (pinned in `backend/requirements.txt` / `frontend/package.json`) |
|---|---|
| API | fastapi, uvicorn, pydantic-settings, SQLAlchemy 2, psycopg 3, Alembic |
| PDF | pikepdf (qpdf), pypdfium2 (PDFium), pdfminer.six, reportlab (overlays), img2pdf |
| OCR & vision | Tesseract 5 executable (+ `spa`, `eng`, `osd` data), opencv-python-headless, numpy, Pillow |
| EPUB | EbookLib, lxml, EPUBCheck 5.1.0 (Java, optional but recommended) |
| EPUB → PDF | WeasyPrint (needs Pango) |
| Tests/quality | pytest, httpx, ruff · vitest, ESLint (eslint-config-next), tsc |
| Frontend | next 15.5.27, react 19.1.10 — no UI kits, no web fonts, no CDN |

## Installation

### 1. System prerequisites

| Tool | Debian / Ubuntu | macOS (Homebrew) | Windows |
|---|---|---|---|
| Python 3.12 + venv | `sudo apt install python3.12 python3.12-venv` | `brew install python@3.12` | python.org installer (with `py` launcher) |
| Node.js 20 or 22 LTS (≥ 18.18) | nodesource or `nvm install 22` | `brew install node@22` | nodejs.org installer |
| PostgreSQL 15/16 server | `sudo apt install postgresql` | `brew install postgresql@16` | EDB installer (add `bin` to PATH) — or Docker Desktop |
| Tesseract + languages | `sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa tesseract-ocr-osd` | `brew install tesseract tesseract-lang` | UB Mannheim build; tick Spanish |
| Poppler/PDF rendering | not required (PDFium is bundled in pypdfium2) | — | — |
| LibreOffice | `sudo apt install libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress` | `brew install --cask libreoffice` | libreoffice.org; set `PDFWS_SOFFICE_CMD` if not on PATH |
| Pango (EPUB→PDF) | `sudo apt install libpango-1.0-0 libpangoft2-1.0-0` | `brew install pango` | GTK3 runtime (see WeasyPrint docs) |
| Java 11+ (EPUBCheck) | `sudo apt install default-jre-headless` | `brew install openjdk` | Temurin JRE |

On Linux the native PostgreSQL mode does not need the system service running: the scripts create a private
cluster in `data/postgres`. On Debian the server binaries live in `/usr/lib/postgresql/<ver>/bin`, which the
scripts find automatically.

### 2. Project setup (one time)

```bash
./scripts/setup.sh --with-epubcheck
```

This creates `.env` from `.env.example` with random local database passwords, creates `backend/.venv` and
installs pinned Python packages, runs `npm ci` in `frontend/`, initialises and starts PostgreSQL, applies
migrations, downloads EPUBCheck (checksum-verified) and prints tool diagnostics. Each step can also be run
separately (see [Command reference](#command-reference)).

Windows: `powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Setup`.

## Configuration (`.env`)

`.env` is git-ignored and created automatically. All keys, with safe defaults, are documented in
[`.env.example`](.env.example):

| Key | Default | Meaning |
|---|---|---|
| `PDFWS_HOST` | `127.0.0.1` | Backend bind address; non-loopback requires `PDFWS_ALLOW_NETWORK=true` |
| `PDFWS_BACKEND_PORT` / `PDFWS_FRONTEND_PORT` | `8765` / `3000` | Ports (frontend always binds 127.0.0.1) |
| `PDFWS_EXTRA_ALLOWED_HOSTS` | empty | Extra `host:port` values accepted in the Host header |
| `PDFWS_DATA_DIR` | `./data` | Where documents, OCR workspaces, backups and the private DB cluster live |
| `PDFWS_PG_MODE` | `native` | `native` (private cluster), `docker` (`docker-compose.yml`), `external` |
| `PDFWS_PG_PORT` | `54329` | PostgreSQL port (loopback) |
| `POSTGRES_OWNER_PASSWORD` / `POSTGRES_APP_PASSWORD` | generated | Owner role runs migrations; app role has no UPDATE/DELETE on the audit log |
| `DATABASE_ADMIN_URL` / `DATABASE_URL` | generated | SQLAlchemy URLs for the two roles |
| `PDFWS_MAX_UPLOAD_MB`, `PDFWS_MAX_PAGES` | 500, 3000 | Import limits |
| `PDFWS_MAX_CONCURRENT_JOBS`, `PDFWS_MAX_QUEUED_JOBS` | 2, 200 | Worker pool and queue bounds |
| `PDFWS_JOB_TIMEOUT_SECONDS`, `PDFWS_SUBPROCESS_TIMEOUT_SECONDS`, `PDFWS_SUBPROCESS_MEMORY_MB` | 14400, 600, 4096 | Resource limits |
| `PDFWS_TEMP_MAX_AGE_HOURS` | 24 | Abandoned temp files are removed after this |
| `PDFWS_OCR_DPI`, `PDFWS_OCR_LANGUAGES` | 300, `spa+eng` | OCR defaults |
| `PDFWS_TESSERACT_CMD`, `PDFWS_SOFFICE_CMD`, `PDFWS_JAVA_CMD`, `PDFWS_EPUBCHECK_JAR` | on PATH / `tools/` | Tool locations |

No cloud credentials exist or are needed.

### Database initialisation

`./scripts/db.sh start` initialises (first time) and starts the private cluster, which listens on 127.0.0.1
only with SCRAM authentication. It then creates the `pdfws` database and the unprivileged `pdfws_app` role.
`./scripts/db.sh migrate` applies the Alembic migrations as the owner role. With Docker, set
`PDFWS_PG_MODE=docker`; `docker compose up -d db` publishes on `127.0.0.1` only.

## Running

```bash
./scripts/dev.sh
```

The script:
1. validates `.env`
2. starts PostgreSQL and applies migrations
3. prints diagnostics for every native tool, with install hints for anything missing
4. starts the backend and the frontend
5. prints the URL

Open **http://127.0.0.1:3000**. Press Ctrl+C to stop both servers. To serve a production build instead:
`PDFWS_FRONTEND_MODE=prod ./scripts/dev.sh`. On Windows: `scripts\dev.ps1`.

Features whose tools are missing stay visible but fail with a specific "not installed" message. They are never
silently skipped. The home page lists any missing tools.

## OCR language data

Spanish and English (`spa+eng`) plus `osd` (orientation) are the defaults. Check them with
`tesseract --list-langs`. To add a language, install its pack (e.g. `tesseract-ocr-fra`) and use `spa+eng+fra`
in the OCR form or in `PDFWS_OCR_LANGUAGES`. The model files are read locally from Tesseract's tessdata
directory (`TESSDATA_PREFIX` if set). Nothing is downloaded at runtime.

## LibreOffice

The app runs `soffice --headless --convert-to pdf` with an isolated temporary profile per job. The profile sets
macro security to "very high" with execution disabled. Input files get generated names, and a timeout applies.
If `soffice` is not on PATH, set `PDFWS_SOFFICE_CMD` (e.g.
`/Applications/LibreOffice.app/Contents/MacOS/soffice` or `C:\Program Files\LibreOffice\program\soffice.exe`).

## EPUB validation (EPUBCheck)

`./scripts/install-epubcheck.sh` downloads EPUBCheck 5.1.0 into `tools/epubcheck/`, verifying its SHA-256. It
needs Java 11+. Every EPUB produced is checked by internal checks (ZIP structure, container, package metadata,
manifest resources, media types, spine, navigation, XHTML well-formedness, internal links and anchors, real
text, images present and referenced, empty/duplicate documents, reading order) **and** by EPUBCheck when
installed. If EPUBCheck is unavailable, the report says "EPUBCheck not executed" — it never claims a check
that did not run.

## Storage locations and file permissions

See [data/README.md](data/README.md). Briefly: `data/documents/<id>/original.<ext>` (read-only, 0440) and
`data/documents/<id>/versions/vNNN-<operation>-<id>.<ext>`. Directories are 0700 (the data root is 0711, so a
PostgreSQL cluster can run under its own account). The session token is 0600. Storage names are always
generated; uploaded filenames are kept only as sanitised display names in the database.

## Privacy and security model

Summary (full details in [docs/security-privacy.md](docs/security-privacy.md)):
* Loopback binding only, a secret per-run token injected server-side, Host/Origin/Fetch-Metadata checks, no
  CORS, and a strict CSP. Together these stop other websites and DNS-rebinding attacks from using the local API.
* Untrusted-input handling: content-signature validation, size/page limits, archive traversal protection,
  safe XML parsing, no shell interpolation, timeouts and resource limits, and no macro or script execution.
* Passwords and redaction terms are never stored or logged. Restrictions are never bypassed.
* The audit log is append-only (database privileges + triggers) and hash-chained. That detects tampering but
  is not tamper-proof against the machine's administrator.

## Backup and restore

* **Create**: UI → *Storage & backups* → *Create backup*, or `./scripts/backup.sh`. A backup contains a
  consistent (REPEATABLE READ) JSON export of every table, a copy of every stored file, and `manifest.json`
  (SHA-256 per file) plus `manifest.sha256`. It is marked **verified** only after every file has been re-hashed.
* **Verify**: UI → *Verify*, or `./scripts/verify.sh <backup-dir>`.
* **Restore**: stop the app, then run `./scripts/restore.sh <backup-dir> --confirm RESTORE` (or use the UI
  with typed confirmation). The backup is verified first and refused if anything is altered or missing. The
  database schema revision must match. A safety backup of the current state is made automatically. Current
  files are **moved** to `data/quarantine/pre-restore-<time>/`, not deleted. All restored files are then
  re-verified against the database. Metadata is never restored without its files.
* Keep copies of `data/backups/*` on an external or encrypted drive. A backup on the same disk does not
  protect against disk failure.

## Data export and deletion

* Per document: *Library → document → Export all versions (ZIP)* (files + `metadata.json`).
* Everything: *Storage → Export all documents + metadata*, or `GET /api/export`.
* Audit log: *Processing history → Export JSON/CSV*.
* Delete a document: *Library → document → Delete* (type DELETE). This removes its files, versions, OCR
  workspace and thumbnails. The audit log keeps hashes only.
* Delete everything: `./scripts/reset-dev.sh --wipe-data --i-understand-this-deletes-everything`. It makes and
  copies a final backup first.
* Expiration: off by default. Originals never expire unless the separate *original* policy is explicitly enabled.
* Deletion is not forensic erasure (SSDs, snapshots, backups).

## Tests and quality checks

```bash
./scripts/test.sh                 # everything below
./scripts/test.sh --backend-only  # ruff + pytest (unit, integration, e2e)
./scripts/test.sh --frontend-only # tsc, eslint, vitest, next build
./scripts/test.sh --e2e           # only the end-to-end scanned-PDF → EPUB test
```

Backend tests use a separate database (`pdfws_test`, created and migrated per run) and a temporary data
directory, so your documents are never touched. Fixtures are synthetic and generated at test time
(`python -m tests.fixtures <dir>` writes them out): selectable-text PDF, scanned PDF, text + illustration,
multipage, two-column, rotated page, encrypted, malformed, photographed page (skew + perspective),
EPUB with images, DOCX. Tests skip **only** when a native tool is genuinely missing (Tesseract, LibreOffice,
EPUBCheck, PostgreSQL), and pytest's `-rs` output lists each skip with its reason.

Coverage of the required cases: merge page count; split pages; reorder; rotation; compression validity and
size report; visible watermark; redaction across extraction paths (PDFium, pdfminer, raw content streams); OCR
of Spanish + English within the documented tolerance (≥ 95 % character similarity); OCR output is selectable
text; image assets survive hybrid conversion; EPUB validation detects missing resources and broken links;
original hash unchanged after operations; invalid page ranges rejected; unsafe filenames, paths and archives
cannot escape; failed jobs register no outputs; audit events for completed and failed jobs; append-only audit
log; Office conversion; outputs linked to input versions and hashes; deletion and backup/restore behaviour;
interrupted-job recovery/resume; idempotency and cancellation; and the end-to-end scanned PDF → OCR → review →
approve → hybrid EPUB → download → validate → hash and audit checks.

## Command reference

| Task | Command |
|---|---|
| One-command start | `./scripts/dev.sh` (Windows: `scripts\dev.ps1`) |
| Full setup | `./scripts/setup.sh [--with-epubcheck]` |
| Install frontend deps | `cd frontend && npm ci` |
| Create Python env | `python3.12 -m venv backend/.venv` |
| Install backend deps | `backend/.venv/bin/pip install -r backend/requirements.txt` |
| Initialise / start / stop PostgreSQL | `./scripts/db.sh init` · `start` · `stop` · `status` · `psql` |
| Apply migrations | `./scripts/db.sh migrate` (= `cd backend && .venv/bin/alembic upgrade head`) |
| Backend tests | `cd backend && .venv/bin/python -m pytest -rs` |
| Frontend tests | `cd frontend && npm test` |
| TypeScript type check | `cd frontend && npm run typecheck` |
| Lint | `cd backend && .venv/bin/ruff check app tests` · `cd frontend && npm run lint` |
| End-to-end test | `./scripts/test.sh --e2e` (= `pytest -m e2e`) |
| Build frontend | `cd frontend && npm run build` |
| Create backup | `./scripts/backup.sh` |
| Restore backup | `./scripts/restore.sh <dir> --confirm RESTORE` |
| Verify stored hashes (+ audit chain, + backup) | `./scripts/verify.sh [backup-dir]` |
| Tool diagnostics | `cd backend && PYTHONPATH=. .venv/bin/python -m app.cli diagnostics` |
| Install EPUBCheck | `./scripts/install-epubcheck.sh` |
| API docs (local) | http://127.0.0.1:8765/api/docs (token required; use via the app) |

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md) for missing tools, database, ports, tokens, interrupted
jobs, performance and redaction verification failures.

## Known limitations

* OCR quality depends on the scan; reviewing low-confidence regions is recommended for books. Details and
  layout caveats (tables/formulas kept as images, heuristic figure and heading detection, XY-cut reading order,
  no page de-warping) are in [docs/operations.md](docs/operations.md#known-limitations-of-ocr-and-layout-reconstruction).
* EPUB → PDF and Office → PDF change pagination and may substitute fonts; there is no round-trip guarantee.
* Merge does not combine interactive forms, named destinations or attachments (reported per file).
* Redaction verification covers the supported extraction paths; it is not a forensic guarantee.
* The audit log is tamper-evident, not tamper-proof against an administrator.
* The single-user design assumes one OS user; there is no multi-user access control inside the app.

## Updating dependencies

```bash
# Backend: edit backend/requirements.in if needed, then
cd backend && .venv/bin/pip install -U -r requirements.in && \
  (echo "# Pinned, tested versions (Python 3.12). Update with: pip install -U -r requirements.in && pip freeze"; \
   .venv/bin/pip freeze | grep -vE '^(pip|setuptools)==') > requirements.txt
# Frontend: bump exact versions in frontend/package.json (stay on Next.js 15 / React 19), then
cd frontend && npm install && npm audit
# Migrations after model changes
cd backend && .venv/bin/alembic revision --autogenerate -m "describe change" && .venv/bin/alembic upgrade head
# Always
./scripts/test.sh
```

## Resetting the development environment

`./scripts/reset-dev.sh` removes only build caches, temporary files and the thumbnail cache. **Your documents,
database and backups are kept.** `--deps` also removes `backend/.venv` and `frontend/node_modules`. Deleting
data requires the explicit `--wipe-data --i-understand-this-deletes-everything` flags and makes a final backup
first. Never delete `data/` by hand while the database is running.

## Verification log (commands actually executed)

Run on Ubuntu 24.04 (Python 3.12.3, Node 22.22.0, PostgreSQL 16, Tesseract 5.3.4 spa/eng/osd,
LibreOffice 24.2.7.2, OpenJDK 21 + EPUBCheck 5.1.0, WeasyPrint 70.0):

| Command | Result |
|---|---|
| `apt-get install tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa tesseract-ocr-osd ghostscript qpdf` | installed (Tesseract was missing from the image) |
| `python3.12 -m venv backend/.venv && pip install -r requirements.in` | OK; versions frozen into `requirements.txt` |
| `./scripts/db.sh start` / `./scripts/db.sh migrate` | private cluster initialised on 127.0.0.1:54329; migrations 0001–0002 applied |
| `./scripts/install-epubcheck.sh` | EPUBCheck 5.1.0 installed, checksum verified |
| `python -m app.cli diagnostics` | all 11 tools/libraries available |
| `cd frontend && npm install` | 352 packages |
| `ruff check app tests` | All checks passed |
| `pytest -rs` (backend) | **63 passed, 0 skipped, 0 failed** (≈ 54 s) |
| `./scripts/test.sh --e2e` | **1 passed** (end-to-end scanned PDF → hybrid EPUB) |
| `npx tsc --noEmit` | no errors |
| `npx eslint .` | no errors |
| `npx vitest run` | **19 passed** |
| `npx next build` | succeeded (15 routes) |
| `./scripts/test.sh` (all of the above) | All checks passed |
| `./scripts/dev.sh` | started DB, migrations, diagnostics, backend, frontend; reported `http://127.0.0.1:3000` |
| `./scripts/backup.sh` | backup created and **verified** (27 files) |
| `./scripts/verify.sh <backup>` | stored hashes OK, audit chain intact, backup OK (exit 0) |
| Browser run (Playwright + Chromium against the production build) | upload → OCR review (edit, save, approve) → hybrid EPUB (validation passed, EPUBCheck 0 errors, preview with text and image) → page organizer → range validation → audit-chain check: all succeeded |

Issues found and fixed during verification:
* A commit/response race from FastAPI `yield` dependencies (fixed with function-scoped dependencies).
* Document deletion nulling foreign keys (fixed with cascade).
* EPUBCheck failing under an address-space limit (the JVM now runs with heap limits instead).
* The fixed-layout nav document reported by EPUBCheck (now kept out of the pre-paginated spine).
* A deskew sign error.
* A data-directory permission that blocked a separately-owned PostgreSQL cluster.
