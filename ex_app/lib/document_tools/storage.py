"""Storage helpers for uploaded files, generated results, and transient jobs."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4


def app_storage_root() -> Path:
    root = os.getenv("APP_PERSISTENT_STORAGE")
    if root:
        path = Path(root)
    else:
        path = Path(os.getenv("DOCUMENT_TOOLS_STORAGE", "/tmp/document-tools"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_dir(job_id: str) -> Path:
    path = app_storage_root() / "jobs" / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_upload_name(filename: str) -> str:
    cleaned = Path(filename).name.strip().replace("\x00", "")
    return cleaned or f"upload-{uuid4().hex}"


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / safe_upload_name(filename)
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 1000):
        next_candidate = directory / f"{stem}-{index}{suffix}"
        if not next_candidate.exists():
            return next_candidate
    return directory / f"{stem}-{uuid4().hex}{suffix}"
