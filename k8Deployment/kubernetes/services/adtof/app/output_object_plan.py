"""Name ADTOF's two private outputs before a model creates either artifact.

This pure mapping accepts only ``RunningADTOFStem`` evidence: the preceding
composition has already downloaded a checksum-verified drums WAV and committed
the exact task's ``leased`` -> ``running`` transition. It returns the two
stable MinIO coordinates that one ADTOF attempt may later write:

* ``midi/{job_id}/drums.mid``; and
* ``midi/{job_id}/drums_bpm.json``.

There are deliberately no output byte counts or output SHA-256 values yet: no
model output exists at this boundary. The returned immutable *base* provenance
is therefore joined with locally verified output-artifact evidence by a later
small task before any upload is allowed. This module does not read the scratch
file, invoke ADTOF, call MinIO, mutate PostgreSQL, acknowledge RabbitMQ, build
an image, or use Kubernetes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from app.adtof_requested_message import ADTOF_STEM_NAME, LOCAL_UPLOADS_BUCKET
from app.model_configuration import ADTOF_MODEL_CONFIGURATION_ID
from app.stem_download import DownloadedADTOFStem, MAX_ADTOF_STEM_SIZE_BYTES
from app.stem_task_start import RunningADTOFStem
from app.task_claim import ADTOF_STEM_MODES, MAX_ADTOF_TASK_ATTEMPTS, ADTOFTaskLease


ADTOF_OUTPUT_METADATA_SCHEMA_VERSION = "1"
ADTOF_OUTPUT_PRODUCER = "adtof"
ADTOF_MIDI_CONTENT_TYPE = "audio/midi"
ADTOF_TEMPO_CONTENT_TYPE = "application/json"

class ADTOFOutputArtifactKind(StrEnum):
    """The fixed logical artifacts one completed ADTOF drums task produces."""

    MIDI = "drum-midi"
    TEMPO_CANDIDATE = "tempo-candidate"


class ADTOFOutputObjectPlanContractError(RuntimeError):
    """A forged/incomplete running-task value cannot name a private output."""


@dataclass(frozen=True)
class ADTOFOutputObjectPlan:
    """One deterministic output coordinate plus immutable base provenance.

    ``base_s3_metadata`` intentionally excludes ``size-bytes`` and ``sha256``.
    Those are properties of bytes that do not exist until later local artifact
    validation. Treating their absence as explicit prevents a caller from
    fabricating integrity evidence before it has inspected model output.
    """

    bucket: str
    object_key: str
    content_type: str
    artifact_kind: ADTOFOutputArtifactKind
    base_s3_metadata: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ADTOFOutputObjectPlans:
    """The complete fixed pair of outputs for one running drums task.

    Named fields avoid an order-dependent list/tuple at the later model and
    upload boundaries: MIDI and tempo candidate are different formats even
    though they share a Job, task, and provenance identity.
    """

    midi: ADTOFOutputObjectPlan
    tempo_candidate: ADTOFOutputObjectPlan


def _error() -> ADTOFOutputObjectPlanContractError:
    """Return one non-sensitive category without task IDs, paths, or digests."""

    return ADTOFOutputObjectPlanContractError("ADTOF output object plan is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require durable canonical UUID text before it forms a private key."""

    if not isinstance(value, str):
        raise _error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _error() from error
    if canonical != value:
        raise _error()
    return canonical


