"""Run one complete ADTOF supervisor step and apply its control action once.

The cadence-aware supervisor step chooses the next bounded worker action and
the pure backoff decision. ``supervisor_action.py`` is the only layer that may
perform the corresponding shutdown-aware wait. This small composition joins
them exactly once and returns both evidence and the next immutable supervisor
state for a future entrypoint loop.

It does not own a persistent loop, RabbitMQ/PostgreSQL/MinIO connection
lifecycle, signal installation, resource cleanup, image build, Deployment, or
Kubernetes API call. Operational exceptions propagate instead of being turned
into a made-up `continue` result; a later entrypoint decides whether to restart
process resources after a reviewed retryable failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.processing.adtof_cpu_process import DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS, ADTOFCPUProcessRunner
from app.runtime.claimed_task_success import ADTOFClaimedTaskSuccessStorageClient
from app.runtime.supervisor_action import (
    ADTOFShutdownWaiter,
    ADTOFSupervisorActionResult,
    apply_adtof_supervisor_decision,
)
from app.runtime.supervisor_step import (
    ADTOFSupervisorStepResult,
    ADTOFSupervisorStepState,
    run_one_adtof_supervisor_step,
)
from app.runtime.worker_cycle import ADTOFWorkerCycleDatabase


@dataclass(frozen=True)
class ADTOFSupervisorOnceResult:
    """One completed step/action pair and the exact state for a later cycle.

    The next state is returned even for shutdown or fatal outcomes so the
    result remains an accurate observation of the completed step. A real
    entrypoint will stop instead of reusing it in those cases. This object
    retains no channel/client, broker delivery, credentials, object key, or
    scratch path.
    """

    step: ADTOFSupervisorStepResult
    action_result: ADTOFSupervisorActionResult
    next_state: ADTOFSupervisorStepState

    def __post_init__(self) -> None:
        """Prevent a runner from separating a decision from its applied action."""

        if not isinstance(self.step, ADTOFSupervisorStepResult):
            raise TypeError("ADTOF supervisor once step is invalid.")
        if not isinstance(self.action_result, ADTOFSupervisorActionResult):
            raise TypeError("ADTOF supervisor once action result is invalid.")
        if not isinstance(self.next_state, ADTOFSupervisorStepState):
            raise TypeError("ADTOF supervisor once next state is invalid.")
        if self.next_state != self.step.next_state:
            raise ValueError("ADTOF supervisor once must retain its step next state.")
        if self.action_result.action is not self.step.decision.action:
            raise ValueError("ADTOF supervisor once action does not match its decision.")


def run_one_adtof_supervisor_cycle(
    channel: Any,
    *,
    state: ADTOFSupervisorStepState,
    database: ADTOFWorkerCycleDatabase,
    storage_client: ADTOFClaimedTaskSuccessStorageClient,
    work_directory: Path,
    shutdown_waiter: ADTOFShutdownWaiter,
    process_timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    process_runner: ADTOFCPUProcessRunner | None = None,
    jitter_fraction: float = 0.0,
) -> ADTOFSupervisorOnceResult:
    """Run one worker step and apply its single shutdown-aware control action.

    The step owns normal/recovery cadence and failure classification; the
    action adapter owns the one possible wait. This function performs no
    additional I/O after the action result. In particular, a shutdown result
    cannot accidentally begin another RabbitMQ receive or recovery scan.
    """

    step = run_one_adtof_supervisor_step(
        channel,
        state=state,
        database=database,
        storage_client=storage_client,
        work_directory=work_directory,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
        jitter_fraction=jitter_fraction,
    )
    if not isinstance(step, ADTOFSupervisorStepResult):
        raise TypeError("ADTOF supervisor step returned an invalid result.")

    action_result = apply_adtof_supervisor_decision(
        step.decision,
        shutdown_waiter=shutdown_waiter,
    )
    if not isinstance(action_result, ADTOFSupervisorActionResult):
        raise TypeError("ADTOF supervisor action returned an invalid result.")
    return ADTOFSupervisorOnceResult(
        step=step,
        action_result=action_result,
        next_state=step.next_state,
    )
