"""Run ADTOF supervisor cycles until shutdown or fatal configuration ends them.

This is the first intentional persistent loop in the worker source. It does
not build a RabbitMQ/PostgreSQL/MinIO client; the caller supplies those already
prepared dependencies and retains responsibility for closing them outside this
function. Each pass delegates to the one-step supervisor runner, whose action
has already performed at most one shutdown-aware wait.

Before every new worker cycle, the loop also checks the same shutdown waiter
with a zero-second wait. This matters when SIGTERM arrives during CPU work and
the completed step selects immediate progress: no extra RabbitMQ receive or
recovery scan may begin before the process observes that already-set event.
The loop otherwise continues only after the runner returns ``continue`` and
stops visibly on ``shutdown_requested`` or ``exit_fatal``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.adtof_cpu_process import DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS, ADTOFCPUProcessRunner
from app.claimed_task_success import ADTOFClaimedTaskSuccessStorageClient
from app.shutdown_event import ADTOFShutdownWaiter
from app.supervisor_action import ADTOFSupervisorActionOutcome
from app.supervisor_once import ADTOFSupervisorOnceResult, run_one_adtof_supervisor_cycle
from app.supervisor_step import ADTOFSupervisorStepState
from app.worker_cycle import ADTOFWorkerCycleDatabase


class ADTOFSupervisorLoopOutcome(StrEnum):
    """The two terminal control facts the caller must handle after the loop."""

    SHUTDOWN_REQUESTED = "shutdown_requested"
    EXIT_FATAL = "exit_fatal"


@dataclass(frozen=True)
class ADTOFSupervisorLoopResult:
    """Terminal loop result without retaining external clients or raw work data."""

    outcome: ADTOFSupervisorLoopOutcome
    final_state: ADTOFSupervisorStepState
    completed_cycles: int
    final_cycle: ADTOFSupervisorOnceResult | None = None

    def __post_init__(self) -> None:
        """Keep pre-cycle shutdown and runner-terminal results distinguishable."""

        if not isinstance(self.outcome, ADTOFSupervisorLoopOutcome):
            raise TypeError("ADTOF supervisor loop outcome is invalid.")
        if not isinstance(self.final_state, ADTOFSupervisorStepState):
            raise TypeError("ADTOF supervisor loop final state is invalid.")
        if type(self.completed_cycles) is not int or self.completed_cycles < 0:
            raise ValueError("ADTOF supervisor loop completed cycle count is invalid.")
        if self.final_cycle is None:
            if self.completed_cycles != 0 or self.outcome is not ADTOFSupervisorLoopOutcome.SHUTDOWN_REQUESTED:
                raise ValueError("ADTOF supervisor loop pre-cycle shutdown result is invalid.")
            return
        if not isinstance(self.final_cycle, ADTOFSupervisorOnceResult):
            raise TypeError("ADTOF supervisor loop final cycle is invalid.")
        if self.final_state != self.final_cycle.next_state:
            raise ValueError("ADTOF supervisor loop final state must match its final cycle.")
        if self.outcome is ADTOFSupervisorLoopOutcome.EXIT_FATAL:
            expected_action_outcomes = {ADTOFSupervisorActionOutcome.EXIT_FATAL}
        else:
            # A signal can arrive after a previous `continue` action but before
            # the next cycle's zero-second event check. That last completed
            # cycle remains useful audit evidence even though it did not itself
            # return `shutdown_requested`.
            expected_action_outcomes = {
                ADTOFSupervisorActionOutcome.CONTINUE,
                ADTOFSupervisorActionOutcome.SHUTDOWN_REQUESTED,
            }
        if self.final_cycle.action_result.outcome not in expected_action_outcomes:
            raise ValueError("ADTOF supervisor loop terminal action does not match its outcome.")


def _shutdown_requested_before_cycle(shutdown_waiter: ADTOFShutdownWaiter) -> bool:
    """Observe a prior signal without adding a delay or starting any worker work."""

    if not isinstance(shutdown_waiter, ADTOFShutdownWaiter):
        raise TypeError("shutdown_waiter must be ADTOFShutdownWaiter.")
    requested = shutdown_waiter.wait_for_shutdown(0.0)
    if type(requested) is not bool:
        raise TypeError("ADTOF shutdown waiter must return a boolean.")
    return requested


def run_adtof_supervisor_until_stop(
    channel: Any,
    *,
    initial_state: ADTOFSupervisorStepState,
    database: ADTOFWorkerCycleDatabase,
    storage_client: ADTOFClaimedTaskSuccessStorageClient,
    work_directory: Path,
    shutdown_waiter: ADTOFShutdownWaiter,
    process_timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    process_runner: ADTOFCPUProcessRunner | None = None,
    jitter_fraction: float = 0.0,
) -> ADTOFSupervisorLoopResult:
    """Continue bounded cycles until shutdown is observed or configuration exits.

    The loop intentionally catches no exception: the one-step runner already
    converts only reviewed operational failures into action results, while an
    unclassified failure must escape to the outer process/lifecycle boundary.
    No client is created, reconnected, closed, or inspected here. Its owner
    decides client lifecycle around this loop after the terminal result returns.
    """

    if not isinstance(initial_state, ADTOFSupervisorStepState):
        raise TypeError("initial_state must be ADTOFSupervisorStepState.")
    current_state = initial_state
    completed_cycles = 0
    last_cycle: ADTOFSupervisorOnceResult | None = None

    while True:
        if _shutdown_requested_before_cycle(shutdown_waiter):
            return ADTOFSupervisorLoopResult(
                outcome=ADTOFSupervisorLoopOutcome.SHUTDOWN_REQUESTED,
                final_state=current_state,
                completed_cycles=completed_cycles,
                # A shutdown observed after a `continue` cycle still retains
                # that last completed cycle. The special zero-cycle shape is
                # reserved for a signal present before the loop starts.
                final_cycle=last_cycle,
            )

        cycle = run_one_adtof_supervisor_cycle(
            channel,
            state=current_state,
            database=database,
            storage_client=storage_client,
            work_directory=work_directory,
            shutdown_waiter=shutdown_waiter,
            process_timeout_seconds=process_timeout_seconds,
            process_runner=process_runner,
            jitter_fraction=jitter_fraction,
        )
        if not isinstance(cycle, ADTOFSupervisorOnceResult):
            raise TypeError("ADTOF supervisor runner returned an invalid result.")
        completed_cycles += 1
        current_state = cycle.next_state
        if cycle.action_result.outcome is ADTOFSupervisorActionOutcome.CONTINUE:
            last_cycle = cycle
            continue
        if cycle.action_result.outcome is ADTOFSupervisorActionOutcome.SHUTDOWN_REQUESTED:
            return ADTOFSupervisorLoopResult(
                outcome=ADTOFSupervisorLoopOutcome.SHUTDOWN_REQUESTED,
                final_state=current_state,
                completed_cycles=completed_cycles,
                final_cycle=cycle,
            )
        if cycle.action_result.outcome is ADTOFSupervisorActionOutcome.EXIT_FATAL:
            return ADTOFSupervisorLoopResult(
                outcome=ADTOFSupervisorLoopOutcome.EXIT_FATAL,
                final_state=current_state,
                completed_cycles=completed_cycles,
                final_cycle=cycle,
            )
        raise TypeError("ADTOF supervisor action outcome is invalid.")
