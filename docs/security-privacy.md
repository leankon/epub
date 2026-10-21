# Security and privacy

## What stays local

* Documents, page renders, OCR text, EPUBs, thumbnails, logs and the database all live on this computer.
* No accounts, no telemetry, no analytics, no cloud OCR/AI, no hosted conversion, no CDN assets at runtime
  (system fonts only; Next.js telemetry is disabled by `NEXT_TELEMETRY_DISABLED=1` in every script).
* Network access is only needed to *install* dependencies, Tesseract language data and EPUBCheck.
* EPUB→PDF rendering uses a fetcher that refuses every URL except files inside the extracted EPUB and `data:`.

## Local attack surface and protections

| Threat | Protection |
|---|---|
| Other machines on the network | Both servers bind to `127.0.0.1`. Binding elsewhere requires `PDFWS_ALLOW_NETWORK=true`. |
| Malicious websites calling the local API (CSRF) | Backend requires a random per-run session token (`data/run/session-token`, mode 0600) that only the Next.js server reads; the browser never sees it. The proxy rejects foreign `Origin`, `Sec-Fetch-Site: cross-site/same-site`, and state-changing requests without the custom `X-PDFWS-Client` header (forces a CORS preflight that is never granted). No CORS headers are ever sent. |
| DNS rebinding | Host header allow-list on both proxy and backend (HTTP 421 otherwise). |
| Untrusted files | Type from content signatures; size and page limits; Pillow decompression-bomb guard; generated storage names; path containment checks; ZIP extraction rejects traversal, absolute paths, symlinks, too many entries and suspicious compression ratios; XML parsed with entities and network disabled. |
| Active content | PDF JavaScript is never executed (no viewer). LibreOffice runs with a throwaway profile, macro security "very high" and macro execution disabled. EPUB previews strip scripts and are served with a restrictive CSP inside a script-less sandboxed iframe. |
| Subprocesses | argv lists only (never a shell), generated filenames, timeouts, process-group kill, address-space limits (except JVM/LibreOffice which get heap limits and timeouts), no core dumps. |
| Resource exhaustion | Bounded worker pool, bounded queue (HTTP 429), upload concurrency limit, job and subprocess timeouts, pages processed one at a time. |
| Error leakage | Structured error bodies without stack traces or paths; details go to the backend log only. |
| SQL injection | SQLAlchemy parameterised queries throughout. |
| Secrets in logs/DB | Passwords and redaction terms are kept in memory only; job parameters are sanitised before storage; audit details are scrubbed; corrections history stores lengths, not text. |

## Encryption and access controls

Encrypted PDFs are detected on import. Passwords are requested per operation, used in memory, and never stored.
Usage restrictions (owner password) are **never bypassed**: removing them requires the owner password, even if
the file opens without a user password. Nothing attempts to guess passwords.

## Redaction

Redaction rewrites the page content stream glyph by glyph (keeping the position of the remaining text), paints
image pixels black under the marked areas (on page-local copies of the image), drops vector paths that lie
inside an area, rewrites affected form XObjects, removes overlapping annotations and clears form values. It also
removes thumbnails and, by default, document metadata, XMP, attachments, JavaScript and automatic actions,
accessibility tags (which can contain `/ActualText` copies), and redacted terms in bookmark titles. The output is
a full rewrite with no incremental-update history.

The result is then checked with PDFium and pdfminer.six text extraction, plus a check for characters inside the
redacted areas. If anything remains extractable, the job fails and no file is saved.

**Limitations**: fonts with non-identity CMaps are handled approximately (reported as a warning). Vector shapes
that only partially overlap an area are covered but not removed. Content inside embedded fonts' glyph
programs, unusual encodings or exotic structures may escape automated checks. For high-risk documents,
inspect the output visually and with specialised forensic tools. No forensic-grade guarantee is made.

## Signatures and forms

Signature fields, their byte ranges, and form fields are inspected. Operations that would modify a signed or form
document require explicit confirmation (HTTP 409 `confirmation_required` until acknowledged). Modifying a signed
PDF invalidates its signature. This application does not verify signer identity cryptographically and is **not**
a qualified or regulated electronic-signature platform. The personal visual-signature workflow is not
implemented (see operations.md).

## Audit log

`audit_events` is append-only:
* The runtime database role (`pdfws_app`) has `SELECT, INSERT` only on it; `UPDATE/DELETE/TRUNCATE` are revoked.
* Triggers reject `UPDATE`, `DELETE` and `TRUNCATE` for every role.
* Each event stores `seq`, `prev_hash` and `event_hash = sha256(canonical row ‖ prev_hash)`; `GET /api/audit/verify`
  (or `./scripts/verify.sh`) recomputes the chain.

This makes accidental or application-level tampering detectable. It is **not** tamper-proof against the
machine's administrator or the database owner role, who can disable triggers or rewrite the chain.

## Deletion

Deleting a document removes its files (originals, versions, OCR workspace, thumbnails) and database rows; the
audit log keeps only hashes and counts. Ordinary deletion does **not** guarantee forensic erasure on SSDs
(wear levelling), copy-on-write filesystems, snapshots, or in backups and copies you made. Use full-disk
encryption for sensitive material.

## What local processing does not protect against

Malware or a compromised computer, other users with access to your OS account or an administrator account,
physical theft of an unencrypted disk, cloud-synced folders containing the data directory, and backups copied
elsewhere. The application is not "absolutely secure".

## Dependency and security update policy

* Python dependencies are pinned in `backend/requirements.txt` (direct intent in `requirements.in`); npm versions
  are pinned exactly in `frontend/package.json` and locked in `package-lock.json`. EPUBCheck is pinned by version
  and SHA-256.
* Review updates monthly, and immediately for security advisories affecting pikepdf/qpdf, PDFium, Pillow, lxml,
  OpenCV, FastAPI/Starlette, Next.js or PostgreSQL. Run `pip-audit` / `npm audit` locally if desired (not run
  automatically: the app never phones home).
* Update procedure: see README → Updating dependencies; always run `./scripts/test.sh` afterwards.
* Keep OS packages (Tesseract, LibreOffice, PostgreSQL, Java) updated via your package manager.
