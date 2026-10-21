# Supported operations and limitations

Status: **Implemented** = works end to end and is covered by tests or verified in the UI; **Partial** = works
with documented gaps; **Unavailable** = not implemented.

| Operation | Status | Notes |
|---|---|---|
| Import (PDF, EPUB, DOCX/ODT/DOC/RTF, XLSX/ODS/XLS, PPTX/ODP/PPT, PNG/JPEG/TIFF/WebP/BMP/GIF) | Implemented | Content-signature validation, size/page limits, encryption/signature/form inspection |
| Merge | Implemented | Order preserved; page sizes and rotations kept; bookmarks kept (grouped per file); first file's metadata kept. Forms, named destinations, attachments: warned, not merged |
| Split (ranges / extract / every page / bookmarks) | Implemented | Bookmarks within ranges are remapped |
| Reorder / rotate / duplicate / delete pages | Implemented | Drag-and-drop thumbnail organizer + keyboard buttons |
| Compress (lossless / high quality / balanced / strong) | Implemented | Lossy presets downsample to target DPI based on displayed image size and re-encode 8-bit RGB/gray images as JPEG; masks, CMYK, JBIG2, JPX and 1-bit images are left untouched. Reports sizes and reduction. Never claims lossless for lossy presets |
| Text / image watermark | Implemented | Position, opacity, size, rotation, colour, page ranges, tiled mode; upright on rotated pages; live preview |
| Page numbers | Implemented | Formats with `{n}`/`{total}`, start number, position, margin, page ranges; preview |
| Permanent redaction (areas and search terms) | Implemented | See security doc for method, verification and limitations |
| Metadata (title, author, subject, keywords, language) | Implemented | Info dictionary and XMP kept in sync |
| Bookmarks (add / edit / nest / remove) | Implemented | |
| Protect (AES-256) / unlock | Implemented | Owner password required to lift restrictions; nothing is bypassed |
| Extract images | Implemented | Original encoding where possible (JPEG/JPX passthrough) with manifest |
| PDF to images (PNG/JPEG) | Implemented | 36–600 DPI |
| Images to PDF | Implemented | img2pdf (lossless embedding of JPEG/PNG); alpha flattened onto white |
| Inspect and integrity | Implemented | Page sizes, metadata, signatures (structural only), forms, annotations, attachments, JavaScript, incremental revisions, SHA-256 |
| Searchable PDF (OCR text layer) | Implemented | Invisible text over original images; pages with good text skipped; existing OCR layers skipped unless forced |
| OCR analysis + layout + review/correction + approval | Implemented | Per-page checkpoints, resume, reprocess with other settings |
| PDF to EPUB (hybrid / reflowable / fixed) | Implemented | Validation gate; conversion report |
| EPUB validation | Implemented | Internal checks; EPUBCheck when installed (reported honestly when not) |
| EPUB to PDF | Implemented | WeasyPrint; pagination differs from e-readers; complex CSS may render differently |
| Office to PDF | Implemented | LibreOffice headless; fonts/layout may change |
| Batch processing | Implemented | compress, watermark, page numbers, rotate, metadata, OCR, PDF→images, extract images, Office→PDF, EPUB→PDF |
| Version history, comparison, download | Implemented | Comparison: hashes, size, page count, metadata diff, first-page thumbnails |
| Audit log (append-only, hash chain), export JSON/CSV | Implemented | |
| Backup / verify / restore, export, deletion, expiration | Implemented | |
| Table reconstruction as HTML tables | Unavailable | Tables are preserved as image fallbacks |
| MathML for formulas | Unavailable | Formulas are preserved as image fallbacks |
| Cryptographic signature validation, signing | Unavailable | Deliberately excluded (see security doc) |
| Personal visual signature workflow | Unavailable | Not implemented; a watermark image can serve as a visual stamp, which proves nothing about identity |
| Full PDF text editing (Acrobat-style) | Unavailable | Out of scope |
| Granular progress for single-step tools | Partial | Stage + indeterminate indicator by design (no invented percentages) |

## Known limitations of OCR and layout reconstruction

* Accuracy depends on scan quality. Expect errors with low resolution (< 200 DPI), blur, heavy JPEG artefacts,
  decorative or historical typefaces, handwriting, mixed scripts beyond the configured languages, and text on
  textured or coloured backgrounds.
* Figure detection is heuristic. Very light line drawings may be missed. Dense text set on a coloured box may
  be treated as an illustration. Use the role selector in the review screen to fix this.
* Tables, formulas and complex diagrams are kept as images (with their labels as alt text), not rebuilt as markup.
* Heading levels come from relative type size and keywords ("Capítulo", "Chapter", …). Small-caps or same-size
  headings may be missed. Set the role or the chapter boundaries manually.
* Footnote detection needs smaller type at the bottom of the page with a marker. Endnote sections are treated as
  ordinary text.
* Reading order uses XY-cut. Irregular magazine layouts, text wrapping around figures, and sidebars spanning
  columns can produce an unexpected order. Check the order in the review screen.
* Photographed pages: perspective correction needs the page edges visible against a contrasting background.
  Curved pages from book spines are not de-warped.
* Pages are processed one at a time at the configured DPI (300 by default). A 300-page book typically takes
  several minutes per 100 pages on a modern laptop (CPU-bound Tesseract).
