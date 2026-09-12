"""Bind verified local Basic Pitch MIDI evidence to one deterministic MinIO plan.

This pure boundary joins three existing proofs without making an S3 request:

1. the strict durable ``basic-pitch.requested`` delivery contract;
2. the claimed task's lease identity; and
3. the bounded local Standard MIDI File/hash evidence.

It returns exactly one immutable private ``midi/{job_id}/{stem_name}.mid``
object plan with complete metadata.  The following small uploader task must
stream this plan through the restricted Basic Pitch MinIO identity and verify
that MinIO consumed the same bytes.  This module never calls MinIO, changes a
lease/result, acknowledges RabbitMQ, invokes Basic Pitch, or uses Kubernetes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.basic_pitch_requested_message import (
    LOCAL_UPLOADS_BUCKET,
    BasicPitchMidiOutput,
    BasicPitchRequestContractError,
    BasicPitchRequestedMessage,
    build_basic_pitch_midi_output,
)
from app.midi_artifact import VerifiedBasicPitchMidiArtifact, verify_and_hash_basic_pitch_midi
from app.task_lease import BASIC_PITCH_STEMS_BY_MODE, MAX_BASIC_PITCH_TASK_ATTEMPTS, BasicPitchTaskLease


BASIC_PITCH_MIDI_PRODUCER = "basic-pitch"
BASIC_PITCH_MIDI_METADATA_SCHEMA_VERSION = "1"


class BasicPitchMidiOutputObjectContractError(RuntimeError):
    """The lease, durable request, or local MIDI evidence cannot name an object."""


@dataclass(frozen=True)
class BasicPitchMidiOutputObject:
    """One complete local-file-to-private-object plan for a later MinIO uploader.

    The path is ephemeral Pod scratch and the metadata tuple is immutable so a
    caller cannot edit a size/checksum after the plan is built.  This is not an
    upload receipt: it contains no S3 client response, ETag, credential, or
    proof that an object presently exists.
    """

    bucket: str
    object_key: str
    local_path: Path
    content_type: str
    content_length: int
    s3_metadata: tuple[tuple[str, str], ...]
    # Retain the local verifier coordinate only for the immediately following
    # uploader.  It must never enter an S3 request, a database row, or a log.
    artifact: VerifiedBasicPitchMidiArtifact


def _error() -> BasicPitchMidiOutputObjectContractError:
    """Return the stable non-sensitive category for all invalid joined evidence."""

    return BasicPitchMidiOutputObjectContractError("Basic Pitch MIDI output object contract is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical lowercase UUID text before it forms MinIO metadata."""

    if not isinstance(value, str):
        raise _error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _error() from error
    if canonical != value:
        raise _error()
    return canonical


def _validated_message_output(message: object) -> BasicPitchMidiOutput:
    """Rebuild the only private output coordinate from the strict AMQP contract."""

    if not isinstance(message, BasicPitchRequestedMessage):
        raise _error()
    try:
        return build_basic_pitch_midi_output(message)
    except BasicPitchRequestContractError as error:
        raise _error() from error


def _validated_lease(
    lease: object,
    *,
    output: BasicPitchMidiOutput,
) -> tuple[str, str, str]:
    """Require one task lease to bind the output's Job/event/stem coordinate."""

    if not isinstance(lease, BasicPitchTaskLease):
        raise _error()
    task_id = _canonical_uuid(lease.task_id)
    job_id = _canonical_uuid(lease.job_id)
    request_event_id = _canonical_uuid(lease.request_event_id)
    _canonical_uuid(lease.lease_token)
    if (
        job_id != output.job_id
        or request_event_id != output.request_event_id
        or lease.stem_name != output.stem_name
        or lease.input_bucket != LOCAL_UPLOADS_BUCKET
        or lease.input_object_key != f"stems/{job_id}/{lease.stem_name}.wav"
        or lease.stem_mode not in BASIC_PITCH_STEMS_BY_MODE
        or lease.stem_name not in BASIC_PITCH_STEMS_BY_MODE[lease.stem_mode]
        or type(lease.attempt_count) is not int
        or not 1 <= lease.attempt_count <= MAX_BASIC_PITCH_TASK_ATTEMPTS
    ):
        raise _error()
    return task_id, job_id, lease.stem_mode


def _current_midi_artifact(value: object) -> VerifiedBasicPitchMidiArtifact:
    """Repeat output framing/hash proof so old evidence cannot upload new bytes."""

    if not isinstance(value, VerifiedBasicPitchMidiArtifact):
        raise _error()
    try:
        current = verify_and_hash_basic_pitch_midi(value.inference)
    except Exception as error:
        raise _error() from error
    if current != value:
        raise _error()
    return current


def _metadata(
    *,
    task_id: str,
    output: BasicPitchMidiOutput,
    stem_mode: str,
    artifact: VerifiedBasicPitchMidiArtifact,
) -> tuple[tuple[str, str], ...]:
    """Return complete immutable provenance and integrity metadata for one MIDI object."""

    return (
        ("schema-version", BASIC_PITCH_MIDI_METADATA_SCHEMA_VERSION),
        ("producer", BASIC_PITCH_MIDI_PRODUCER),
        ("job-id", output.job_id),
        ("task-id", task_id),
        ("request-event-id", output.request_event_id),
        ("stem-name", output.stem_name),
        ("stem-mode", stem_mode),
        ("size-bytes", str(artifact.size_bytes)),
        ("sha256", artifact.sha256),
        ("input-stem-sha256", output.input_stem_sha256),
    )


def build_basic_pitch_midi_output_object(
    *,
    lease: BasicPitchTaskLease,
    message: BasicPitchRequestedMessage,
    artifact: VerifiedBasicPitchMidiArtifact,
) -> BasicPitchMidiOutputObject:
    """Build exactly one stable object plan from current lease/request/MIDI evidence.

    A recovery of the same durable per-stem task targets the same private key;
    attempt number never enters the key.  The future upload/completion layer
    must retain the task token as its authority—this pure mapping only verifies
    static identity and local bytes, and it deliberately does not decide
    whether the task lease is still current in PostgreSQL.
    """

    output = _validated_message_output(message)
    task_id, job_id, stem_mode = _validated_lease(lease, output=output)
    current_artifact = _current_midi_artifact(artifact)
    if (
        output.bucket != LOCAL_UPLOADS_BUCKET
        or output.content_type != "audio/midi"
        or output.object_key != f"midi/{job_id}/{output.stem_name}.mid"
        or current_artifact.content_type != output.content_type
        or type(current_artifact.size_bytes) is not int
        or current_artifact.size_bytes < 1
        or not isinstance(current_artifact.path, Path)
        or not re.fullmatch(r"[0-9a-f]{64}", current_artifact.sha256)
    ):
        raise _error()
    return BasicPitchMidiOutputObject(
        bucket=output.bucket,
        object_key=output.object_key,
        local_path=current_artifact.path,
        content_type=output.content_type,
        content_length=current_artifact.size_bytes,
        s3_metadata=_metadata(
            task_id=task_id,
            output=output,
            stem_mode=stem_mode,
            artifact=current_artifact,
        ),
        artifact=current_artifact,
    )
