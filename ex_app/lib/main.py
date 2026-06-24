"""FastAPI entrypoint for Nextcloud Document Tools."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from nc_py_api import NextcloudApp
from nc_py_api.ex_app import AppAPIAuthMiddleware, LogLvl, nc_app, run_app, set_handlers

from document_tools import APP_ID, APP_NAME
from document_tools.converter import ConversionError, convert_document
from document_tools.models import FileActionPayload, Job, JobStatus, OutputFormat, SaveRequest
from document_tools.storage import job_dir, safe_upload_name, unique_path

STATIC_ROOT = Path(__file__).parent / "document_tools" / "static"
OCR_LANG = os.getenv("DOCUMENT_TOOLS_OCR_LANG", "ru")
MAX_WORKERS = int(os.getenv("DOCUMENT_TOOLS_MAX_WORKERS", "1"))
SUPPORTED_ACTION_MIME = ",".join(
    [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/html",
        "text/markdown",
        "application/epub+zip",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/webp",
    ]
)

jobs: dict[str, Job] = {}
jobs_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=max(1, MAX_WORKERS), thread_name_prefix="document-tools")


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_handlers(app, enabled_handler)
    yield
    executor.shutdown(wait=False, cancel_futures=False)


APP = FastAPI(title=APP_NAME, lifespan=lifespan)
if os.getenv("DOCUMENT_TOOLS_DISABLE_APPAPI_AUTH") != "1":
    APP.add_middleware(AppAPIAuthMiddleware)

APP.mount("/js", StaticFiles(directory=STATIC_ROOT / "js"), name="js")
APP.mount("/css", StaticFiles(directory=STATIC_ROOT / "css"), name="css")
APP.mount("/img", StaticFiles(directory=STATIC_ROOT / "img"), name="img")


def enabled_handler(enabled: bool, nc: NextcloudApp) -> str:
    try:
        if enabled:
            nc.ui.resources.set_script("top_menu", "main", "js/document_tools-main")
            nc.ui.resources.set_style("top_menu", "main", "css/document_tools")
            nc.ui.top_menu.register("main", APP_NAME, "img/app.svg")
            nc.ui.files_dropdown_menu.register_ex(
                "convert_document",
                "Конвертировать документ",
                "api/file-action",
                mime=SUPPORTED_ACTION_MIME,
                icon="img/app.svg",
            )
            nc.log(LogLvl.INFO, f"{APP_NAME} enabled")
        else:
            nc.ui.files_dropdown_menu.unregister("convert_document")
            nc.ui.top_menu.unregister("main")
            nc.ui.resources.delete_style("top_menu", "main", "css/document_tools")
            nc.ui.resources.delete_script("top_menu", "main", "js/document_tools-main")
            nc.log(LogLvl.INFO, f"{APP_NAME} disabled")
    except Exception as exc:
        return str(exc)
    return ""


@APP.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": APP_ID}


@APP.get("/api/formats")
async def formats() -> dict[str, list[dict[str, str]]]:
    return {
        "formats": [
            {"id": OutputFormat.SEARCHABLE_PDF, "label": "PDF с OCR", "hint": "Searchable PDF"},
            {"id": OutputFormat.PDF, "label": "PDF", "hint": "Документ PDF"},
            {"id": OutputFormat.DOCX, "label": "DOCX", "hint": "Word-документ"},
            {"id": OutputFormat.TXT, "label": "TXT", "hint": "Обычный текст"},
            {"id": OutputFormat.MARKDOWN, "label": "Markdown", "hint": "MD"},
            {"id": OutputFormat.HTML, "label": "HTML", "hint": "Веб-страница"},
            {"id": OutputFormat.EPUB, "label": "EPUB", "hint": "Электронная книга"},
        ]
    }


@APP.post("/api/jobs", status_code=202)
async def create_job(
    output_format: Annotated[OutputFormat, Form()],
    file: Annotated[UploadFile, File()],
) -> Job:
    job = _create_job(file.filename or "document", output_format, "upload")
    directory = job_dir(job.id)
    input_path = unique_path(directory, file.filename or "document")
    with input_path.open("wb") as target:
        while chunk := await file.read(1024 * 1024):
            target.write(chunk)
    job.input_path = str(input_path)
    _store_job(job)
    executor.submit(_process_job, job.id)
    return job


@APP.post("/api/file-action")
async def file_action(payload: FileActionPayload) -> JSONResponse:
    _ = payload
    return JSONResponse(content={"redirect_handler": "main"})


@APP.get("/api/jobs")
async def list_jobs() -> dict[str, list[Job]]:
    with jobs_lock:
        sorted_jobs = sorted(jobs.values(), key=lambda job: job.created_at, reverse=True)
    return {"jobs": sorted_jobs}


@APP.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> Job:
    return _get_job(job_id)


@APP.get("/api/jobs/{job_id}/download")
async def download_job(job_id: str) -> FileResponse:
    job = _get_job(job_id)
    if job.status != JobStatus.DONE or not job.output_file or not job.output_file.exists():
        raise HTTPException(status_code=404, detail="Result is not ready")
    return FileResponse(
        job.output_file,
        filename=job.output_filename or job.output_file.name,
        media_type="application/octet-stream",
    )


@APP.post("/api/jobs/{job_id}/save")
async def save_job(
    job_id: str,
    request: SaveRequest,
    nc: Annotated[NextcloudApp, Depends(nc_app)],
) -> JSONResponse:
    job = _get_job(job_id)
    if job.status != JobStatus.DONE:
        raise HTTPException(status_code=409, detail="Job is not complete")
    if not job.output_file or not job.output_file.exists():
        raise HTTPException(status_code=404, detail="Result file is missing")

    nc.log(LogLvl.WARNING, f"Save mode requested but not wired yet: {request.mode}")
    raise HTTPException(
        status_code=501,
        detail=(
            "Saving back to Nextcloud requires live AppAPI/WebDAV integration. "
            "The endpoint is reserved and currently returns downloadable results."
        ),
    )


def _create_job(filename: str, output_format: OutputFormat, source: str) -> Job:
    operation = "OCR" if output_format == OutputFormat.SEARCHABLE_PDF else "Конвертация"
    directory = job_dir("pending")
    input_path = directory / safe_upload_name(filename)
    return Job(
        filename=safe_upload_name(filename),
        source=source,
        output_format=output_format,
        operation=operation,
        input_path=str(input_path),
    )


def _store_job(job: Job) -> None:
    job.touch()
    with jobs_lock:
        jobs[job.id] = job


def _get_job(job_id: str) -> Job:
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _process_job(job_id: str) -> None:
    job = _get_job(job_id)
    try:
        job.status = JobStatus.RUNNING
        job.progress = 10
        _store_job(job)

        output_dir = job_dir(job.id) / "output"
        result = convert_document(Path(job.input_path), job.output_format, output_dir, ocr_lang=OCR_LANG)

        job.output_path = str(result)
        job.output_filename = result.name
        job.status = JobStatus.DONE
        job.progress = 100
        _store_job(job)
    except ConversionError as exc:
        _fail_job(job, str(exc))
    except Exception as exc:  # pragma: no cover - safety net for background jobs
        _fail_job(job, f"Unexpected conversion error: {exc}")


def _fail_job(job: Job, message: str) -> None:
    job.status = JobStatus.FAILED
    job.progress = 100
    job.error = message
    _store_job(job)


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    run_app("main:APP", log_level=os.getenv("DOCUMENT_TOOLS_LOG_LEVEL", "info"))
