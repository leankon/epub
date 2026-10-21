# Architecture

```
Browser ──HTTP──▶ Next.js 15 (127.0.0.1:3000)
                    ├─ pages (React, TypeScript strict)
                    └─ /api/backend/* proxy ── adds session token ──▶ FastAPI (127.0.0.1:8765)
                                                                    ├─ PostgreSQL (metadata, jobs, audit)
                                                                    ├─ data/ (document binaries)
                                                                    └─ local tools: Tesseract, LibreOffice,
                                                                       EPUBCheck (Java), PDFium, WeasyPrint
```

## Components

| Layer | Technology | Responsibility |
|---|---|---|
| UI | Next.js 15 App Router, React 19, TypeScript strict | Tools, OCR review, EPUB workspace, library, history, storage, settings |
| Proxy | Next.js route handler `app/api/backend/[...path]` | Host/Origin/Fetch-Metadata checks; injects the backend token server-side; streams bodies |
| API | FastAPI + Pydantic | Validated endpoints; structured errors; streaming downloads |
| Jobs | PostgreSQL-backed queue + bounded worker threads | `FOR UPDATE SKIP LOCKED` claiming, persisted progress/steps, cancellation, resume |
| PDF engine | pikepdf (qpdf) | Merge, split, organize, rotate, compress, overlays, metadata, bookmarks, encryption, redaction rewrite |
| Rendering | pypdfium2 (PDFium) | Thumbnails, previews, OCR renders, text positions for redaction and verification |
| Text layout (digital) | pdfminer.six | Text boxes, lines and font sizes from existing text layers |
| OCR | Tesseract 5 (spa+eng, osd) via local executable | Word boxes and confidences (TSV) |
| Image processing | OpenCV | Orientation, deskew, perspective, contrast, denoise, thresholding, figure/table detection |
| EPUB | EbookLib + own XHTML generation | EPUB 3 packaging, nav + NCX, fixed-layout metadata |
| Validation | Internal checks (lxml) + EPUBCheck 5.1 (Java) | Structure, resources, links, text, images, order |
| EPUB→PDF | WeasyPrint (Pango) with sandboxed URL fetcher | Local HTML/CSS rendering, bookmarks from headings |
| Office→PDF | LibreOffice headless | Isolated per-job profile with macros disabled |
| Database | PostgreSQL 16 (native private cluster or Docker) + Alembic | All metadata; append-only audit log |

## Data model (PostgreSQL)

* `documents` – logical identity (title, original filename, kind).
* `document_versions` – one immutable file each: version number, `is_original`, parent version, producing job,
  operation, storage path (relative, generated), size, **SHA-256**, page count, validation result, expiry.
* `document_metadata` – inspection of a version: metadata, encryption, signatures, forms, annotations,
  attachments, JavaScript, page sizes, revisions.
* `jobs`, `job_inputs` (exact input version + hash), `job_steps`, `batches` – processing records with sanitised
  parameters (no passwords/terms), timestamps, duration, warnings, tool versions, attempts.
* `ocr_sessions`, `ocr_pages` (per-page checkpoint with regions/roles/boxes/confidences), `ocr_corrections`
  (history of manual edits: lengths and fields, not content).
* `audit_events` – append-only, hash-chained event log (see security doc).
* `expiration_policies`, `backup_manifests`, `app_settings`.

Binaries never go into PostgreSQL.

## Storage and immutability

Uploads stream into `data/tmp/uploads`, are validated by **content signature** (not extension), hashed, and
moved with a link-based no-overwrite move to `data/documents/<id>/original.<ext>` (mode 0440). Every operation
writes into its job workspace; on success the job manager moves outputs to
`data/documents/<id>/versions/vNNN-<operation>-<shortid>.<ext>` **and** inserts the version rows in the same
database transaction that marks the job succeeded. If that transaction fails, the moved files are deleted.
If the process dies between the move and the commit, startup recovery moves unregistered files to
`data/quarantine/orphans/<timestamp>/` and marks running jobs `interrupted`.

