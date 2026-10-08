"""Generate owner-checked, short-lived browser URLs for private MinIO objects.

The Job API authenticates the caller and reads the durable artifact coordinates
only after its owner-filtered PostgreSQL query succeeds. This module then signs
one exact key with the API's restricted MinIO identity. It performs local
Signature V4 work only; the browser later makes the actual GET directly through
the MinIO Ingress, so large stems/MIDI files never pass through the API Pod.
"""

from __future__ import annotations

import re
from uuid import UUID

import boto3
from botocore.config import Config

from app.object_storage import ObjectStorageSettings


# Match the cloud API's one-hour artifact-read contract. URLs are regenerated
# on each authenticated snapshot and never stored in PostgreSQL.
DEFAULT_DOWNLOAD_URL_EXPIRY_SECONDS = 3600
MAX_DOWNLOAD_URL_EXPIRY_SECONDS = 3600

_STEM_NAMES = frozenset({"vocals", "no_vocals", "bass", "other", "guitar", "piano", "drums"})
_SINGLE_FILENAME_PATTERN = re.compile(r"^[^/\\\x00]{1,255}$")


class PresignedDownloadContractError(ValueError):
    """An internal object key is not exactly the requested Job artifact."""


class PresignedDownloadSigningError(RuntimeError):
    """Boto3 did not produce one usable browser URL from the local settings."""


def _canonical_job_id(job_id: str) -> str:
    """Require lowercase canonical UUID text before it becomes a key component."""

    if not isinstance(job_id, str):
        raise PresignedDownloadContractError("job_id must be a UUID string.")
    try:
        canonical = str(UUID(job_id))
    except (ValueError, AttributeError) as error:
        raise PresignedDownloadContractError("job_id must be a UUID string.") from error
    if canonical != job_id:
        raise PresignedDownloadContractError("job_id must use canonical lowercase UUID form.")
    return canonical


def expected_download_key(*, job_id: str, kind: str, stem_name: str | None = None) -> str:
    """Return one deterministic source, stem, MIDI, or tempo object key.

    This repeats the PostgreSQL/worker object-layout contract immediately
    before signing. It prevents a malformed JSONB record from turning an
    authenticated Job snapshot into a signer for another Job's private object.
    """

    canonical_job_id = _canonical_job_id(job_id)
    if kind == "source":
        raise PresignedDownloadContractError(
            "source downloads require the validated one-filename source key."
        )
    if kind == "stem":
        if stem_name not in _STEM_NAMES:
            raise PresignedDownloadContractError("stem name is not an approved artifact.")
        return f"stems/{canonical_job_id}/{stem_name}.wav"
    if kind == "midi":
        if stem_name not in _STEM_NAMES:
            raise PresignedDownloadContractError("stem name is not an approved artifact.")
        return f"midi/{canonical_job_id}/{stem_name}.mid"
    if kind == "tempo":
        return f"midi/{canonical_job_id}/drums_bpm.json"
    if kind == "sheet-source":
        return f"midi-sheet-inputs/{canonical_job_id}/source.mid"
    if kind == "sheet-pdf":
        return f"midi-sheet-results/{canonical_job_id}/result.pdf"
    if kind == "sheet-musicxml":
        return f"midi-sheet-results/{canonical_job_id}/result.musicxml"
    if kind == "score-midi":
        return f"score-results/{canonical_job_id}/result.mid"
    if kind == "score-musicxml":
        return f"score-results/{canonical_job_id}/result.musicxml"
    raise PresignedDownloadContractError("artifact kind is not supported.")


def _validated_source_key(*, job_id: str, object_key: str) -> str:
    """Accept one flat filename below this Job's immutable source prefix."""

    canonical_job_id = _canonical_job_id(job_id)
    prefix = f"uploads/{canonical_job_id}/"
    if not isinstance(object_key, str) or not object_key.startswith(prefix):
        raise PresignedDownloadContractError("source key did not match its Job prefix.")
    filename = object_key.removeprefix(prefix)
    if (
        not _SINGLE_FILENAME_PATTERN.fullmatch(filename)
        or not filename.strip()
        or filename in {".", ".."}
        or "\\" in filename
    ):
        raise PresignedDownloadContractError("source key did not contain one safe filename.")
    return object_key


def _public_s3_client(settings: ObjectStorageSettings):
    """Create a SigV4 signer for the browser-facing MinIO Ingress origin.

    Explicit credentials prevent boto3 from consulting host profiles, cloud
    metadata, or ambient provider chains. `endpoint_url` is embedded in the
    signed URL; URL generation itself performs no HTTP request.
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


def create_presigned_download_url(
    settings: ObjectStorageSettings,
    *,
    job_id: str,
    object_key: str,
    kind: str,
    stem_name: str | None = None,
    expires_in_seconds: int = DEFAULT_DOWNLOAD_URL_EXPIRY_SECONDS,
) -> str:
    """Sign exactly one owner-checked private object for direct browser GET.

    `source` keys are constrained to one basename under `uploads/{job_id}/`.
    `score-source` keys are one server-chosen PDF/image below score-inputs.
    Stem and MIDI keys must equal their deterministic worker output key, and
    the optional BPM JSON can only be the ADTOF drums result for this Job.
    MinIO IAM independently limits the Job API identity to the reviewed
    prefixes; owner verification and this exact-key check narrow each URL to
    the one artifact the authenticated snapshot is allowed to reveal.
    """

    if (
        isinstance(expires_in_seconds, bool)
        or not isinstance(expires_in_seconds, int)
        or not 1 <= expires_in_seconds <= MAX_DOWNLOAD_URL_EXPIRY_SECONDS
    ):
        raise PresignedDownloadContractError("download URL expiry is outside the reviewed range.")
    if settings.uploads_bucket != "clouddsp-uploads":
        raise PresignedDownloadContractError("download bucket is not the reviewed private bucket.")

    if kind == "score-source":
        # The upload API chooses one of four deterministic source filenames.
        # Do not let a corrupted row authorize arbitrary names or other jobs.
        canonical_id = _canonical_job_id(job_id)
        if object_key not in {
            f"score-inputs/{canonical_id}/source{extension}"
            for extension in (".pdf", ".png", ".jpg", ".jpeg")
        }:
            raise PresignedDownloadContractError("score source key did not match its Job.")
        validated_key = object_key
    elif kind == "source":
        validated_key = _validated_source_key(job_id=job_id, object_key=object_key)
    else:
        validated_key = expected_download_key(job_id=job_id, kind=kind, stem_name=stem_name)
        if object_key != validated_key:
            raise PresignedDownloadContractError("artifact key did not match its deterministic Job key.")

    # generate_presigned_url performs local HMAC work; this Pod does not fetch
    # or proxy artifact bytes. The returned browser request hits the S3 Ingress.
    url = _public_s3_client(settings).generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.uploads_bucket, "Key": validated_key},
        ExpiresIn=expires_in_seconds,
    )
    if not isinstance(url, str) or not url.startswith(settings.public_endpoint + "/"):
        raise PresignedDownloadSigningError("S3 SDK returned an incomplete presigned URL.")
    return url
