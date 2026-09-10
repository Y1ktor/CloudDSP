"""Private MinIO HeadObject verification for one pending direct-upload job.

An S3-compatible ObjectCreated notification is only a delivery hint.  This
module proves that MinIO currently holds the same object PostgreSQL expects
before the future consumer may change durable job state.  It uses HeadObject,
which returns headers/metadata without downloading audio bytes.

The module deliberately has no RabbitMQ acknowledgement, PostgreSQL query,
container entrypoint, or Kubernetes API call.  A later composition task will
call it between the restricted database lookup and conditional state update.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast
from urllib.parse import urlparse

from app.database_transition import PendingDirectUpload, PermanentSourceFailureCategory


# This is the preserved product maximum for the encoded source object.  A
# browser-provided size and MinIO notification size are only hints; the S3
# HeadObject ContentLength checked below is the storage-side enforcement point.
MAX_SOURCE_SIZE_BYTES = 256 * 1024 * 1024
DEFAULT_MINIO_INTERNAL_ENDPOINT = "http://clouddsp-minio.clouddsp-data.svc:9000"
DEFAULT_S3_REGION = "us-east-1"
DEFAULT_UPLOADS_BUCKET = "clouddsp-uploads"


class HeadObjectClient(Protocol):
    """The one S3-client method required by this focused verifier."""

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return S3-compatible headers/metadata without reading object bytes."""


class ObjectStorageConfigurationError(RuntimeError):
    """Raise a safe category for missing or unsafe private S3 configuration."""


class ObjectStorageUnavailable(RuntimeError):
    """Raise a retryable category for MinIO transport/service failures.

    The message intentionally excludes a driver diagnostic, endpoint, bucket,
    object key, and credential. A later queue consumer will retry this class
    rather than marking a potentially healthy source object as failed.
    """


class ObjectStorageProtocolError(RuntimeError):
    """Raise when MinIO returns a structurally unusable HeadObject response."""


class PermanentSourceVerificationError(RuntimeError):
    """Describe one safe, non-retryable mismatch with a durable job contract."""

    def __init__(self, category: PermanentSourceFailureCategory) -> None:
        self.category = category
        # The enum value is a small reviewed category rather than raw object
        # metadata or an SDK exception. It is safe to persist later as a job
        # error_message, but callers must still avoid logging keys or headers.
        super().__init__(category.value)


@dataclass(frozen=True)
class ObjectStorageSettings:
    """Private S3 settings injected into the future upload-intake Pod.

    Both access key and secret key are excluded from ``repr``. They arrive from
    the ignored `clouddsp-upload-intake-minio-credentials` Secret, whereas
    endpoint, bucket, region, and addressing style are ordinary reviewed
    Deployment configuration.
    """

    internal_endpoint: str
    bucket_name: str
    region_name: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    addressing_style: str = "path"

    @classmethod
    def from_environment(cls) -> "ObjectStorageSettings":
        """Build settings without placing passwords or endpoints in source code."""

        endpoint = os.environ.get(
            "UPLOAD_INTAKE_S3_INTERNAL_ENDPOINT",
            DEFAULT_MINIO_INTERNAL_ENDPOINT,
        )
        _validate_private_endpoint(endpoint)
        bucket_name = _required_environment_text(
            "UPLOAD_INTAKE_S3_UPLOADS_BUCKET",
            default=DEFAULT_UPLOADS_BUCKET,
        )
        region_name = _required_environment_text(
            "UPLOAD_INTAKE_S3_REGION",
            default=DEFAULT_S3_REGION,
        )
        addressing_style = _required_environment_text(
            "UPLOAD_INTAKE_S3_ADDRESSING_STYLE",
            default="path",
        )
        if addressing_style != "path":
            # The local browser and Pods use one MinIO host; virtual bucket
            # subdomains are neither routed by Traefik nor part of this design.
            raise ObjectStorageConfigurationError("UPLOAD_INTAKE_S3_ADDRESSING_STYLE must be path.")
        return cls(
            internal_endpoint=endpoint,
            bucket_name=bucket_name,
            region_name=region_name,
            access_key=_required_environment_text("UPLOAD_INTAKE_S3_ACCESS_KEY"),
            secret_key=_required_environment_text("UPLOAD_INTAKE_S3_SECRET_KEY"),
            addressing_style=addressing_style,
        )