Before each job runs, input files are re-hashed and compared with the database (integrity check).

## Jobs

* Bounded concurrency (`PDFWS_MAX_CONCURRENT_JOBS`), bounded queue (`PDFWS_MAX_QUEUED_JOBS`, HTTP 429 when full),
  upload concurrency limit, per-job timeout, per-subprocess timeout and address-space limit.
* Progress is stage-based. A percentage is shown only when a real total exists (pages, files); otherwise the UI
  shows the stage with an indeterminate indicator.
* Cancellation is checked between stages and pages. Interrupted jobs can be resumed; OCR resumes from the last
  completed page because each page is committed as a checkpoint.
* Secrets (passwords, redaction terms) live only in worker memory. A job interrupted by a restart asks for them
  again on resume.
* Idempotency keys make retries safe.

## OCR and layout pipeline

1. **Classification** (per page, PDFium): character count, text plausibility, invisible-text ratio (existing OCR
   layers), image coverage → `digital`, `mixed`, `scanned`, `ocr_layer`, `corrupt_text`, `no_text`.
   Good digital pages are **not** OCR'd (pdfminer layout is used instead), avoiding duplicate text.
2. **Preprocessing** (OpenCV, on copies): perspective correction for photographed pages (only when the paper
   outline clearly differs from the background), orientation via Tesseract OSD, deskew from text-line angles,
   CLAHE for low contrast, non-local-means denoising for noisy pages, adaptive threshold for uneven lighting,
   resolution normalisation from measured glyph height. Geometric corrections are applied to a colour copy
   (used for review and figure crops); photometric ones only to the OCR image. The original render is kept.
3. **Recognition**: Tesseract `--oem 1 --psm 3`, TSV output (word boxes, confidences).
4. **Layout**: non-text ink and mid-tone/colour areas not explained by recognised words become figure
   candidates (photo vs illustration by tonal statistics); ruled grids become tables; symbol-dense lines become
   formulas. Words inside figures become figure labels and stay out of the reading flow (no duplication).
   Roles: heading (size vs body text, chapter keywords), caption (near a figure, "Figura/Figure…" or smaller
   type), header/footer/page number (margins), footnote (small type, bottom, marker), marginal note, list item.
5. **Reading order**: recursive XY-cut preferring column (vertical) cuts, so two-column text is read column by
   column, never interleaved line by line. Captions follow their figure.

**Evaluated and not adopted**: PaddleOCR / PP-Structure and deep layout models (LayoutParser/Detectron2,
DocLayout-YOLO). They improve some layouts but require large frameworks (PaddlePaddle/PyTorch), and their model
weights are normally fetched from the internet at first use. That conflicts with the offline-first and
reproducible-install requirements, and would add a second competing OCR pipeline. The OpenCV + Tesseract
pipeline above is fully local and deterministic. A heavier detector could later be plugged in at the
`detect_visual_regions` boundary with pre-downloaded weights.

## EPUB construction

Regions (with user corrections taking precedence) are flattened into blocks; paragraphs broken by page or column
breaks are re-joined (with de-hyphenation); running headers/footers/page numbers are omitted and counted in the
report; footnotes go to a notes section of their chapter. Chapters split at the top heading level (or user-defined
boundaries; or 15-page chunks if no headings exist). Strategies:

* **hybrid** (default): semantic XHTML + figure assets; tables/formulas and pages whose OCR failed or is very low
  confidence (and not reviewed) become visible image fallbacks, all listed in the report.
* **reflowable**: like hybrid, but never replaces a whole page with an image unless analysis failed.
* **fixed**: pre-paginated EPUB 3; each page image with absolutely positioned transparent text (selectable,
  searchable).

Every build is validated (internal checks + EPUBCheck when installed). A failing EPUB is **not registered**
and cannot be downloaded; the job fails with the validation report.
