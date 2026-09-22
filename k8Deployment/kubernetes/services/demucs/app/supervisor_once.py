"""Run one complete Demucs supervisor step and apply its control action once.

The cadence-aware step selects the next bounded worker action and pure backoff
decision. ``supervisor_action.py`` alone may perform the corresponding
shutdown-aware wait. This composition joins them once and returns both compact
evidence and the immutable next state for a future entrypoint loop.

It owns no persistent loop, RabbitMQ/PostgreSQL/MinIO connection lifecycle,
signal installation, resource cleanup, image build, Deployment, or Kubernetes
API call. Operational exceptions propagate instead of becoming made-up
``continue`` facts; a later entrypoint owns any resource restart after a
reviewed retryable failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from app.demucs_artifact_upload import DemucsPutObjectClient
from app.demucs_process import DemucsProcessRunner
from app.ffprobe_process import DemucsFFprobeRunner
from app.planned_stem_upload import DemucsPlannedStemUploader
from app.pre_model_failure_transition import DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS
from app.running_failure_transition import DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS
from app.source_preflight import DemucsSourcePreflightClient
from app.supervisor_action import (
    DemucsShutdownWaiter,
    DemucsSupervisorActionResult,
    apply_demucs_supervisor_decision,
)
from app.supervisor_step import (
    DemucsSupervisorStepResult,
    DemucsSupervisorStepState,
    run_one_demucs_supervisor_step,
)
from app.worker_cycle import DemucsWorkerCycleDatabase


@dataclass(frozen=True)
class DemucsSupervisorOnceResult:
    """One completed step/action pair and the exact state for a later cycle.

    Next state remains present for shutdown/fatal control outcomes so this is
    an accurate completed-step observation. A real entrypoint must stop rather
    than reuse it in those outcomes. The result carries no channel/client,
    broker delivery, credential, object key, lease token, or scratch path.
    """

    step: DemucsSupervisorStepResult
    action_result: DemucsSupervisorActionResult
    next_state: DemucsSupervisorStepState

    def __post_init__(self) -> None:
        """Prevent applying an action/state unrelated to the returned decision."""

        if not isinstance(self.step, DemucsSupervisorStepResult):
            raise TypeError("Demucs supervisor once step is invalid.")
        if not isinstance(self.action_result, DemucsSupervisorActionResult):
            raise TypeError("Demucs supervisor once action result is invalid.")
        if not isinstance(self.next_state, DemucsSupervisorStepState):
            raise TypeError("Demucs supervisor once next state is invalid.")
        if self.next_state != self.step.next_state:
            raise ValueError("Demucs supervisor once must retain its step next state.")
        if self.action_result.action is not self.step.decision.action:
            raise ValueError("Demucs supervisor once action does not match its decision.")


def run_one_demucs_supervisor_cycle(
    channel: Any,
    *,
    state: DemucsSupervisorStepState,
    database: DemucsWorkerCycleDatabase,
    source_client: DemucsSourcePreflightClient,
    artifact_client: DemucsPutObjectClient,
    work_directory: Path,
    shutdown_waiter: DemucsShutdownWaiter,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
    demucs_runner: DemucsProcessRunner | None = None,
    uploader: DemucsPlannedStemUploader | None = None,
    event_id_factory: Callable[[], UUID] = uuid4,
    pre_model_retry_after_seconds: int = DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
    running_retry_after_seconds: int = DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
    jitter_fraction: float = 0.0,
) -> DemucsSupervisorOnceResult:
    """Run one worker step and apply one shutdown-aware control action.

    The step owns cadence/failure classification; the action adapter owns the
    one possible wait. Nothing occurs after that action returns, so a shutdown
    result cannot accidentally start another broker receive or recovery scan.
    """

    step = run_one_demucs_supervisor_step(
        channel,
        state=state,
        database=database,
        source_client=source_client,
        artifact_client=artifact_client,
        work_directory=work_directory,
        ffprobe_runner=ffprobe_runner,
        demucs_runner=demucs_runner,
        uploader=uploader,
        event_id_factory=event_id_factory,
        pre_model_retry_after_seconds=pre_model_retry_after_seconds,
        running_retry_after_seconds=running_retry_after_seconds,
        jitter_fraction=jitter_fraction,
    )
    if not isinstance(step, DemucsSupervisorStepResult):
        raise TypeError("Demucs supervisor step returned an invalid result.")

    action_result = apply_demucs_supervisor_decision(
        step.decision,
        shutdown_waiter=shutdown_waiter,
    )
    if not isinstance(action_result, DemucsSupervisorActionResult):
        raise TypeError("Demucs supervisor action returned an invalid result.")
    return DemucsSupervisorOnceResult(
        step=step,
        action_result=action_result,
        next_state=step.next_state,
    )
