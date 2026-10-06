"""Run Demucs supervisor cycles until shutdown or fatal configuration stops them.

This is the first intentional persistent loop in the Demucs worker source. It
creates no RabbitMQ/PostgreSQL/MinIO client: callers supply already-prepared
dependencies and retain responsibility for closing them outside this function.
Each pass delegates to the one-step runner, whose action performs at most one
shutdown-aware wait.

Before every new cycle, the loop checks the same shutdown Event with a
zero-second wait. This matters when SIGTERM arrives during inference and a
completed step selected immediate progress: no extra RabbitMQ receive or
recovery scan may begin before the worker observes that already-set Event.
Only a returned ``continue`` repeats; ``shutdown_requested`` and ``exit_fatal``
stop visibly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from app.artifacts.demucs_artifact_upload import DemucsPutObjectClient
from app.processing.demucs_process import DemucsProcessRunner
from app.processing.ffprobe_process import DemucsFFprobeRunner
from app.artifacts.planned_stem_upload import DemucsPlannedStemUploader
from app.db.pre_model_failure_transition import DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS
from app.db.running_failure_transition import DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS
from app.runtime.shutdown_event import DemucsShutdownWaiter
from app.processing.source_preflight import DemucsSourcePreflightClient
from app.runtime.supervisor_action import DemucsSupervisorActionOutcome
from app.runtime.supervisor_once import DemucsSupervisorOnceResult, run_one_demucs_supervisor_cycle
from app.runtime.supervisor_step import DemucsSupervisorStepState
from app.runtime.worker_cycle import DemucsWorkerCycleDatabase


class DemucsSupervisorLoopOutcome(StrEnum):
    """Terminal control facts an entrypoint must handle after the loop ends."""

    SHUTDOWN_REQUESTED = "shutdown_requested"
    EXIT_FATAL = "exit_fatal"


@dataclass(frozen=True)
class DemucsSupervisorLoopResult:
    """Terminal loop observation without external clients or raw work details."""

    outcome: DemucsSupervisorLoopOutcome
    final_state: DemucsSupervisorStepState
    completed_cycles: int
    final_cycle: DemucsSupervisorOnceResult | None = None

    def __post_init__(self) -> None:
        """Distinguish pre-cycle shutdown from a runner-terminal result."""

        if not isinstance(self.outcome, DemucsSupervisorLoopOutcome):
            raise TypeError("Demucs supervisor loop outcome is invalid.")
        if not isinstance(self.final_state, DemucsSupervisorStepState):
            raise TypeError("Demucs supervisor loop final state is invalid.")
        if type(self.completed_cycles) is not int or self.completed_cycles < 0:
            raise ValueError("Demucs supervisor loop completed cycle count is invalid.")
        if self.final_cycle is None:
            if self.completed_cycles != 0 or self.outcome is not DemucsSupervisorLoopOutcome.SHUTDOWN_REQUESTED:
                raise ValueError("Demucs supervisor loop pre-cycle shutdown result is invalid.")
            return
        if not isinstance(self.final_cycle, DemucsSupervisorOnceResult):
            raise TypeError("Demucs supervisor loop final cycle is invalid.")
        if self.final_state != self.final_cycle.next_state:
            raise ValueError("Demucs supervisor loop final state must match its final cycle.")
        if self.outcome is DemucsSupervisorLoopOutcome.EXIT_FATAL:
            expected_action_outcomes = {DemucsSupervisorActionOutcome.EXIT_FATAL}
        else:
            # SIGTERM may arrive after a `continue` cycle and before the next
            # zero-time event check. Retain that last completed cycle as audit
            # evidence even though its own action did not report shutdown.
            expected_action_outcomes = {
                DemucsSupervisorActionOutcome.CONTINUE,
                DemucsSupervisorActionOutcome.SHUTDOWN_REQUESTED,
            }
        if self.final_cycle.action_result.outcome not in expected_action_outcomes:
            raise ValueError("Demucs supervisor loop terminal action does not match its outcome.")


def _shutdown_requested_before_cycle(shutdown_waiter: DemucsShutdownWaiter) -> bool:
    """Observe an already-set Event without delaying or beginning worker work."""

    if not isinstance(shutdown_waiter, DemucsShutdownWaiter):
        raise TypeError("shutdown_waiter must be DemucsShutdownWaiter.")
    requested = shutdown_waiter.wait_for_shutdown(0.0)
    if type(requested) is not bool:
        raise TypeError("Demucs shutdown waiter must return a boolean.")
    return requested


def run_demucs_supervisor_until_stop(
    channel: Any,
    *,
    initial_state: DemucsSupervisorStepState,
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
) -> DemucsSupervisorLoopResult:
    """Repeat bounded cycles until shutdown is observed or configuration exits.

    No exception is caught here. The one-step runner already converts only
    reviewed availability/configuration failures into actions; unknown errors
    must reach the outer process/lifecycle owner. No client is created,
    reconnected, closed, or inspected in this loop.
    """

    if not isinstance(initial_state, DemucsSupervisorStepState):
        raise TypeError("initial_state must be DemucsSupervisorStepState.")
    current_state = initial_state
    completed_cycles = 0
    last_cycle: DemucsSupervisorOnceResult | None = None

    while True:
        if _shutdown_requested_before_cycle(shutdown_waiter):
            return DemucsSupervisorLoopResult(
                outcome=DemucsSupervisorLoopOutcome.SHUTDOWN_REQUESTED,
                final_state=current_state,
                completed_cycles=completed_cycles,
                # Zero cycles has no final evidence. A post-continue signal
                # retains its last completed cycle for accurate observation.
                final_cycle=last_cycle,
            )

        cycle = run_one_demucs_supervisor_cycle(
            channel,
            state=current_state,
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=work_directory,
            shutdown_waiter=shutdown_waiter,
            ffprobe_runner=ffprobe_runner,
            demucs_runner=demucs_runner,
            uploader=uploader,
            event_id_factory=event_id_factory,
            pre_model_retry_after_seconds=pre_model_retry_after_seconds,
            running_retry_after_seconds=running_retry_after_seconds,
            jitter_fraction=jitter_fraction,
        )
        if not isinstance(cycle, DemucsSupervisorOnceResult):
            raise TypeError("Demucs supervisor runner returned an invalid result.")
        completed_cycles += 1
        current_state = cycle.next_state
        if cycle.action_result.outcome is DemucsSupervisorActionOutcome.CONTINUE:
            last_cycle = cycle
            continue
        if cycle.action_result.outcome is DemucsSupervisorActionOutcome.SHUTDOWN_REQUESTED:
            return DemucsSupervisorLoopResult(
                outcome=DemucsSupervisorLoopOutcome.SHUTDOWN_REQUESTED,
                final_state=current_state,
                completed_cycles=completed_cycles,
                final_cycle=cycle,
            )
        if cycle.action_result.outcome is DemucsSupervisorActionOutcome.EXIT_FATAL:
            return DemucsSupervisorLoopResult(
                outcome=DemucsSupervisorLoopOutcome.EXIT_FATAL,
                final_state=current_state,
                completed_cycles=completed_cycles,
                final_cycle=cycle,
            )
        raise TypeError("Demucs supervisor action outcome is invalid.")