@dataclass(frozen=True)
class VerifiedSourceObject:
    """The minimal storage facts confirmed before a durable state transition.

    This internal result contains no object body, access key, secret key,
    presigned URL, AMQP message, or user identity. The future consumer needs
    it only as evidence that its previous HeadObject comparison succeeded.
    """

    bucket_name: str
    object_key: str
    content_type: str
    size_bytes: int


def _required_environment_text(name: str, *, default: str | None = None) -> str:
    """Read one non-empty configuration/Secret value without logging it."""

    value = os.environ.get(name, default)
    if value is None or not isinstance(value, str) or not value or "\x00" in value:
        raise ObjectStorageConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _validate_private_endpoint(endpoint: str) -> None:
    """Require an HTTP(S) endpoint that is not the browser-facing localhost host."""

    if not isinstance(endpoint, str) or not endpoint or "\x00" in endpoint:
        raise ObjectStorageConfigurationError("UPLOAD_INTAKE_S3_INTERNAL_ENDPOINT must be non-empty text.")
    parsed = urlparse(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ObjectStorageConfigurationError("UPLOAD_INTAKE_S3_INTERNAL_ENDPOINT is invalid.")
    # The `.localhost` host is exposed for a Mac browser through Traefik. A Pod
    # must use private ClusterIP Service DNS instead, so it cannot accidentally
    # depend on host-port routing or make object traffic browser-visible.
    if parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost"):
        raise ObjectStorageConfigurationError("UPLOAD_INTAKE_S3_INTERNAL_ENDPOINT must be private Service DNS.")


def create_boto3_head_object_client(settings: ObjectStorageSettings) -> HeadObjectClient:
    """Create the future runtime's path-style MinIO client lazily.

    Boto3/botocore are imported only here. Focused unit tests can inject a tiny
    fake HeadObject client without installing an SDK or opening any network
    connection; the later image task installs the pinned requirements lock and
    calls this factory in the long-running consumer process.
    """

    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise ObjectStorageConfigurationError("Pinned S3 client dependency is unavailable.") from error

    return cast(
        HeadObjectClient,
        boto3.client(
            "s3",
            endpoint_url=settings.internal_endpoint,
            region_name=settings.region_name,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            config=Config(s3={"addressing_style": settings.addressing_style}),
        ),
    )


def _error_code(error: BaseException) -> str | None:
    """Extract an S3-style error code without importing a specific SDK class."""

    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    error_mapping = response.get("Error")
    if not isinstance(error_mapping, Mapping):
        return None
    code = error_mapping.get("Code")
    return code if isinstance(code, str) else None


def _head_object_or_raise(
    client: HeadObjectClient,
    *,
    bucket_name: str,
    object_key: str,
) -> Mapping[str, object]:
    """Perform HeadObject and classify absent versus retryable storage errors."""

    try:
        response = client.head_object(Bucket=bucket_name, Key=object_key)
    except Exception as error:  # SDKs expose several concrete transport types.
        # A completed ObjectCreated notification followed by a definite 404
        # means the object cannot satisfy its pending job contract. Other S3
        # errors may be an outage, authorization regression, or transport fault
        # and must be retried rather than persisted as an invalid upload.
        if _error_code(error) in {"404", "NoSuchKey", "NoSuchObject", "NotFound"}:
            raise PermanentSourceVerificationError(
                PermanentSourceFailureCategory.OBJECT_MISSING
            ) from error
        raise ObjectStorageUnavailable("MinIO HeadObject is temporarily unavailable.") from error

    if not isinstance(response, Mapping):
        raise ObjectStorageProtocolError("MinIO returned an invalid HeadObject response.")
    return response


def _metadata_value(metadata: object, *, name: str) -> str:
    """Read exactly one case-insensitive user-metadata value from MinIO."""

    if not isinstance(metadata, Mapping):
        raise ObjectStorageProtocolError("MinIO returned invalid object metadata.")
    matches: list[str] = []
    for raw_name, raw_value in metadata.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise ObjectStorageProtocolError("MinIO returned invalid object metadata.")
        if raw_name.lower() == name:
            matches.append(raw_value)
    if len(matches) != 1 or not matches[0] or "\x00" in matches[0]:
        raise ObjectStorageProtocolError("MinIO returned invalid object metadata.")
    return matches[0]


def _positive_content_length(response: Mapping[str, object]) -> int:
    """Return a valid bounded storage-side size or a permanent safe category."""

    content_length = response.get("ContentLength")
    if isinstance(content_length, bool) or not isinstance(content_length, int) or content_length < 1:
        raise ObjectStorageProtocolError("MinIO returned an invalid ContentLength.")
    if content_length > MAX_SOURCE_SIZE_BYTES:
        raise PermanentSourceVerificationError(
            PermanentSourceFailureCategory.SIZE_LIMIT_EXCEEDED
        )
    return content_length


def verify_pending_direct_upload_head_object(
    client: HeadObjectClient,
    *,
    pending: PendingDirectUpload,
    settings: ObjectStorageSettings,
) -> VerifiedSourceObject:
    """Verify one pending database row against MinIO without downloading audio.

    The caller obtains ``pending`` only from ``find_pending_direct_upload``.
    This verifier first ensures it is configured for that one durable bucket,
    then issues exactly one private HeadObject request for the stored key. It
    compares canonical Job API metadata rather than trusting the AMQP event's
    size, ETag, or filename.

    A successful return permits the later transaction composition to call
    ``mark_verified_source_uploaded_and_enqueue_demucs``. A permanent mismatch
    carries a bounded category for ``mark_permanently_invalid_source``. A
    storage outage raises ``ObjectStorageUnavailable`` so the future consumer
    can use RabbitMQ's bounded retry path without corrupting job state.
    """

    if not isinstance(pending, PendingDirectUpload):
        raise TypeError("pending must be a PendingDirectUpload.")
    if not isinstance(settings, ObjectStorageSettings):
        raise TypeError("settings must be ObjectStorageSettings.")
    if pending.input_bucket != settings.bucket_name:
        # Do not make a cross-bucket request even if a future caller somehow
        # manufactures an object. This role's MinIO policy is also prefix and
        # bucket limited, but the application boundary should fail first.
        raise ObjectStorageProtocolError("Pending job uses an unexpected input bucket.")
    if pending.source_content_type is None or not pending.source_content_type:
        # Direct browser uploads have a canonical type from the Job API. A
        # missing durable value is a database-contract failure, not evidence
        # that the object itself is invalid.
        raise ObjectStorageProtocolError("Pending direct-upload job lacks a canonical content type.")

    response = _head_object_or_raise(
        client,
        bucket_name=pending.input_bucket,
        object_key=pending.input_object_key,
    )
    content_length = _positive_content_length(response)
    content_type = response.get("ContentType")
    if not isinstance(content_type, str) or not content_type:
        raise ObjectStorageProtocolError("MinIO returned an invalid ContentType.")
    if content_type != pending.source_content_type:
        raise PermanentSourceVerificationError(
            PermanentSourceFailureCategory.CONTENT_TYPE_MISMATCH
        )

    metadata = response.get("Metadata")
    if _metadata_value(metadata, name="job-id") != pending.job_id:
        raise PermanentSourceVerificationError(
            PermanentSourceFailureCategory.METADATA_MISMATCH
        )
    if _metadata_value(metadata, name="stem-mode") != pending.stem_mode:
        raise PermanentSourceVerificationError(
            PermanentSourceFailureCategory.METADATA_MISMATCH
        )

    # The storage ContentLength is the hard 256 MiB enforcement point above.
    # For a browser direct upload, the Job API also persisted the exact local
    # File.size that it used to issue the form. MinIO stores the file bytes,
    # not the multipart form envelope, so an available declared size must
    # equal the completed object size. A mismatch proves the uploaded object
    # is not the source that this durable job authorized.
    if pending.source_size_bytes is not None and content_length != pending.source_size_bytes:
        raise PermanentSourceVerificationError(
            PermanentSourceFailureCategory.METADATA_MISMATCH
        )

    return VerifiedSourceObject(
        bucket_name=pending.input_bucket,
        object_key=pending.input_object_key,
        content_type=content_type,
        size_bytes=content_length,
    )
