"""Conversion and OCR dispatch.

Heavy dependencies are imported lazily so the ExApp can still start and report
clear errors if a Docker image was built without an optional tool.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .models import OutputFormat

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


class ConversionError(RuntimeError):
    """Raised when a requested conversion cannot be completed."""


def convert_document(
    input_path: Path,
    output_format: OutputFormat,
    output_dir: Path,
    *,
    ocr_lang: str = "ru",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_ext = input_path.suffix.lower()

    if output_format == OutputFormat.SEARCHABLE_PDF:
        return to_searchable_pdf(input_path, output_dir, ocr_lang)
    if output_format == OutputFormat.PDF:
        return to_pdf(input_path, output_dir, ocr_lang)
    if output_format == OutputFormat.DOCX:
        return to_docx(input_path, output_dir)
    if output_format == OutputFormat.TXT:
        return to_txt(input_path, output_dir, ocr_lang)
    if output_format == OutputFormat.MARKDOWN:
        return to_markdown(input_path, output_dir)
    if output_format == OutputFormat.HTML:
        return to_html(input_path, output_dir)
    if output_format == OutputFormat.EPUB:
        return to_epub(input_path, output_dir)

    raise ConversionError(f"Unsupported output format: {output_format}")


def to_pdf(input_path: Path, output_dir: Path, ocr_lang: str) -> Path:
    ext = input_path.suffix.lower()
    if ext == ".pdf":
        output = output_dir / input_path.name
        shutil.copy2(input_path, output)
        return output
    if ext in IMAGE_EXTENSIONS:
        return image_to_pdf(input_path, output_dir / f"{input_path.stem}.pdf", ocr_lang)
    if ext in {".html", ".htm"}:
        return html_to_pdf(input_path, output_dir / f"{input_path.stem}.pdf")
    if ext == ".epub":
        return epub_to_pdf(input_path, output_dir / f"{input_path.stem}.pdf")
    return libreoffice_convert(input_path, output_dir, "pdf")


def to_docx(input_path: Path, output_dir: Path) -> Path:
    if input_path.suffix.lower() == ".pdf":
        try:
            from pdf2docx import Converter
        except Exception as exc:  # pragma: no cover - dependency availability
            raise ConversionError("pdf2docx is not available in the container.") from exc

        output = output_dir / f"{input_path.stem}.docx"
        converter = Converter(str(input_path))
        try:
            converter.convert(str(output), start=0, end=None)
        finally:
            converter.close()
        return output
    return pandoc_convert(input_path, output_dir / f"{input_path.stem}.docx")


def to_txt(input_path: Path, output_dir: Path, ocr_lang: str) -> Path:
    ext = input_path.suffix.lower()
    output = output_dir / f"{input_path.stem}.txt"
    if ext == ".pdf":
        try:
            import fitz
        except Exception as exc:  # pragma: no cover
            raise ConversionError("PyMuPDF is not available in the container.") from exc

        with fitz.open(input_path) as doc:
            text = "\n\n".join(page.get_text("text") for page in doc)
        if text.strip():
            output.write_text(text, encoding="utf-8")
            return output
        output.write_text(ocr_file_to_text(input_path, ocr_lang), encoding="utf-8")
        return output
    if ext in IMAGE_EXTENSIONS:
        output.write_text(ocr_file_to_text(input_path, ocr_lang), encoding="utf-8")
        return output
    if ext == ".docx":
        markdown = to_markdown(input_path, output_dir)
        output.write_text(markdown.read_text(encoding="utf-8"), encoding="utf-8")
        return output
    if ext in {".txt", ".md", ".markdown", ".html", ".htm"}:
        output.write_text(input_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        return output
    return pandoc_convert(input_path, output)


def to_markdown(input_path: Path, output_dir: Path) -> Path:
    ext = input_path.suffix.lower()
    output = output_dir / f"{input_path.stem}.md"
    if ext == ".docx":
        try:
            import mammoth

            with input_path.open("rb") as docx_file:
                result = mammoth.convert_to_markdown(docx_file)
            output.write_text(result.value, encoding="utf-8")
            return output
        except Exception:
            return pandoc_convert(input_path, output)
    if ext in {".md", ".markdown"}:
        shutil.copy2(input_path, output)
        return output
    if ext == ".pdf":
        txt = to_txt(input_path, output_dir, os.getenv("DOCUMENT_TOOLS_OCR_LANG", "ru"))
        output.write_text(txt.read_text(encoding="utf-8"), encoding="utf-8")
        return output
    return pandoc_convert(input_path, output)


def to_html(input_path: Path, output_dir: Path) -> Path:
    output = output_dir / f"{input_path.stem}.html"
    if input_path.suffix.lower() in {".html", ".htm"}:
        shutil.copy2(input_path, output)
        return output
    return pandoc_convert(input_path, output)


def to_epub(input_path: Path, output_dir: Path) -> Path:
    if input_path.suffix.lower() == ".epub":
        output = output_dir / input_path.name
        shutil.copy2(input_path, output)
        return output
    return pandoc_convert(input_path, output_dir / f"{input_path.stem}.epub")


def to_searchable_pdf(input_path: Path, output_dir: Path, ocr_lang: str) -> Path:
    ext = input_path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return image_to_pdf(input_path, output_dir / f"{input_path.stem}.searchable.pdf", ocr_lang)
    if ext != ".pdf":
        input_path = to_pdf(input_path, output_dir, ocr_lang)

    try:
        import fitz
        from PIL import Image
    except Exception as exc:  # pragma: no cover
        raise ConversionError("PyMuPDF and Pillow are required for searchable PDF OCR.") from exc

    ocr = paddle_ocr(ocr_lang)
    output = output_dir / f"{input_path.stem}.searchable.pdf"
    with fitz.open(input_path) as doc:
        for page_index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image_path = output_dir / f"page-{page_index + 1}.png"
            pix.save(image_path)
            image = Image.open(image_path)
            scale_x = page.rect.width / image.width
            scale_y = page.rect.height / image.height
            for text, box in iter_ocr_boxes(ocr, image_path):
                if not text.strip():
                    continue
                x0 = min(point[0] for point in box) * scale_x
                y0 = min(point[1] for point in box) * scale_y
                x1 = max(point[0] for point in box) * scale_x
                y1 = max(point[1] for point in box) * scale_y
                rect = fitz.Rect(x0, y0, x1, y1)
                page.insert_textbox(
                    rect,
                    text,
                    fontsize=max(4, rect.height * 0.7),
                    color=(1, 1, 1),
                    fill_opacity=0,
                    render_mode=3,
                )
            image_path.unlink(missing_ok=True)
        doc.save(output, garbage=4, deflate=True)
    return output


def image_to_pdf(input_path: Path, output: Path, ocr_lang: str) -> Path:
    try:
        import fitz
        from PIL import Image
    except Exception as exc:  # pragma: no cover
        raise ConversionError("PyMuPDF and Pillow are required for image OCR.") from exc

    with Image.open(input_path) as image:
        width, height = image.size
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_image(page.rect, filename=str(input_path))

    ocr = paddle_ocr(ocr_lang)
    for text, box in iter_ocr_boxes(ocr, input_path):
        if not text.strip():
            continue
        rect = fitz.Rect(
            min(point[0] for point in box),
            min(point[1] for point in box),
            max(point[0] for point in box),
            max(point[1] for point in box),
        )
        page.insert_textbox(
            rect,
            text,
            fontsize=max(4, rect.height * 0.7),
            color=(1, 1, 1),
            fill_opacity=0,
            render_mode=3,
        )
    doc.save(output, garbage=4, deflate=True)
    doc.close()
    return output


def ocr_file_to_text(input_path: Path, ocr_lang: str) -> str:
    try:
        import fitz
    except Exception as exc:  # pragma: no cover
        raise ConversionError("PyMuPDF is required for OCR text extraction.") from exc

    ocr = paddle_ocr(ocr_lang)
    chunks: list[str] = []
    if input_path.suffix.lower() == ".pdf":
        with fitz.open(input_path) as doc:
            for page_index, page in enumerate(doc):
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image_path = input_path.parent / f"ocr-page-{page_index + 1}.png"
                pix.save(image_path)
                chunks.append(ocr_image_to_text(ocr, image_path))
                image_path.unlink(missing_ok=True)
    else:
        chunks.append(ocr_image_to_text(ocr, input_path))
    return "\n\n".join(chunk for chunk in chunks if chunk.strip())


def ocr_image_to_text(ocr: object, image_path: Path) -> str:
    lines = [text for text, _box in iter_ocr_boxes(ocr, image_path)]
    return "\n".join(lines)


def iter_ocr_boxes(ocr: object, image_path: Path):
    result = ocr.ocr(str(image_path), cls=True)
    pages = result if result and isinstance(result[0], list) else [result]
    for page in pages:
        if not page:
            continue
        for line in page:
            if not line or len(line) < 2:
                continue
            box, payload = line[0], line[1]
            text = payload[0] if payload else ""
            yield text, box


def paddle_ocr(lang: str) -> object:
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:  # pragma: no cover
        raise ConversionError("PaddleOCR is not available in the container.") from exc
    return PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)


def html_to_pdf(input_path: Path, output: Path) -> Path:
    try:
        from weasyprint import HTML

        HTML(filename=str(input_path)).write_pdf(str(output))
        return output
    except Exception:
        return libreoffice_convert(input_path, output.parent, "pdf")


def epub_to_pdf(input_path: Path, output: Path) -> Path:
    if shutil.which("ebook-convert"):
        run(["ebook-convert", str(input_path), str(output)])
        return output
    return pandoc_convert(input_path, output)


def libreoffice_convert(input_path: Path, output_dir: Path, target_format: str) -> Path:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise ConversionError("LibreOffice headless is not installed in the container.")
    before = set(output_dir.iterdir())
    run([
        soffice,
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to",
        target_format,
        "--outdir",
        str(output_dir),
        str(input_path),
    ])
    after = set(output_dir.iterdir())
    created = sorted(after - before, key=lambda path: path.stat().st_mtime, reverse=True)
    if created:
        return created[0]
    expected = output_dir / f"{input_path.stem}.{target_format.split(':', 1)[0]}"
    if expected.exists():
        return expected
    raise ConversionError("LibreOffice did not produce an output file.")


def pandoc_convert(input_path: Path, output: Path) -> Path:
    if not shutil.which("pandoc"):
        raise ConversionError("Pandoc is not installed in the container.")
    run(["pandoc", str(input_path), "-o", str(output)])
    return output


def run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise ConversionError(detail) from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"Command timed out: {' '.join(command)}") from exc
