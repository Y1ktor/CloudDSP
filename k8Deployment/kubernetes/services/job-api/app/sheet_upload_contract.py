"""Browser contract for the independent MIDI-to-sheet upload workflow.

The browser supplies only a file description and direction. Keycloak owns the
subject; PostgreSQL and the API own the job ID, object key, and state. A MIDI MIME label is only a hint: score intake must inspect the actual bytes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# The separate limit bounds the MIDI engraving storage and parsing
# exposure. Timing, track count and note limits require byte inspection in the worker.
MAX_SHEET_SOURCE_BYTES = 10 * 1024 * 1024
SHEET_MEDIA_TYPES = {
    ".mid": ("audio/midi", frozenset({"audio/midi", "audio/x-midi", "application/x-midi"})),
    ".midi": ("audio/midi", frozenset({"audio/midi", "audio/x-midi", "application/x-midi"})),
}


class SheetUploadRequest(BaseModel):
    """One authenticated score-to-MIDI upload intent, with no storage controls."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    direction: Literal["midi_to_sheet"]
    filename: str
    content_type: str | None = None
    size_bytes: int = Field(ge=1, le=MAX_SHEET_SOURCE_BYTES)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if (
            not value.strip()
            or value != PurePath(value).name
            or value in {".", ".."}
            or "\\" in value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or len(value) > 255
        ):
            raise ValueError("filename must be one non-empty basename up to 255 characters.")
        return value

    @field_validator("content_type")
    @classmethod
    def normalize_content_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > 255:
            raise ValueError("content_type must be at most 255 characters.")
        normalized = value.split(";", 1)[0].strip().lower()
        return None if normalized in {"", "application/octet-stream"} else normalized

    @model_validator(mode="after")
    def supported_score_type(self) -> "SheetUploadRequest":
        supported = SHEET_MEDIA_TYPES.get(PurePath(self.filename).suffix.lower())
        if supported is None:
            raise ValueError("Choose a MID or MIDI file.")
        if self.content_type is not None and self.content_type not in supported[1]:
            raise ValueError("filename and content_type must describe the same sheet format.")
        return self

    @property
    def extension(self) -> str:
        return ".mid"

    @property
    def canonical_content_type(self) -> str:
        return SHEET_MEDIA_TYPES[self.extension][0]


class SheetUploadCreatedResponse(BaseModel):
    """Short-lived form returned only to the authenticated submitting browser."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    job_id: str
    direction: Literal["midi_to_sheet"]
    status: Literal["upload_pending"]
    revision: int = Field(ge=1)
    expires_at: datetime
    upload_url: str = Field(min_length=1, max_length=2048)
    upload_fields: dict[str, str] = Field(min_length=1)
    max_source_bytes: int = Field(ge=1, le=MAX_SHEET_SOURCE_BYTES)

    @field_validator("job_id")
    @classmethod
    def canonical_job_id(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("job_id must be a canonical UUID.")
        return value

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone offset.")
        return value

    @field_validator("upload_fields")
    @classmethod
    def browser_owns_file_field(cls, value: dict[str, str]) -> dict[str, str]:
        if "file" in value:
            raise ValueError("upload_fields must not include file.")
        return value
