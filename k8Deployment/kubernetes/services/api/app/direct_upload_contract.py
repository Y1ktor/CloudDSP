"""Pure HTTP contracts for the future authenticated direct-upload route.

This module deliberately has no FastAPI route decorator, PostgreSQL query,
MinIO client, RabbitMQ publisher, or clock-based side effect. It is the first
small piece of POST /jobs: a precise agreement about what the React browser may
submit and what a later successful response must contain.

Keeping this contract independent from storage and database code means unit
tests can prove malformed browser input fails before the future route creates
durable state or asks boto3 to sign a form. The database module now owns the
following PostgreSQL transaction; a later route task will call the already-
tested presigned upload helper only after that transaction succeeds.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# These product constants match the preserved cloud API and the existing React
# client. They are shared with the MinIO policy-signing helper so the browser,
# request validator, and S3 form cannot accidentally disagree about a limit or
# valid stem choice.
MAX_SOURCE_UPLOAD_BYTES = 256 * 1024 * 1024
VALID_DIRECT_UPLOAD_STEM_MODES = frozenset({"2-stems", "4-stems", "6-stems"})

# Browser MIME detection varies across operating systems and audio containers.
# The filename extension establishes the canonical durable type, while this
# table lists the alternate browser-reported types CloudDSP accepts for each
# format. A later worker must still inspect/decode the uploaded bytes before
# passing them to Demucs; a MIME label is not proof that bytes are safe audio.
_SUPPORTED_AUDIO_MEDIA_TYPES: dict[str, tuple[str, frozenset[str]]] = {
    ".wav": ("audio/wav", frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"})),
    ".mp3": ("audio/mpeg", frozenset({"audio/mpeg", "audio/mp3"})),
    ".flac": ("audio/flac", frozenset({"audio/flac", "audio/x-flac"})),
    ".m4a": ("audio/mp4", frozenset({"audio/mp4", "audio/x-m4a", "audio/m4a"})),
    ".aac": ("audio/aac", frozenset({"audio/aac", "audio/x-aac"})),
    ".ogg": ("audio/ogg", frozenset({"audio/ogg", "application/ogg"})),
    ".opus": ("audio/ogg", frozenset({"audio/ogg", "audio/opus", "application/ogg"})),
    ".aiff": ("audio/aiff", frozenset({"audio/aiff", "audio/x-aiff"})),
    ".aif": ("audio/aiff", frozenset({"audio/aiff", "audio/x-aiff"})),
    ".webm": ("audio/webm", frozenset({"audio/webm"})),
}

DirectUploadStemMode = Literal["2-stems", "4-stems", "6-stems"]
UploadPendingStatus = Literal["upload_pending"]


class DirectUploadJobRequest(BaseModel):
    """A browser's intent to create exactly one direct-upload processing job.

    It has no owner field: the future route will obtain that immutable identity
    exclusively from the Keycloak authentication dependency. It also has no
    bucket, key, status, revision, or presigned-field input, because allowing a
    browser to choose those server-controlled values would bypass durable-job
    ownership and MinIO policy boundaries.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    filename: str
    # Null, empty, and generic octet-stream are accepted because browsers often
    # omit reliable audio MIME information. The filename extension below still
    # determines a canonical durable type before a future S3 policy is signed.
    content_type: str | None = None
    size_bytes: int = Field(ge=1, le=MAX_SOURCE_UPLOAD_BYTES)
    stem_mode: DirectUploadStemMode = "6-stems"

    @field_validator("filename")
    @classmethod
    def filename_must_be_one_safe_basename(cls, value: str) -> str:
        """Reject paths/traversal before a later route creates an object key."""

        filename = PurePath(value).name
        if (
            not value.strip()
            or filename in {"", ".", ".."}
            or filename != value
            or "\\" in value
            or "\x00" in value
            or len(filename) > 255
        ):
            raise ValueError("filename must be one non-empty basename up to 255 characters.")
        return filename

    @field_validator("content_type")
    @classmethod
    def normalize_browser_content_type(cls, value: str | None) -> str | None:
        """Normalize a MIME parameter away without yet choosing a file format."""

        if value is None:
            return None
        if len(value) > 255:
            raise ValueError("content_type must be at most 255 characters.")

        # Browsers may report values like audio/wav; codecs=1. S3 will later
        # require the canonical simple MIME type, never the browser parameter.
        normalized = value.split(";", 1)[0].strip().lower()
        if normalized in {"", "application/octet-stream"}:
            return None
        return normalized

    @model_validator(mode="after")
    def filename_and_content_type_must_describe_supported_audio(self) -> "DirectUploadJobRequest":
        """Enforce the preserved cloud extension/MIME allowlist as one pair."""

        extension = PurePath(self.filename).suffix.lower()
        supported_type = _SUPPORTED_AUDIO_MEDIA_TYPES.get(extension)
        if supported_type is None:
            raise ValueError(
                "Supported audio files are WAV, MP3, FLAC, M4A, AAC, OGG, Opus, AIFF, and WebM."
            )

        _, accepted_browser_types = supported_type
        if self.content_type is not None and self.content_type not in accepted_browser_types:
            raise ValueError(f"{extension} must be uploaded with an audio content type.")
        return self

    @property
    def canonical_source_content_type(self) -> str:
        """Return the exact type the later MinIO policy and jobs row will store."""

        # Model validation above proves this extension has one entry, so this
        # assertion cannot be reached through valid public construction.
        canonical_type, _ = _SUPPORTED_AUDIO_MEDIA_TYPES[PurePath(self.filename).suffix.lower()]
        return canonical_type


class DirectUploadJobCreatedResponse(BaseModel):
    """The complete browser response a future successful POST /jobs returns.

    The response has no input bucket/key, access key, MinIO root credential, or
    server-side error. Its short-lived upload fields are intentionally returned
    only to the authenticated browser and must never be stored in PostgreSQL or
    application logs. The browser appends its File in a file form field after
    copying every provided field unchanged.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    job_id: str
    status: UploadPendingStatus
    revision: int = Field(ge=1)
    expires_at: datetime
    upload_url: str = Field(min_length=1, max_length=2_048)
    upload_fields: dict[str, str] = Field(min_length=1)
    max_source_bytes: int = Field(ge=1, le=MAX_SOURCE_UPLOAD_BYTES)

    @field_validator("job_id")
    @classmethod
    def job_id_must_be_a_canonical_uuid(cls, value: str) -> str:
        """Avoid returning an arbitrary identifier where a database UUID is expected."""

        from uuid import UUID

        try:
            normalized = str(UUID(value))
        except (ValueError, AttributeError) as error:
            raise ValueError("job_id must be a UUID string.") from error
        if normalized != value:
            raise ValueError("job_id must use canonical lowercase UUID form.")
        return value

    @field_validator("expires_at")
    @classmethod
    def expires_at_must_have_a_timezone(cls, value: datetime) -> datetime:
        """Make browser-visible retention expiry unambiguous across time zones."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone offset.")
        return value

    @field_validator("upload_fields")
    @classmethod
    def upload_fields_must_not_override_the_browser_file(cls, value: dict[str, str]) -> dict[str, str]:
        """Reserve the multipart file field for the browser-selected audio."""

        if "file" in value:
            raise ValueError("upload_fields must not include the browser file field.")
        return value
