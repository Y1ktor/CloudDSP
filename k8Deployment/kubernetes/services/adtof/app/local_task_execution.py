"""Compose one already-running ADTOF task through local CPU output verification.

This post-start coordinator is intentionally smaller than a full worker. Its
caller already owns a committed ``RunningADTOFStem`` inside the temporary
download context. For that one private WAV it builds the deterministic output
plans, reserves/runs the fixed CPU child process, then verifies and hashes both
local result files before returning them.

The returned paths remain valid only while the caller keeps the surrounding
``started_verified_adtof_stem`` context open. This module does not download a
stem, access MinIO, mutate PostgreSQL, receive/acknowledge RabbitMQ, schedule a
retry, build an image, or use Kubernetes. Later boundaries own uploading,
stored-object proof, guarded completion, and worker supervision.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.adtof_cpu_process import (
    DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    ADTOFCPUProcessRunner,
    run_adtof_cpu_inference_process,
)
from app.adtof_inference_command import ADTOFCPUInferenceCommand, build_adtof_cpu_inference_command
from app.output_artifact import ADTOFTempoCandidate, VerifiedADTOFOutputArtifact, verify_and_hash_adtof_output_artifact
from app.output_object_plan import ADTOFOutputObjectPlans, build_adtof_output_object_plans
from app.stem_task_start import RunningADTOFStem


class ADTOFLocalTaskExecutionContractError(RuntimeError):
    """The composed local result does not contain exactly one MIDI and tempo proof."""


@dataclass(frozen=True)
class VerifiedADTOFLocalTaskOutputs:
    """All non-durable local evidence from one successful ADTOF child process.

    ``running`` and the two artifact paths deliberately remain part of this
    value so the next upload-plan boundary can bind output bytes to the exact
    running lease and base provenance. Nothing in this dataclass is safe to
    persist or return to a browser: all paths are temporary Pod scratch.
    """

    running: RunningADTOFStem
    inference: ADTOFCPUInferenceCommand
    output_plans: ADTOFOutputObjectPlans
    midi: VerifiedADTOFOutputArtifact
    tempo: VerifiedADTOFOutputArtifact
    tempo_candidate: ADTOFTempoCandidate


def _local_result_or_raise(
    *,
    running: RunningADTOFStem,
    inference: ADTOFCPUInferenceCommand,
    output_plans: ADTOFOutputObjectPlans,
    midi: VerifiedADTOFOutputArtifact,
    tempo: VerifiedADTOFOutputArtifact,
) -> VerifiedADTOFLocalTaskOutputs:
    """Prevent a local model exit from being mistaken for complete dual evidence."""

    if (
        not isinstance(running, RunningADTOFStem)
        or not isinstance(inference, ADTOFCPUInferenceCommand)
        or not isinstance(output_plans, ADTOFOutputObjectPlans)
        or not isinstance(midi, VerifiedADTOFOutputArtifact)
        or not isinstance(tempo, VerifiedADTOFOutputArtifact)
        or midi.output_plan != output_plans.midi
        or tempo.output_plan != output_plans.tempo_candidate
        or midi.path != inference.midi_output_path
        or tempo.path != inference.tempo_output_path
        or midi.tempo_candidate is not None
        or not isinstance(tempo.tempo_candidate, ADTOFTempoCandidate)
    ):
        raise ADTOFLocalTaskExecutionContractError("ADTOF local task output evidence is invalid.")
    return VerifiedADTOFLocalTaskOutputs(
        running=running,
        inference=inference,
        output_plans=output_plans,
        midi=midi,
        tempo=tempo,
        tempo_candidate=tempo.tempo_candidate,
    )


def execute_running_adtof_local_task(
    *,
    running: RunningADTOFStem,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    process_runner: ADTOFCPUProcessRunner | None = None,
) -> VerifiedADTOFLocalTaskOutputs:
    """Run/verify both local outputs for one already-running ADTOF drums task.

    This function must be called only inside ``started_verified_adtof_stem``'s
    ``with`` block. The command builder/process runner/artifact verifier each
    revalidate their own inputs, so a direct frozen value cannot broaden a
    source path, command, output key, or local file read. Exceptions propagate
    unchanged: a later supervisor owns the reviewed retry/terminal policy.
    """

    if not isinstance(running, RunningADTOFStem):
        raise TypeError("running must be RunningADTOFStem.")
    if not isinstance(work_directory, Path):
        raise TypeError("work_directory must be pathlib.Path.")
    if type(process_timeout_seconds) is not int:
        raise TypeError("process_timeout_seconds must be an integer.")

    output_plans = build_adtof_output_object_plans(running=running)
    inference = build_adtof_cpu_inference_command(
        running=running,
        work_directory=work_directory,
    )
    completed_inference = run_adtof_cpu_inference_process(
        inference,
        timeout_seconds=process_timeout_seconds,
        runner=process_runner,
    )
    midi = verify_and_hash_adtof_output_artifact(
        output_plan=output_plans.midi,
        artifact_path=completed_inference.midi_output_path,
    )
    tempo = verify_and_hash_adtof_output_artifact(
        output_plan=output_plans.tempo_candidate,
        artifact_path=completed_inference.tempo_output_path,
    )
    return _local_result_or_raise(
        running=running,
        inference=completed_inference,
        output_plans=output_plans,
        midi=midi,
        tempo=tempo,
    )
