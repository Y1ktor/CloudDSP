"""Local, constrained Signature V4 contracts for browser-to-Minio uploads.

The Job API is a control plane: it authenticates a caller, creates durable job
state, and returns a short-lived form contract. It deliberately is not the
data path for audio or score bytes. A browser posts its selected source file
directly to the private MinIO bucket through Traefik after this module signs
the workflow-specific form.

Boto3's generate_presigned_post method performs only local cryptographic work
with the restricted S3 credential. No method in this module contacts MinIO,
Kubernetes Service DNS, or a public *.localhost address. The returned endpoint
must therefore be the browser-visible Traefik URL, while future
server-originated S3 reads use ObjectStorageSettings.internal_endpoint in
their own, separate helper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping
from uuid import UUID

import boto3
from botocore.config import Config

from app.direct_upload_contract import MAX_SOURCE_UPLOAD_BYTES, VALID_DIRECT_UPLOAD_STEM_MODES
from app.object_storage import ObjectStorageSettings
from app.score_upload_contract import MAX_SCORE_SOURCE_BYTES, SCORE_MEDIA_TYPES


# This short signature lifetime is an S3 concern rather than request-validation
# policy, so it remains here. The shared source-size and stem-mode constants
# live in direct_upload_contract.py, where the POST /jobs route validates
# browser input before invoking this signer.
DEFAULT_UPLOAD_POST_EXPIRY_SECONDS = 300
MAX_UPLOAD_POST_EXPIRY_SECONDS = 900

# This helper receives a canonical MIME type from the POST /jobs request
# validator. It repeats only a conservative syntax check before placing the
# exact value in an S3 policy; the route, not this S3 primitive, decides which
# filename/type combinations CloudDSP supports.
_CONTENT_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)


class PresignedUploadContractError(ValueError):
    """Raised before signing when an internal upload-contract input is unsafe.

    These errors are internal contract failures. The route treats them as a
    server-side 503 because valid browser input should never cause one; they
    contain no access key, secret, full generated policy, or signature.
    """


class PresignedUploadSigningError(RuntimeError):
    """Raised only if the local S3 SDK cannot construct a valid form result."""


@dataclass(frozen=True)
class PresignedPost:
    """The browser-safe parts of a key-bound, short-lived upload form.

    The policy, credential scope, and signature in fields authorize this one
    form until it expires, so they are intentionally omitted from repr. The
    caller may serialize fields to the authenticated browser response; it must
    not log this object or store those short-lived values in PostgreSQL.
    """

    url: str
    fields: Mapping[str, str] = field(repr=False)
    expires_in_seconds: int
    maximum_source_bytes: int


def _normalized_job_id(job_id: str) -> str:
    """Return a canonical UUID so the signed key exactly matches one job prefix."""

    if not isinstance(job_id, str):
        raise PresignedUploadContractError("job_id must be a UUID string.")
    try:
        return str(UUID(job_id))
    except (ValueError, AttributeError) as error:
        raise PresignedUploadContractError("job_id must be a UUID string.") from error


def _validated_input_object_key(*, job_id: str, input_object_key: str) -> str:
    """Require one job-owned source filename, never a broad or other-job key.

    The database migration has a matching invariant. Repeating it before
    signing means a future caller cannot turn the restricted uploads/* S3
    identity into a presigned form for another job, a nested worker path, or an
    unrelated object merely by passing a surprising key string.
    """

    if not isinstance(input_object_key, str):
        raise PresignedUploadContractError("input_object_key must be a string.")

    required_prefix = f"uploads/{job_id}/"
    if not input_object_key.startswith(required_prefix):
        raise PresignedUploadContractError(
            "input_object_key must use the current job's uploads/{job_id}/ prefix."
        )

    filename = input_object_key.removeprefix(required_prefix)
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or len(filename) > 255
    ):
        raise PresignedUploadContractError(
            "input_object_key must end with one non-empty filename basename."
        )
    return input_object_key


def _validated_content_type(content_type: str) -> str:
    """Require one canonical MIME type that can safely become an exact policy field."""

    if not isinstance(content_type, str) or not _CONTENT_TYPE_PATTERN.fullmatch(content_type):
        raise PresignedUploadContractError("content_type must be one canonical MIME type.")
    return content_type


def _validated_positive_limit(*, name: str, value: int, maximum: int) -> int:
    """Reject booleans, zero, negatives, and accidental policy relaxations."""

    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise PresignedUploadContractError(f"{name} must be between 1 and {maximum}.")
    return value


def _validated_stem_mode(stem_mode: str) -> str:
    """Bind the processing choice into object metadata exactly as the cloud API does."""

    if stem_mode not in VALID_DIRECT_UPLOAD_STEM_MODES:
        raise PresignedUploadContractError("stem_mode must be 2-stems, 4-stems, or 6-stems.")
    return stem_mode


def _public_s3_client(settings: ObjectStorageSettings):
    """Create an S3 signer configured for MinIO's one-host public Ingress.

    Explicit credentials prevent boto3 from probing ambient provider chains
    such as EC2 metadata. endpoint_url influences the URL embedded in the form
    only; generate_presigned_post makes no request to that URL. Path-style
    addressing is required because Traefik exposes one MinIO host, not a
    wildcard host for bucket.minio.localhost.
    """

    return boto3.client(
        "s3",
        endpoint_url=settings.public_endpoint,
        aws_access_key_id=settings.access_key,
        aws_secret_access_key=settings.secret_key,
        region_name=settings.region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": settings.addressing_style},
        ),
    )


def create_constrained_source_upload_post(
    settings: ObjectStorageSettings,
    *,
    job_id: str,
    input_object_key: str,
    content_type: str,
    stem_mode: str,
    maximum_source_bytes: int = MAX_SOURCE_UPLOAD_BYTES,
    expires_in_seconds: int = DEFAULT_UPLOAD_POST_EXPIRY_SECONDS,
) -> PresignedPost:
    """Generate a short-lived MinIO form that can write one source object only.

    S3 POST policies are enforcement rules evaluated by MinIO, not merely hints
    for the React client. The generated policy requires all of the following:

    * the configured private bucket and the one exact job-owned object key;
    * the one canonical content type accepted by the future API route;
    * job/stem metadata that lets future upload intake verify the object;
    * a one-byte-to-256-MiB (or lower caller-supplied) object-size range; and
    * an expiry no longer than fifteen minutes.

    Boto3 automatically adds exact bucket and key policy conditions from Bucket
    and Key. They must not be duplicated in Conditions: boto3's documented POST
    API would otherwise produce malformed policy input.
    """

    canonical_job_id = _normalized_job_id(job_id)
    object_key = _validated_input_object_key(
        job_id=canonical_job_id,
        input_object_key=input_object_key,
    )
    canonical_content_type = _validated_content_type(content_type)
    canonical_stem_mode = _validated_stem_mode(stem_mode)
    size_limit = _validated_positive_limit(
        name="maximum_source_bytes",
        value=maximum_source_bytes,
        maximum=MAX_SOURCE_UPLOAD_BYTES,
    )
    expiry = _validated_positive_limit(
        name="expires_in_seconds",
        value=expires_in_seconds,
        maximum=MAX_UPLOAD_POST_EXPIRY_SECONDS,
    )

    # This is local HMAC signing. The SDK returns a URL plus browser form fields
    # but does not upload a byte or make an HTTP request while this function runs.
    response = _public_s3_client(settings).generate_presigned_post(
        Bucket=settings.uploads_bucket,
        Key=object_key,
        Fields={
            "Content-Type": canonical_content_type,
            "x-amz-meta-job-id": canonical_job_id,
            "x-amz-meta-stem-mode": canonical_stem_mode,
        },
        Conditions=[
            {"Content-Type": canonical_content_type},
            {"x-amz-meta-job-id": canonical_job_id},
            {"x-amz-meta-stem-mode": canonical_stem_mode},
            ["content-length-range", 1, size_limit],
        ],
        ExpiresIn=expiry,
    )

    url = response.get("url")
    fields = response.get("fields")
    if not isinstance(url, str) or not url or not isinstance(fields, dict):
        # This would indicate an SDK/programming regression, not a browser
        # error. Keep returned policy/signature material out of the exception.
        raise PresignedUploadSigningError("S3 SDK returned an incomplete presigned POST.")

    # Boto3 produces string field values. Copy the mapping so callers cannot
    # mutate a private SDK response object after the contract is returned.
    return PresignedPost(
        url=url,
        fields={str(name): str(value) for name, value in fields.items()},
        expires_in_seconds=expiry,
        maximum_source_bytes=size_limit,
    )


def create_constrained_score_upload_post(
    settings: ObjectStorageSettings,
    *,
    job_id: str,
    input_object_key: str,
    content_type: str,
    maximum_source_bytes: int = MAX_SCORE_SOURCE_BYTES,
    expires_in_seconds: int = DEFAULT_UPLOAD_POST_EXPIRY_SECONDS,
) -> PresignedPost:
    """Sign one score source object without widening the audio upload form.

    The exact key, content type, direction metadata, and byte range are S3 POST
    policy conditions enforced by MinIO. A later intake worker must verify the
    object's actual bytes and compare its metadata with the durable score row.
    """

    canonical_job_id = _normalized_job_id(job_id)
    required_prefix = f"score-inputs/{canonical_job_id}/"
    extension = input_object_key.removeprefix(required_prefix).removeprefix("source")
    if (
        not input_object_key.startswith(required_prefix)
        or extension not in SCORE_MEDIA_TYPES
        or input_object_key != f"{required_prefix}source{extension}"
        or content_type != SCORE_MEDIA_TYPES[extension][0]
    ):
        raise PresignedUploadContractError("Score upload key and content type must match one supported source.")
    size_limit = _validated_positive_limit(
        name="maximum_source_bytes", value=maximum_source_bytes, maximum=MAX_SCORE_SOURCE_BYTES
    )
    expiry = _validated_positive_limit(
        name="expires_in_seconds", value=expires_in_seconds, maximum=MAX_UPLOAD_POST_EXPIRY_SECONDS
    )
    try:
        response = _public_s3_client(settings).generate_presigned_post(
            Bucket=settings.uploads_bucket,
            Key=input_object_key,
            Fields={
                "Content-Type": content_type,
                "x-amz-meta-job-id": canonical_job_id,
                "x-amz-meta-score-direction": "score_to_midi",
            },
            Conditions=[
                {"Content-Type": content_type},
                {"x-amz-meta-job-id": canonical_job_id},
                {"x-amz-meta-score-direction": "score_to_midi"},
                ["content-length-range", 1, size_limit],
            ],
            ExpiresIn=expiry,
        )
    except Exception as error:
        raise PresignedUploadSigningError("S3 SDK could not sign the score upload.") from error
    url, fields = response.get("url"), response.get("fields")
    if not isinstance(url, str) or not url or not isinstance(fields, dict):
        raise PresignedUploadSigningError("S3 SDK returned an incomplete score upload form.")
    return PresignedPost(
        url=url,
        fields={str(name): str(value) for name, value in fields.items()},
        expires_in_seconds=expiry,
        maximum_source_bytes=size_limit,
    )