def _validated_running_stem(value: object) -> tuple[ADTOFTaskLease, DownloadedADTOFStem]:
    """Revalidate the static proof carried by a committed-running handoff.

    Python dataclasses can be constructed directly, so this repeats every
    private coordinate and bounded-input check needed to ensure that a caller
    cannot broaden the output prefix by forging a running wrapper. Current
    lease ownership is intentionally not rechecked here: this pure planner has
    no database clock or connection; the later completion transaction owns
    that authority.
    """

    if not isinstance(value, RunningADTOFStem):
        raise _error()
    lease = value.lease
    stem = value.stem
    if not isinstance(lease, ADTOFTaskLease) or not isinstance(stem, DownloadedADTOFStem):
        raise _error()

    _canonical_uuid(lease.task_id)
    job_id = _canonical_uuid(lease.job_id)
    _canonical_uuid(lease.request_event_id)
    _canonical_uuid(lease.lease_token)

    if (
        lease.stem_name != ADTOF_STEM_NAME
        or lease.input_bucket != LOCAL_UPLOADS_BUCKET
        or lease.input_object_key != f"stems/{job_id}/{ADTOF_STEM_NAME}.wav"
        or lease.stem_mode not in ADTOF_STEM_MODES
        or type(lease.attempt_count) is not int
        or not 1 <= lease.attempt_count <= MAX_ADTOF_TASK_ATTEMPTS
        or not isinstance(lease.lease_expires_at, datetime)
        or lease.lease_expires_at.tzinfo is None
        or not isinstance(value.started_at, datetime)
        or value.started_at.tzinfo is None
        or not isinstance(stem.stem_path, Path)
        or type(stem.size_bytes) is not int
        or not 1 <= stem.size_bytes <= MAX_ADTOF_STEM_SIZE_BYTES
        or not isinstance(stem.sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", stem.sha256)
    ):
        raise _error()
    return lease, stem


def _base_metadata(
    *,
    lease: ADTOFTaskLease,
    input_stem_sha256: str,
    artifact_kind: ADTOFOutputArtifactKind,
) -> tuple[tuple[str, str], ...]:
    """Return immutable provenance shared by an artifact's later final plan."""

    return (
        ("schema-version", ADTOF_OUTPUT_METADATA_SCHEMA_VERSION),
        ("producer", ADTOF_OUTPUT_PRODUCER),
        ("job-id", lease.job_id),
        ("task-id", lease.task_id),
        ("request-event-id", lease.request_event_id),
        ("stem-name", ADTOF_STEM_NAME),
        ("stem-mode", lease.stem_mode),
        ("artifact-kind", artifact_kind.value),
        ("input-stem-sha256", input_stem_sha256),
        ("model-config-id", ADTOF_MODEL_CONFIGURATION_ID),
    )


def build_adtof_output_object_plans(*, running: RunningADTOFStem) -> ADTOFOutputObjectPlans:
    """Build the fixed private MIDI/tempo coordinates for one running ADTOF task.

    The generated keys deliberately omit an attempt number. Lease recovery
    therefore targets the same logical artifacts instead of accumulating
    attempt-specific browser-visible objects. This function is intentionally
    side-effect free; it neither accesses the temporary input path nor claims
    that either planned output has been generated or stored.
    """

    lease, stem = _validated_running_stem(running)
    job_id = _canonical_uuid(lease.job_id)
    midi_kind = ADTOFOutputArtifactKind.MIDI
    tempo_kind = ADTOFOutputArtifactKind.TEMPO_CANDIDATE
    return ADTOFOutputObjectPlans(
        midi=ADTOFOutputObjectPlan(
            bucket=LOCAL_UPLOADS_BUCKET,
            object_key=f"midi/{job_id}/drums.mid",
            content_type=ADTOF_MIDI_CONTENT_TYPE,
            artifact_kind=midi_kind,
            base_s3_metadata=_base_metadata(
                lease=lease,
                input_stem_sha256=stem.sha256,
                artifact_kind=midi_kind,
            ),
        ),
        tempo_candidate=ADTOFOutputObjectPlan(
            bucket=LOCAL_UPLOADS_BUCKET,
            object_key=f"midi/{job_id}/drums_bpm.json",
            content_type=ADTOF_TEMPO_CONTENT_TYPE,
            artifact_kind=tempo_kind,
            base_s3_metadata=_base_metadata(
                lease=lease,
                input_stem_sha256=stem.sha256,
                artifact_kind=tempo_kind,
            ),
        ),
    )
