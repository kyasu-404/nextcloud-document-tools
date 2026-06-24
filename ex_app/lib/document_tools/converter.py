"""Conversion and OCR dispatch."""

from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
from pathlib import Path

from .models import OutputFormat

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}
DOCUMENT_EXTENSIONS = {".doc", ".docx", ".odt", ".rtf"}
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".html", ".htm"}
EPUB_EXTENSIONS = {".epub"}


class ConversionError(RuntimeError):
    """Raised when a requested conversion cannot be completed."""


def convert_document(
    input_path: Path,
    output_format: OutputFormat,
    output_dir: Path,
    *,
    ocr_lang: str = "rus+eng",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    validate_conversion(input_path, output_format)

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

    raise ConversionError(f"Формат результата не поддерживается: {output_format}")


def validate_conversion(input_path: Path, output_format: OutputFormat) -> None:
    ext = input_path.suffix.lower()
    if ext in IMAGE_EXTENSIONS and output_format not in {
        OutputFormat.SEARCHABLE_PDF,
        OutputFormat.PDF,
        OutputFormat.TXT,
    }:
        raise ConversionError("Для изображений доступны только распознавание текста и PDF с OCR.")
    if ext in EPUB_EXTENSIONS and output_format not in {OutputFormat.PDF, OutputFormat.TXT, OutputFormat.HTML}:
        raise ConversionError("Для EPUB сейчас доступны PDF, HTML и TXT.")
    if output_format == OutputFormat.DOCX and ext in IMAGE_EXTENSIONS:
        raise ConversionError("Изображение нельзя напрямую преобразовать в DOCX. Сначала распознайте текст.")


def diagnostics() -> dict[str, object]:
    imports = {}
    for module in ("fitz", "pdf2docx", "mammoth", "weasyprint", "paddleocr"):
        try:
            imported = __import__(module)
            imports[module] = {"ok": True, "version": getattr(imported, "__version__", "")}
        except Exception as exc:
            imports[module] = {"ok": False, "error": str(exc)}

    commands = {}
    for command in ("pandoc", "pdftotext", "ocrmypdf", "tesseract", "libreoffice", "soffice", "qpdf", "gs", "file"):
        commands[command] = shutil.which(command) or ""

    tesseract_languages: list[str] = []
    if commands["tesseract"]:
        try:
            result = subprocess.run(
                ["tesseract", "--list-langs"],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            tesseract_languages = [
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip() and not line.startswith("List of available")
            ]
        except Exception:
            tesseract_languages = []

    return {
        "commands": commands,
        "imports": imports,
        "tesseract_languages": tesseract_languages,
        "storage_writable": os.access(os.getenv("APP_PERSISTENT_STORAGE", "/tmp"), os.W_OK),
    }


def analyze_file(input_path: Path) -> dict[str, object]:
    ext = input_path.suffix.lower()
    info: dict[str, object] = {
        "filename": input_path.name,
        "extension": ext,
        "size": input_path.stat().st_size if input_path.exists() else 0,
        "mimetype": mimetypes.guess_type(input_path.name)[0] or "application/octet-stream",
        "pages": None,
        "encrypted": False,
        "has_text": None,
        "needs_ocr": None,
    }
    if ext == ".pdf":
        try:
            import fitz

            with fitz.open(input_path) as doc:
                info["pages"] = doc.page_count
                info["encrypted"] = bool(doc.is_encrypted)
                text = ""
                if not doc.is_encrypted:
                    for page_index in range(min(doc.page_count, 3)):
                        text += doc[page_index].get_text("text")
                info["has_text"] = bool(text.strip())
                info["needs_ocr"] = not info["has_text"]
        except Exception as exc:
            info["analysis_error"] = str(exc)
    elif ext in IMAGE_EXTENSIONS:
        info["needs_ocr"] = True
        info["has_text"] = False
    return info


def to_pdf(input_path: Path, output_dir: Path, ocr_lang: str) -> Path:
    ext = input_path.suffix.lower()
    if ext == ".pdf":
        output = output_dir / input_path.name
        shutil.copy2(input_path, output)
        return output
    if ext in IMAGE_EXTENSIONS:
        return image_to_pdf(input_path, output_dir / f"{input_path.stem}.pdf", ocr_lang, searchable=False)
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
            raise ConversionError("В контейнере не установлен модуль pdf2docx для PDF -> DOCX.") from exc

        output = output_dir / f"{input_path.stem}.docx"
        converter = Converter(str(input_path))
        try:
            converter.convert(str(output), start=0, end=None)
        finally:
            converter.close()
        ensure_output(output)
        return output
    return pandoc_convert(input_path, output_dir / f"{input_path.stem}.docx")


def to_txt(input_path: Path, output_dir: Path, ocr_lang: str) -> Path:
    ext = input_path.suffix.lower()
    output = output_dir / f"{input_path.stem}.txt"
    if ext == ".pdf":
        text = pdf_text(input_path)
        if not text.strip():
            text = ocr_file_to_text(input_path, ocr_lang)
        output.write_text(text, encoding="utf-8")
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
    if ext == ".epub":
        html = to_html(input_path, output_dir)
        return pandoc_convert(html, output)
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
        txt = to_txt(input_path, output_dir, os.getenv("DOCUMENT_TOOLS_OCR_LANG", "rus+eng"))
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
        return image_to_pdf(input_path, output_dir / f"{input_path.stem}.searchable.pdf", ocr_lang, searchable=True)
    if ext != ".pdf":
        input_path = to_pdf(input_path, output_dir, ocr_lang)

    output = output_dir / f"{input_path.stem}.searchable.pdf"
    if shutil.which("ocrmypdf"):
        run(
            [
                "ocrmypdf",
                "--skip-text",
                "--rotate-pages",
                "--deskew",
                "--optimize",
                "1",
                "-l",
                tesseract_lang(ocr_lang),
                str(input_path),
                str(output),
            ],
            timeout=1800,
        )
        ensure_output(output)
        return output
    return paddle_searchable_pdf(input_path, output)


def image_to_pdf(input_path: Path, output: Path, ocr_lang: str, *, searchable: bool) -> Path:
    if searchable and shutil.which("tesseract"):
        base = output.with_suffix("")
        run(["tesseract", str(input_path), str(base), "-l", tesseract_lang(ocr_lang), "pdf"], timeout=900)
        generated = base.with_suffix(".pdf")
        if generated != output:
            generated.replace(output)
        ensure_output(output)
        return output

    try:
        import fitz
        from PIL import Image
    except Exception as exc:  # pragma: no cover
        raise ConversionError("Для создания PDF из изображения нужны PyMuPDF и Pillow.") from exc

    with Image.open(input_path) as image:
        width, height = image.size
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_image(page.rect, filename=str(input_path))
    doc.save(output, garbage=4, deflate=True)
    doc.close()
    ensure_output(output)
    return output


def pdf_text(input_path: Path) -> str:
    try:
        import fitz
    except Exception as exc:  # pragma: no cover
        raise ConversionError("В контейнере не установлен PyMuPDF для извлечения текста из PDF.") from exc

    with fitz.open(input_path) as doc:
        if doc.is_encrypted:
            raise ConversionError("PDF зашифрован. Сначала снимите пароль или выберите другой файл.")
        return "\n\n".join(page.get_text("text") for page in doc)


def ocr_file_to_text(input_path: Path, ocr_lang: str) -> str:
    ext = input_path.suffix.lower()
    if shutil.which("tesseract"):
        if ext == ".pdf":
            return ocr_pdf_to_text_with_tesseract(input_path, ocr_lang)
        return tesseract_image_to_text(input_path, ocr_lang)
    return paddle_file_to_text(input_path, ocr_lang)


def ocr_pdf_to_text_with_tesseract(input_path: Path, ocr_lang: str) -> str:
    try:
        import fitz
    except Exception as exc:  # pragma: no cover
        raise ConversionError("Для OCR PDF нужен PyMuPDF или OCRmyPDF.") from exc

    chunks: list[str] = []
    with fitz.open(input_path) as doc:
        for page_index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image_path = input_path.parent / f"ocr-page-{page_index + 1}.png"
            pix.save(image_path)
            try:
                chunks.append(tesseract_image_to_text(image_path, ocr_lang))
            finally:
                image_path.unlink(missing_ok=True)
    return "\n\n".join(chunk for chunk in chunks if chunk.strip())


def tesseract_image_to_text(input_path: Path, ocr_lang: str) -> str:
    output_base = input_path.parent / f"{input_path.stem}.ocr"
    output_txt = output_base.with_suffix(".txt")
    run(["tesseract", str(input_path), str(output_base), "-l", tesseract_lang(ocr_lang)], timeout=900)
    if not output_txt.exists():
        raise ConversionError("Tesseract не создал текстовый файл OCR.")
    try:
        return output_txt.read_text(encoding="utf-8", errors="ignore")
    finally:
        output_txt.unlink(missing_ok=True)


def paddle_file_to_text(input_path: Path, ocr_lang: str) -> str:
    try:
        import fitz
    except Exception as exc:  # pragma: no cover
        raise ConversionError("Для OCR нужен Tesseract или PyMuPDF + PaddleOCR.") from exc

    ocr = paddle_ocr(ocr_lang)
    chunks: list[str] = []
    if input_path.suffix.lower() == ".pdf":
        with fitz.open(input_path) as doc:
            for page_index, page in enumerate(doc):
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image_path = input_path.parent / f"ocr-page-{page_index + 1}.png"
                pix.save(image_path)
                chunks.append(paddle_image_to_text(ocr, image_path))
                image_path.unlink(missing_ok=True)
    else:
        chunks.append(paddle_image_to_text(ocr, input_path))
    return "\n\n".join(chunk for chunk in chunks if chunk.strip())


def paddle_image_to_text(ocr: object, image_path: Path) -> str:
    lines = [text for text, _box in iter_ocr_boxes(ocr, image_path)]
    return "\n".join(lines)


def paddle_searchable_pdf(input_path: Path, output: Path) -> Path:
    try:
        import fitz
        from PIL import Image
    except Exception as exc:  # pragma: no cover
        raise ConversionError(
            "В контейнере нет OCRmyPDF; fallback через PaddleOCR требует PyMuPDF и Pillow."
        ) from exc

    ocr = paddle_ocr(os.getenv("DOCUMENT_TOOLS_OCR_LANG", "rus+eng"))
    with fitz.open(input_path) as doc:
        for page_index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image_path = output.parent / f"page-{page_index + 1}.png"
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
    ensure_output(output)
    return output


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
        raise ConversionError("В контейнере не установлен PaddleOCR.") from exc
    return PaddleOCR(use_angle_cls=True, lang=paddle_lang(lang))


def html_to_pdf(input_path: Path, output: Path) -> Path:
    try:
        from weasyprint import HTML

        HTML(filename=str(input_path)).write_pdf(str(output))
        ensure_output(output)
        return output
    except Exception:
        return libreoffice_convert(input_path, output.parent, "pdf")


def epub_to_pdf(input_path: Path, output: Path) -> Path:
    if shutil.which("ebook-convert"):
        run(["ebook-convert", str(input_path), str(output)], timeout=900)
        ensure_output(output)
        return output
    return pandoc_convert(input_path, output)


def libreoffice_convert(input_path: Path, output_dir: Path, target_format: str) -> Path:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise ConversionError("В контейнере не установлен LibreOffice headless.")
    before = set(output_dir.iterdir())
    run(
        [
            soffice,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--convert-to",
            target_format,
            "--outdir",
            str(output_dir),
            str(input_path),
        ],
        timeout=900,
    )
    after = set(output_dir.iterdir())
    created = sorted(after - before, key=lambda path: path.stat().st_mtime, reverse=True)
    if created:
        ensure_output(created[0])
        return created[0]
    expected = output_dir / f"{input_path.stem}.{target_format.split(':', 1)[0]}"
    ensure_output(expected)
    return expected


def pandoc_convert(input_path: Path, output: Path) -> Path:
    if not shutil.which("pandoc"):
        raise ConversionError("В контейнере не установлен Pandoc.")
    run(["pandoc", str(input_path), "-o", str(output)], timeout=900)
    ensure_output(output)
    return output


def ensure_output(path: Path) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        raise ConversionError("Конвертер не создал выходной файл.")


def tesseract_lang(lang: str) -> str:
    normalized = (lang or "rus+eng").lower().replace("ru", "rus")
    if normalized in {"rus", "eng", "rus+eng", "eng+rus"}:
        return normalized
    if "rus" in normalized and "eng" in normalized:
        return "rus+eng"
    if "rus" in normalized:
        return "rus"
    return "eng"


def paddle_lang(lang: str) -> str:
    normalized = (lang or "ru").lower()
    if "rus" in normalized or normalized.startswith("ru"):
        return "ru"
    return "en"


def run(command: list[str], *, timeout: int = 600) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise ConversionError(humanize_error(detail)) from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"Обработка превысила лимит времени: {' '.join(command)}") from exc


def humanize_error(detail: str) -> str:
    if not detail:
        return "Конвертер завершился с ошибкой без подробностей."
    lowered = detail.lower()
    if "not utf-8 encoded" in lowered:
        return "Выбранная операция не подходит для этого файла. Для изображений используйте OCR TXT или PDF с OCR."
    if "command not found" in lowered or "no such file" in lowered:
        return "В контейнере отсутствует нужный системный инструмент для этой операции."
    return detail[-1200:]
