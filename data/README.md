# Local data directory

Created and managed by the application (path configurable with `PDFWS_DATA_DIR`). Nothing here is
committed to Git.

| Path | Contents |
|---|---|
| `documents/<document-id>/original.<ext>` | Immutable imported original (read-only file) |
| `documents/<document-id>/versions/vNNN-<operation>-<id>.<ext>` | Immutable derived versions |
| `ocr/<session-id>/` | Page renders, processed images and extracted figure assets for OCR review |
| `postgres/` | Private PostgreSQL cluster (native mode) |
| `backups/<timestamp>-<id>/` | Backups: `db/*.jsonl`, `files/`, `manifest.json`, `manifest.sha256` |
| `exports/` | Temporary export ZIPs (removed after download) |
| `tmp/` | Job workspaces and uploads in progress (cleaned automatically) |
| `cache/` | Page thumbnails (safe to delete) |
| `quarantine/` | Orphaned files found at startup and files replaced by a restore (never auto-deleted) |
| `run/` | Session token (`0600`) and logs |
