"""Synthetic, legally distributable test fixtures generated on demand (nothing copyrighted).

Run `python -m tests.fixtures <dir>` to write them to disk for manual inspection.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pikepdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
                   "/Library/Fonts/Times New Roman.ttf", "C:/Windows/Fonts/times.ttf"]

SPANISH = [
    "El viejo faro se alzaba sobre la costa rocosa.",
    "Cada noche, la señora Núñez subía la escalera",
    "para encender la lámpara y vigilar el océano.",
]
ENGLISH = [
    "The lighthouse keeper wrote in her journal",
    "about the ships that passed during the storm.",
]
SPANISH_TEXT = " ".join(SPANISH)
ENGLISH_TEXT = " ".join(ENGLISH)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def illustration(w: int = 700, h: int = 460) -> Image.Image:
    """A colourful synthetic drawing (sun, sea, lighthouse) with gradients - clearly not text."""
    img = Image.new("RGB", (w, h), (135, 190, 235))
    d = ImageDraw.Draw(img)
    for y in range(h // 2, h):
        t = (y - h / 2) / (h / 2)
        d.line([(0, y), (w, y)], fill=(int(20 + 30 * t), int(80 + 40 * t), int(150 - 40 * t)))
    d.ellipse([w * 0.70, h * 0.08, w * 0.88, h * 0.35], fill=(250, 200, 40))
    d.polygon([(w * 0.25, h * 0.85), (w * 0.33, h * 0.15), (w * 0.41, h * 0.85)], fill=(230, 230, 225),
              outline=(60, 40, 40))
    for k in range(4):
        y = h * (0.3 + 0.13 * k)
        d.rectangle([w * 0.28, y, w * 0.38, y + h * 0.05], fill=(200, 40, 40))
    d.rectangle([w * 0.3, h * 0.10, w * 0.36, h * 0.17], fill=(255, 240, 150))
    return img.filter(ImageFilter.GaussianBlur(0.6))


def scanned_page_image(dpi: int = 300, *, with_figure: bool = True, lines: list[str] | None = None,
                       noise: bool = True, caption: str | None = "Figura 1. El faro de la costa.") -> Image.Image:
    w, h = int(8.27 * dpi), int(11.69 * dpi)
    img = Image.new("RGB", (w, h), (250, 248, 242))
    d = ImageDraw.Draw(img)
    big, body = font(int(dpi * 0.22)), font(int(dpi * 0.13))
    x, y = int(w * 0.12), int(h * 0.08)
    d.text((x, y), "Capítulo 1", font=big, fill=(20, 20, 20))
    y += int(dpi * 0.55)
    for ln in (lines or SPANISH):
        d.text((x, y), ln, font=body, fill=(25, 25, 25))
        y += int(dpi * 0.22)
    y += int(dpi * 0.2)
    if with_figure:
        fig = illustration(int(w * 0.6), int(w * 0.38))
        img.paste(fig, (x, y))
        y += fig.height + int(dpi * 0.12)
        if caption:
            d.text((x, y), caption, font=font(int(dpi * 0.11)), fill=(40, 40, 40))
            y += int(dpi * 0.4)
    for ln in ENGLISH:
        d.text((x, y), ln, font=body, fill=(25, 25, 25))
        y += int(dpi * 0.22)
    d.text((w // 2, int(h * 0.95)), "7", font=body, fill=(40, 40, 40))
    if noise:
        arr = np.asarray(img).astype(np.int16)
        rng = np.random.default_rng(42)
        arr = np.clip(arr + rng.normal(0, 6, arr.shape), 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
    return img


def photographed_page_image(dpi: int = 200) -> Image.Image:
    """Page photographed on a dark table: rotated a few degrees with perspective distortion."""
    page = scanned_page_image(dpi, noise=False)
    page = page.rotate(-3, expand=True, fillcolor=(60, 50, 45))
    bw, bh = int(page.width * 1.18), int(page.height * 1.12)
    canvas_img = Image.new("RGB", (bw, bh), (60, 50, 45))
    canvas_img.paste(page, ((bw - page.width) // 2, (bh - page.height) // 2))
    coeffs = _perspective_coeffs([(0, 0), (bw, 0), (bw, bh), (0, bh)],
                                 [(bw * 0.02, 0), (bw * 0.98, bh * 0.01), (bw, bh), (0, bh * 0.99)])
    return canvas_img.transform((bw, bh), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC,
                                fillcolor=(60, 50, 45))


def _perspective_coeffs(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> list[float]:
    m = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        m.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        m.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    a = np.array(m, dtype=float)
    b = np.array([c for p in src for c in p], dtype=float)
    return list(np.linalg.solve(a, b))


def image_pdf(images: list[Image.Image], out: Path, dpi: int = 300) -> Path:
    c = canvas.Canvas(str(out))
    for im in images:
        wpt, hpt = im.width * 72 / dpi, im.height * 72 / dpi
        c.setPageSize((wpt, hpt))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=92)
        c.drawImage(ImageReader(io.BytesIO(buf.getvalue())), 0, 0, wpt, hpt)
        c.showPage()
    c.save()
    return out


def text_pdf(out: Path, pages: int = 3, *, title: str = "Synthetic Text") -> Path:
    c = canvas.Canvas(str(out), pagesize=A4)
    c.setTitle(title)
    c.setAuthor("PDF Workstation tests")
    for i in range(pages):
        c.setFont("Helvetica-Bold", 20)
        c.drawString(72, 760, f"Chapter {i + 1}")
        c.bookmarkPage(f"ch{i}")
        c.addOutlineEntry(f"Chapter {i + 1}", f"ch{i}", level=0)
        c.setFont("Helvetica", 12)
        c.drawString(72, 720, f"Page {i + 1} body text. The secret code is SWORDFISH-{i}.")
        c.drawString(72, 700, "Contact maria.lopez@example.com for details.")
        c.showPage()
    c.save()
    return out


def text_and_image_pdf(out: Path) -> Path:
    c = canvas.Canvas(str(out), pagesize=A4)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(72, 770, "Illustrated Chapter")
    c.setFont("Helvetica", 12)
    y = 730
    for ln in ["This page has selectable digital text above an embedded picture.",
               "The picture below must be preserved as a separate image asset."]:
        c.drawString(72, y, ln)
        y -= 18
    buf = io.BytesIO()
    illustration(800, 520).save(buf, format="JPEG", quality=90)
    c.drawImage(ImageReader(io.BytesIO(buf.getvalue())), 72, 330, 400, 260)
    c.setFont("Helvetica-Oblique", 10)
    c.drawString(72, 315, "Figure 1. A synthetic lighthouse.")
    c.setFont("Helvetica", 12)
    c.drawString(72, 280, "Text continues after the figure with a final sentence.")
    c.showPage()
    c.save()
    return out


def two_column_pdf(out: Path) -> Path:
    c = canvas.Canvas(str(out), pagesize=A4)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 780, "Two Column Article")
    c.setFont("Helvetica", 11)
    left = [f"Left column line {i} alpha." for i in range(1, 16)]
    right = [f"Right column line {i} omega." for i in range(1, 16)]
    for k, (a, b) in enumerate(zip(left, right, strict=True)):
        y = 740 - k * 15
        c.drawString(72, y, a)
        c.drawString(320, y, b)
    c.showPage()
    c.save()
    return out


def rotated_pdf(out: Path) -> Path:
    text_pdf(out, pages=2)
    with pikepdf.open(out, allow_overwriting_input=True) as pdf:
        pdf.pages[1].rotate(90, relative=True)
        pdf.save(out)
    return out


def encrypted_pdf(out: Path, password: str = "s3cret") -> Path:
    src = out.with_name("plain-for-enc.pdf")
    text_pdf(src, pages=1)
    with pikepdf.open(src) as pdf:
        pdf.save(out, encryption=pikepdf.Encryption(user=password, owner=password + "-owner", R=6))
    src.unlink()
    return out


def malformed_pdf(out: Path) -> Path:
    out.write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\ntrailer garbage\n%%EOF")
    return out


def epub_with_image(out: Path) -> Path:
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("urn:uuid:12345678-1234-5678-1234-567812345678")
    book.set_title("Libro de Prueba")
    book.set_language("es")
    book.add_author("Autora Sintética")
    buf = io.BytesIO()
    illustration(400, 260).save(buf, format="PNG")
    img = epub.EpubItem(uid="img1", file_name="images/faro.png", media_type="image/png", content=buf.getvalue())
    book.add_item(img)
    c1 = epub.EpubHtml(title="Capítulo 1", file_name="c1.xhtml", lang="es")
    c1.content = ("<h1>Capítulo 1</h1><p>Había una vez un faro junto al mar, y la señora Núñez lo cuidaba.</p>"
                  "<figure><img src='images/faro.png' alt='Faro'/><figcaption>El faro</figcaption></figure>")
    c2 = epub.EpubHtml(title="Capítulo 2", file_name="c2.xhtml", lang="es")
    c2.content = "<h1>Capítulo 2</h1><p>La tormenta llegó al amanecer.</p>"
    for ch in (c1, c2):
        book.add_item(ch)
    book.toc = [c1, c2]
    book.spine = ["nav", c1, c2]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(out), book)
    return out


def docx_file(out: Path) -> Path:
    """Minimal hand-built DOCX (no external library), with an accented paragraph."""
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="word/document.xml"/></Relationships>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           '<w:p><w:r><w:t>Informe de prueba: canción, niño, acción.</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Second paragraph in English.</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return out


def write_all(d: Path) -> dict[str, Path]:
    d.mkdir(parents=True, exist_ok=True)
    return {
        "text": text_pdf(d / "text.pdf"),
        "scanned": image_pdf([scanned_page_image()], d / "scanned.pdf"),
        "text_image": text_and_image_pdf(d / "text-image.pdf"),
        "multipage": text_pdf(d / "multipage.pdf", pages=6),
        "two_column": two_column_pdf(d / "two-column.pdf"),
        "rotated": rotated_pdf(d / "rotated.pdf"),
        "encrypted": encrypted_pdf(d / "encrypted.pdf"),
        "malformed": malformed_pdf(d / "malformed.pdf"),
        "photographed": image_pdf([photographed_page_image()], d / "photographed.pdf", dpi=200),
        "epub": epub_with_image(d / "book.epub"),
        "docx": docx_file(d / "report.docx"),
    }


if __name__ == "__main__":
    for k, v in write_all(Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures-out")).items():
        print(f"{k}: {v}")


def novel_pdf(out: Path, *, with_bookmarks: bool = True) -> Path:
    """Digital novel like a typical ebook-to-PDF export: image cover, all-caps chapter names in body size,
    month part titles, indented paragraphs, dialogue, tab-separated words and a hanging last word."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    serif = "Helvetica"
    for cand in FONT_CANDIDATES:
        if Path(cand).exists():
            pdfmetrics.registerFont(TTFont("FxSerif", cand))
            serif = "FxSerif"
            break
    c = canvas.Canvas(str(out), pagesize=(420, 595))
    c.setTitle("Novela de Prueba")
    c.setAuthor("Autora Sintética")
    cover = illustration(840, 1190)
    c.drawImage(ImageReader(cover), 0, 0, 420, 595)
    c.showPage()

    def para_lines(c: canvas.Canvas, y: float, lines: list[str], indent: bool = True) -> float:
        for k, ln in enumerate(lines):
            t = c.beginText(50 + (14 if indent and k == 0 else 0), y)
            t.setFont(serif, 11)
            for word_i, w in enumerate(ln.split(" ")):
                if word_i:
                    t.textOut("\t")  # word gaps encoded as tabs, as in many exported PDFs
                t.textOut(w)
            c.drawText(t)
            y -= 15
        return y

    chapters = [("ENERO", "(VERANO)", "AXEL"), (None, None, "LEAH"), ("FEBRERO", "(VERANO)", "AXEL")]
    for n, (month, season, name) in enumerate(chapters, start=1):
        y = 520
        c.setFont(serif, 11)
        if month:
            c.drawCentredString(210, y, month)
            c.drawCentredString(210, y - 15, season or "")
            y -= 50
        c.drawCentredString(210, y, name)
        if with_bookmarks:
            key = f"c{n}"
            c.bookmarkPage(key)
            c.addOutlineEntry(f"{n}. {name.capitalize()}", key, level=0)
        y -= 40
        y = para_lines(c, y, ["Era el capítulo número " + str(n) + " y la casa estaba en", "silencio desde hacía horas."])
        y = para_lines(c, y, ["—¿Vienes? —preguntó ella, y él se echó a"])
        c.setFont(serif, 11)
        c.drawString(50, y, "reír.")
        y -= 15
        y = para_lines(c, y, ["El mar golpeaba las rocas con una fuerza que", "nadie recordaba."])
        c.showPage()
    c.save()
    return out


def scanned_book_with_footer(out: Path, pages: int = 6, dpi: int = 200) -> Path:
    """Scanned pages with a site footer ("www.lectulandia.com - Página N") printed above the bottom margin."""
    imgs = []
    body = font(int(dpi * 0.13))
    for n in range(1, pages + 1):
        w, h = int(8.27 * dpi), int(11.69 * dpi)
        img = Image.new("RGB", (w, h), (250, 248, 242))
        d = ImageDraw.Draw(img)
        y = int(h * 0.1)
        for ln in SPANISH + ENGLISH:
            d.text((int(w * 0.12), y), f"{ln}", font=body, fill=(25, 25, 25))
            y += int(dpi * 0.22)
        d.text((int(w * 0.12), y), f"Fin del texto de la página {n}.", font=body, fill=(25, 25, 25))
        d.text((int(w * 0.3), int(h * 0.86)), f"www.lectulandia.com - Página {n + 8}", font=font(int(dpi * 0.1)),
               fill=(60, 60, 60))
        imgs.append(img)
    return image_pdf(imgs, out, dpi=dpi)
