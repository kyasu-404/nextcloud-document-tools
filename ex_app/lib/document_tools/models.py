"""Shared API models for the document tools backend."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class OutputFormat(str, Enum):
    SEARCHABLE_PDF = "searchable_pdf"
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    MARKDOWN = "markdown"
    HTML = "html"
    EPUB = "epub"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class SaveMode(str, Enum):
    DOWNLOAD = "download"
    SAVE_BACK = "save_back"
    REPLACE_ORIGINAL = "replace_original"
    SAVE_TO_FOLDER = "save_to_folder"


class Job(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    filename: str
    source: str = "upload"
    output_format: OutputFormat
    operation: str
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    input_path: str
    output_path: str | None = None
    output_filename: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    @property
    def output_file(self) -> Path | None:
        return Path(self.output_path) if self.output_path else None


class FileActionPayload(BaseModel):
    files: list[dict[str, Any]] | None = None


class NextcloudJobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_id: int = Field(alias="fileId")
    output_format: OutputFormat


class SaveRequest(BaseModel):
    mode: SaveMode
    folder: str | None = None
