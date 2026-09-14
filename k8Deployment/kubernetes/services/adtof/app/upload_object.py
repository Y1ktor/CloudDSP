"""Bind verified local ADTOF results to complete private MinIO upload plans.

The local-task composition has a running task, fixed base output coordinates,
and two bounded local artifacts. This pure boundary revalidates all of those
facts, repeats each local file's format/SHA-256 verification, and appends the
resulting immutable ``size-bytes`` and ``sha256`` fields to its base metadata.
It returns exactly two upload plans: drum MIDI and tempo-candidate JSON.

The local paths remain Pod scratch and are meaningful only while the enclosing
running-stem context remains open. This module neither creates an S3 client nor
uploads bytes, changes PostgreSQL, handles RabbitMQ, runs ADTOF, or calls
Kubernetes. A later uploader must stream these exact plans and then verify the
stored MinIO objects before task completion is considered.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.adtof_inference_command import ADTOFCPUInferenceCommand
from app.local_task_execution import VerifiedADTOFLocalTaskOutputs
from app.output_artifact import (
    ADTOFTempoCandidate,
    VerifiedADTOFOutputArtifact,
    verify_and_hash_adtof_output_artifact,
)
from app.output_object_plan import ADTOFOutputObjectPlan, ADTOFOutputObjectPlans, build_adtof_output_object_plans


class ADTOFUploadObjectPlanContractError(RuntimeError):
    """Running-task/base-plan/local-artifact evidence cannot form a MinIO plan."""


@dataclass(frozen=True)
class ADTOFUploadObject:
    """One complete private local-file-to-MinIO object plan.

    This is not a stored-object receipt: MinIO has not been contacted. The
    retained artifact is only for the immediately following upload boundary to
    repeat the local file proof before it streams bytes. Neither the path nor
    this dataclass may enter a database row, browser payload, or public log.
    """

    bucket: str
    object_key: str
    local_path: Path
    content_type: str
    content_length: int
    s3_metadata: tuple[tuple[str, str], ...]
    artifact: VerifiedADTOFOutputArtifact


@dataclass(frozen=True)
class ADTOFUploadObjects:
    """The indivisible MIDI/tempo pair a completed ADTOF task may upload."""

    midi: ADTOFUploadObject
    tempo_candidate: ADTOFUploadObject


def _error() -> ADTOFUploadObjectPlanContractError:
    """Return one stable category without paths, object keys, or digests."""

    return ADTOFUploadObjectPlanContractError("ADTOF upload object plan is invalid.")


def _current_artifact(
    *,
    plan: ADTOFOutputObjectPlan,
    previous: object,
) -> VerifiedADTOFOutputArtifact:
    """Repeat local validation so stale same-path evidence cannot be uploaded."""

    if not isinstance(previous, VerifiedADTOFOutputArtifact) or previous.output_plan != plan:
        raise _error()
    try:
        current = verify_and_hash_adtof_output_artifact(
            output_plan=plan,
            artifact_path=previous.path,
        )
    except Exception as error:
        raise _error() from error
    if current != previous:
        raise _error()
    return current


def _complete_metadata(
    *,
    base_plan: ADTOFOutputObjectPlan,
    artifact: VerifiedADTOFOutputArtifact,
) -> tuple[tuple[str, str], ...]:
    """Append only byte-derived integrity fields to frozen base provenance."""

    return base_plan.base_s3_metadata + (
        ("size-bytes", str(artifact.size_bytes)),
        ("sha256", artifact.sha256),
    )


def _upload_object(
    *,
    base_plan: ADTOFOutputObjectPlan,
    artifact: VerifiedADTOFOutputArtifact,
) -> ADTOFUploadObject:
    """Expose the exact local evidence needed by one later MinIO upload call."""

    if (
        not isinstance(base_plan, ADTOFOutputObjectPlan)
        or not isinstance(artifact.path, Path)
        or artifact.output_plan != base_plan
        or artifact.size_bytes < 1
    ):
        raise _error()
    return ADTOFUploadObject(
        bucket=base_plan.bucket,
        object_key=base_plan.object_key,
        local_path=artifact.path,
        content_type=base_plan.content_type,
        content_length=artifact.size_bytes,
        s3_metadata=_complete_metadata(base_plan=base_plan, artifact=artifact),
        artifact=artifact,
    )


def build_adtof_upload_objects(*, local_outputs: VerifiedADTOFLocalTaskOutputs) -> ADTOFUploadObjects:
    """Build complete immutable MIDI/tempo upload plans from current local proof.

    Rebuilding base plans from the running lease stops a hand-built result from
    substituting another Job, model configuration, output key, or input digest.
    Rehashing both files immediately before the plan returns stops an earlier
    valid file check from authorizing replacement bytes at upload time.
    """

    if not isinstance(local_outputs, VerifiedADTOFLocalTaskOutputs):
        raise _error()
    if (
        not isinstance(local_outputs.inference, ADTOFCPUInferenceCommand)
        or not isinstance(local_outputs.midi, VerifiedADTOFOutputArtifact)
        or not isinstance(local_outputs.tempo, VerifiedADTOFOutputArtifact)
        or not isinstance(local_outputs.tempo_candidate, ADTOFTempoCandidate)
    ):
        raise _error()
    expected_plans = build_adtof_output_object_plans(running=local_outputs.running)
    if (
        not isinstance(local_outputs.output_plans, ADTOFOutputObjectPlans)
        or local_outputs.output_plans != expected_plans
        or local_outputs.midi.output_plan != expected_plans.midi
        or local_outputs.tempo.output_plan != expected_plans.tempo_candidate
        or local_outputs.midi.path != local_outputs.inference.midi_output_path
        or local_outputs.tempo.path != local_outputs.inference.tempo_output_path
        or local_outputs.midi.tempo_candidate is not None
        or local_outputs.tempo.tempo_candidate != local_outputs.tempo_candidate
    ):
        raise _error()

    current_midi = _current_artifact(plan=expected_plans.midi, previous=local_outputs.midi)
    current_tempo = _current_artifact(
        plan=expected_plans.tempo_candidate,
        previous=local_outputs.tempo,
    )
    if current_tempo.tempo_candidate != local_outputs.tempo_candidate:
        raise _error()
    return ADTOFUploadObjects(
        midi=_upload_object(base_plan=expected_plans.midi, artifact=current_midi),
        tempo_candidate=_upload_object(
            base_plan=expected_plans.tempo_candidate,
            artifact=current_tempo,
        ),
    )
