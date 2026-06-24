"""FastAPI entrypoint for Nextcloud Document Tools."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from nc_py_api import NextcloudApp
from nc_py_api.ex_app import AppAPIAuthMiddleware, LogLvl, nc_app, run_app, set_handlers

from document_tools import APP_ID, APP_NAME
from document_tools.converter import ConversionError, analyze_file, convert_document, diagnostics
from document_tools.models import FileActionPayload, Job, JobStatus, NextcloudJobRequest, OutputFormat, SaveRequest
from document_tools.storage import job_dir, safe_upload_name, unique_path

STATIC_ROOT = Path(__file__).parent / "document_tools" / "static"
OCR_LANG = os.getenv("DOCUMENT_TOOLS_OCR_LANG", "rus+eng")
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


@APP.get("/api/diagnostics")
async def get_diagnostics() -> dict[str, object]:
    return diagnostics()


@APP.post("/api/notifications/test")
async def test_notification(nc: Annotated[NextcloudApp, Depends(nc_app)]) -> dict[str, str]:
    try:
        object_id = nc.notifications.create(
            "Nextcloud Document Tools: тест",
            "Уведомления ExApp настроены корректно.",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Не удалось создать уведомление: {exc}") from exc
    return {"status": "ok", "object_id": object_id}


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
    nc: Annotated[NextcloudApp, Depends(nc_app)],
) -> Job:
    job = _create_job(file.filename or "document", output_format, "upload")
    directory = job_dir(job.id)
    input_path = unique_path(directory, file.filename or "document")
    with input_path.open("wb") as target:
        while chunk := await file.read(1024 * 1024):
            target.write(chunk)
    job.input_path = str(input_path)
    job.metadata["analysis"] = analyze_file(input_path)
    _store_job(job)
    executor.submit(_process_job, job.id, nc)
    return job


@APP.post("/api/jobs/upload", status_code=202)
async def create_upload_job(
    request: Request,
    output_format: Annotated[OutputFormat, Query()],
    filename: Annotated[str, Query(min_length=1)],
    nc: Annotated[NextcloudApp, Depends(nc_app)],
) -> Job:
    job = _create_job(filename, output_format, "upload")
    directory = job_dir(job.id)
    input_path = unique_path(directory, filename)
    total_size = 0
    with input_path.open("wb") as target:
        async for chunk in request.stream():
            if chunk:
                total_size += len(chunk)
                target.write(chunk)
    if total_size <= 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    job.input_path = str(input_path)
    job.metadata["size"] = total_size
    job.metadata["analysis"] = analyze_file(input_path)
    _store_job(job)
    executor.submit(_process_job, job.id, nc)
    return job


@APP.post("/api/jobs/from-nextcloud", status_code=202)
async def create_nextcloud_job(
    payload: NextcloudJobRequest,
    nc: Annotated[NextcloudApp, Depends(nc_app)],
) -> Job:
    node = nc.files.by_id(payload.file_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Nextcloud file not found")
    if node.is_dir:
        raise HTTPException(status_code=400, detail="Select a file, not a folder")

    job = _create_job(node.name, payload.output_format, "nextcloud")
    directory = job_dir(job.id)
    input_path = unique_path(directory, node.name)
    with input_path.open("wb") as target:
        nc.files.download2stream(node, target)
    job.input_path = str(input_path)
    job.metadata["nextcloud_file"] = _node_to_dict(node)
    job.metadata["analysis"] = analyze_file(input_path)
    _store_job(job)
    executor.submit(_process_job, job.id, nc)
    return job


@APP.post("/api/file-action")
async def file_action(payload: FileActionPayload) -> JSONResponse:
    _ = payload
    return JSONResponse(content={"redirect_handler": "main"})


@APP.get("/api/nextcloud/files")
async def list_nextcloud_files(
    nc: Annotated[NextcloudApp, Depends(nc_app)],
    path: str = "",
) -> dict[str, object]:
    try:
        nodes = nc.files.listdir(path, depth=1, exclude_self=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot list Nextcloud folder: {exc}") from exc
    items = [_node_to_dict(node) for node in nodes]
    items.sort(key=lambda item: (not item["is_dir"], str(item["name"]).lower()))
    return {"path": path.strip("/"), "items": items}


@APP.get("/api/nextcloud/files/by-id/{file_id}")
async def get_nextcloud_file(
    file_id: int,
    nc: Annotated[NextcloudApp, Depends(nc_app)],
) -> dict[str, object]:
    node = nc.files.by_id(file_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Nextcloud file not found")
    return _node_to_dict(node)


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


def _node_to_dict(node) -> dict[str, object]:
    return {
        "file_id": node.info.fileid,
        "fileId": node.info.fileid,
        "name": node.name,
        "path": node.user_path.rstrip("/"),
        "is_dir": node.is_dir,
        "mimetype": node.info.mimetype,
        "size": node.info.size,
        "permissions": node.info.permissions,
    }


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


def _process_job(job_id: str, nc: NextcloudApp | None = None) -> None:
    job = _get_job(job_id)
    try:
        job.status = JobStatus.RUNNING
        job.stage = "preflight"
        job.progress = 10
        job.log.append("Проверка файла и зависимостей")
        _store_job(job)

        job.stage = "processing"
        job.progress = 35
        job.log.append("Запуск конвертации")
        _store_job(job)

        output_dir = job_dir(job.id) / "output"
        result = convert_document(Path(job.input_path), job.output_format, output_dir, ocr_lang=OCR_LANG)

        job.output_path = str(result)
        job.output_filename = result.name
        job.status = JobStatus.DONE
        job.stage = "done"
        job.progress = 100
        job.log.append(f"Готово: {result.name}")
        _store_job(job)
        _notify_job(nc, job, success=True)
    except ConversionError as exc:
        _fail_job(job, str(exc), nc)
    except Exception as exc:  # pragma: no cover - safety net for background jobs
        _fail_job(job, f"Непредвиденная ошибка конвертации: {exc}", nc)


def _fail_job(job: Job, message: str, nc: NextcloudApp | None = None) -> None:
    job.status = JobStatus.FAILED
    job.stage = "failed"
    job.progress = 100
    job.error = _safe_user_text(message)
    job.log.append(job.error)
    _store_job(job)
    _notify_job(nc, job, success=False)


def _notify_job(nc: NextcloudApp | None, job: Job, *, success: bool) -> None:
    if nc is None:
        return
    subject = "Nextcloud Document Tools: задача завершена" if success else "Nextcloud Document Tools: ошибка"
    message = f"Файл: {job.filename}. Результат: {job.output_filename or job.error or job.stage}."
    try:
        nc.notifications.create(_safe_notification_text(subject), _safe_notification_text(message))
    except Exception as exc:
        job.log.append(f"Не удалось создать уведомление Nextcloud: {exc}")
        _store_job(job)


def _safe_notification_text(value: str) -> str:
    return _safe_user_text(value).replace("%", "%%")


def _safe_user_text(value: str) -> str:
    return " ".join(str(value).split())[:1200]


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    run_app("main:APP", log_level=os.getenv("DOCUMENT_TOOLS_LOG_LEVEL", "info"))
