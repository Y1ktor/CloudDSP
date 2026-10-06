"""Define the exact private MinIO object plan for verified Demucs WAV stems.

This is deliberately a pure contract between the local output/hash boundaries
and a later narrow MinIO ``PutObject`` adapter.  The stable key is always
``stems/{job_id}/{stem_name}.wav``: a recovery of the same durable Demucs task
therefore targets the same private artifact coordinate instead of creating a
browser-visible attempt-specific object.  The task ID, content length, and
SHA-256 evidence travel as immutable S3 user metadata for later verification.

The module re-establishes the current hash inventory rather than trusting a
caller-constructed frozen dataclass.  It performs only local directory/file
reads needed for that revalidation; it never opens a MinIO client, uploads an
object, mutates PostgreSQL, renews a lease, acknowledges RabbitMQ, invokes a
model, builds an image, or changes Kubernetes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.artifacts.demucs_artifact_hash import (
    HashedDemucsStemInventory,
    hash_validated_demucs_stem_inventory,
)
from app.artifacts.demucs_artifacts import DEMUCS_STEM_FILE_EXTENSION, DEMUCS_STEMS_BY_MODE, validate_demucs_stem_inventory
from app.db.task_lease import DemucsTaskLease


# The reviewed MinIO policy permits the Demucs identity to write only this
# prefix in the existing private uploads bucket.  Keeping these values fixed
# here prevents a future Deployment environment variable from turning an
# approved stem into an arbitrary S3 bucket/key write.
LOCAL_DEMUCS_ARTIFACT_BUCKET = "clouddsp-uploads"
DEMUCS_ARTIFACT_KEY_PREFIX = "stems"
DEMUCS_ARTIFACT_CONTENT_TYPE = "audio/wav"
DEMUCS_ARTIFACT_PRODUCER = "demucs"
DEMUCS_ARTIFACT_SCHEMA_VERSION = "1"


class DemucsOutputObjectContractError(RuntimeError):
    """The lease or local hash evidence cannot form a reviewed output object."""


@dataclass(frozen=True)
class DemucsStemOutputObject:
    """One fully specified private object for a later bounded MinIO upload.

    ``local_path`` is deliberately Pod-local and short-lived.  It is input to
    the next uploader only and must never be persisted or exposed through the
    browser.  ``s3_metadata`` is an ordered tuple rather than a mutable dict so
    a caller cannot alter the exact evidence after this object plan is made;
    the later Boto3 adapter can pass ``dict(s3_metadata)`` as ``Metadata``.
    """

    bucket: str
    object_key: str
    local_path: Path
    content_type: str
    content_length: int
    s3_metadata: tuple[tuple[str, str], ...]


def _contract_error() -> DemucsOutputObjectContractError:
    """Return one non-sensitive category for invalid output-plan inputs."""

    # Do not serialize identifiers, a scratch path, or a digest in a normal
    # worker error.  Those values remain available only to local exception
    # chaining and future carefully scoped diagnostics.
    return DemucsOutputObjectContractError("Demucs output object contract is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text before it enters an S3 key."""

    if not isinstance(value, str):
        raise _contract_error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _contract_error() from error
    if value != canonical:
        raise _contract_error()
    return canonical


def _validated_lease_identity(lease: object) -> tuple[str, str, str]:
    """Return the fixed job/task/mode identity permitted to name output keys."""

    if not isinstance(lease, DemucsTaskLease):
        raise _contract_error()
    job_id = _canonical_uuid(lease.job_id)
    task_id = _canonical_uuid(lease.task_id)
    # These are not placed in the key, but requiring their canonical durable
    # form prevents a hand-built partial lease becoming an output authority.
    _canonical_uuid(lease.request_event_id)
    _canonical_uuid(lease.lease_token)
    if lease.input_bucket != LOCAL_DEMUCS_ARTIFACT_BUCKET:
        raise _contract_error()
    if not isinstance(lease.input_object_key, str):
        raise _contract_error()
    expected_source_prefix = f"uploads/{job_id}/"
    source_filename = lease.input_object_key.removeprefix(expected_source_prefix)
    if (
        not lease.input_object_key.startswith(expected_source_prefix)
        or not source_filename
        or "/" in source_filename
    ):
        raise _contract_error()
    if not isinstance(lease.stem_mode, str) or lease.stem_mode not in DEMUCS_STEMS_BY_MODE:
        raise _contract_error()
    return job_id, task_id, lease.stem_mode


def _current_hashed_inventory(value: object) -> HashedDemucsStemInventory:
    """Rebuild hash evidence so stale or forged input cannot select an object."""

    if not isinstance(value, HashedDemucsStemInventory):
        raise _contract_error()
    try:
        # The prior hash result has only its separation command and stem
        # evidence.  Rebuild the exact local inventory, then re-hash it before
        # equality comparison so a same-size byte replacement is also caught.
        current = hash_validated_demucs_stem_inventory(
            validate_demucs_stem_inventory(value.separation)
        )
    except Exception as error:
        raise _contract_error() from error
    if current != value:
        raise _contract_error()
    return current


def _s3_metadata(
    *,
    job_id: str,
    task_id: str,
    stem_name: str,
    stem_mode: str,
    size_bytes: int,
    sha256: str,
) -> tuple[tuple[str, str], ...]:
    """Return the complete immutable metadata set attached to one WAV object."""

    # S3 user-metadata keys are sent by Boto3 as ``x-amz-meta-*`` headers.  The
    # names stay lower-case/hyphenated so MinIO/SDK header normalization cannot
    # produce two spellings of the same durable evidence field.
    return (
        ("schema-version", DEMUCS_ARTIFACT_SCHEMA_VERSION),
        ("producer", DEMUCS_ARTIFACT_PRODUCER),
        ("job-id", job_id),
        ("task-id", task_id),
        ("stem-name", stem_name),
        ("stem-mode", stem_mode),
        ("size-bytes", str(size_bytes)),
        ("sha256", sha256),
    )


def build_demucs_stem_output_objects(
    *,
    lease: DemucsTaskLease,
    hashed_inventory: HashedDemucsStemInventory,
) -> tuple[DemucsStemOutputObject, ...]:
    """Build one deterministic private output plan for every exact Demucs stem.

    The caller supplies the currently running task's durable lease and the
    direct output from :func:`hash_validated_demucs_stem_inventory`.  This
    boundary repeats both local validation and hashing, verifies the mode
    matches the lease, then maps each reviewed stem name to its one key.  It
    intentionally leaves the later uploader responsible for streaming the
    same path and verifying those bytes during its own upload operation.
    """

    job_id, task_id, stem_mode = _validated_lease_identity(lease)
    current_inventory = _current_hashed_inventory(hashed_inventory)
    if current_inventory.separation.stem_mode != stem_mode:
        raise _contract_error()

    return tuple(
        DemucsStemOutputObject(
            bucket=LOCAL_DEMUCS_ARTIFACT_BUCKET,
            object_key=(
                f"{DEMUCS_ARTIFACT_KEY_PREFIX}/{job_id}/"
                f"{artifact.stem_name}{DEMUCS_STEM_FILE_EXTENSION}"
            ),
            local_path=artifact.path,
            content_type=DEMUCS_ARTIFACT_CONTENT_TYPE,
            content_length=artifact.size_bytes,
            s3_metadata=_s3_metadata(
                job_id=job_id,
                task_id=task_id,
                stem_name=artifact.stem_name,
                stem_mode=stem_mode,
                size_bytes=artifact.size_bytes,
                sha256=artifact.sha256,
            ),
        )
        for artifact in current_inventory.artifacts
    )
