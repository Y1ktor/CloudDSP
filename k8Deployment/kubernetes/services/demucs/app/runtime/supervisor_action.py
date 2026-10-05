"""Apply one Demucs supervisor decision through an interruptible waiter.

``supervisor_step.py`` decides what should happen next but does not wait, exit,
or manage resource lifecycles. This adapter supplies one future worker-loop
control boundary:

* immediate checks continue without waiting;
* idle/backoff actions call an injected shutdown-aware waiter exactly once;
* fatal configuration returns an explicit exit result without waiting.

The protocol can be implemented by ``threading.Event.wait`` or equivalent. It
returns ``True`` only when shutdown happens before timeout and ``False`` when
the bounded timeout elapses. This module installs no signal handlers, receives
no messages, reconnects no service, mutates no task, runs no model, creates no
loop, and calls no Kubernetes API.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.runtime.supervisor_backoff import DemucsSupervisorAction, DemucsSupervisorDecision


class DemucsShutdownWaiter(Protocol):
    """A future entrypoint's one interruptible timeout primitive.

    ``threading.Event.wait(timeout)`` has this boolean contract. The narrow
    protocol keeps signal/event machinery outside policy/action code and lets
    tests substitute a deterministic non-blocking fake.
    """

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        """Wait no longer than policy permits; report interrupted shutdown."""


class DemucsSupervisorActionOutcome(StrEnum):
    """Control facts a future long-running loop receives after one action."""

    # Either no delay was needed or the injected wait elapsed normally.
    CONTINUE = "continue"
    # Shutdown interrupted idle/backoff before a later cycle begins.
    SHUTDOWN_REQUESTED = "shutdown_requested"
    # Static configuration cannot recover through waiting; exit visibly.
    EXIT_FATAL = "exit_fatal"


@dataclass(frozen=True)
class DemucsSupervisorActionResult:
    """Compact result after applying one prevalidated decision exactly once."""

    outcome: DemucsSupervisorActionOutcome
    action: DemucsSupervisorAction

    def __post_init__(self) -> None:
        """Keep continue/shutdown/fatal pairings explicit for a later loop."""

        if not isinstance(self.outcome, DemucsSupervisorActionOutcome):
            raise TypeError("Demucs supervisor action outcome is invalid.")
        if not isinstance(self.action, DemucsSupervisorAction):
            raise TypeError("Demucs supervisor action result is invalid.")
        if (
            self.outcome is DemucsSupervisorActionOutcome.EXIT_FATAL
            and self.action is not DemucsSupervisorAction.EXIT_FATAL
        ) or (
            self.outcome is DemucsSupervisorActionOutcome.SHUTDOWN_REQUESTED
            and self.action not in (
                DemucsSupervisorAction.WAIT_IDLE,
                DemucsSupervisorAction.RETRY_AFTER_BACKOFF,
            )
        ) or (
            self.outcome is DemucsSupervisorActionOutcome.CONTINUE
            and self.action is DemucsSupervisorAction.EXIT_FATAL
        ):
            raise ValueError("Demucs supervisor action/result pairing is invalid.")


def apply_demucs_supervisor_decision(
    decision: DemucsSupervisorDecision,
    *,
    shutdown_waiter: DemucsShutdownWaiter,
) -> DemucsSupervisorActionResult:
    """Apply one decision without sleeping or performing any lifecycle I/O.

    Decision construction already enforces action/delay pairing. This function
    invokes the injected waiter once only for idle/backoff. Non-boolean waiter
    output is rejected rather than using truthiness that could accidentally
    terminate or resume a future worker.
    """

    if not isinstance(decision, DemucsSupervisorDecision):
        raise TypeError("decision must be DemucsSupervisorDecision.")
    if not callable(getattr(shutdown_waiter, "wait_for_shutdown", None)):
        raise TypeError("shutdown_waiter must provide wait_for_shutdown.")

    if decision.action is DemucsSupervisorAction.EXIT_FATAL:
        return DemucsSupervisorActionResult(
            outcome=DemucsSupervisorActionOutcome.EXIT_FATAL,
            action=decision.action,
        )
    if decision.action is DemucsSupervisorAction.CHECK_IMMEDIATELY:
        return DemucsSupervisorActionResult(
            outcome=DemucsSupervisorActionOutcome.CONTINUE,
            action=decision.action,
        )

    # Only WAIT_IDLE and RETRY_AFTER_BACKOFF remain. The injected waiter—not
    # this adapter—owns actual blocking and future signal-event integration.
    shutdown_requested = shutdown_waiter.wait_for_shutdown(decision.delay_seconds)
    if type(shutdown_requested) is not bool:
        raise TypeError("Demucs shutdown waiter must return a boolean.")
    return DemucsSupervisorActionResult(
        outcome=(
            DemucsSupervisorActionOutcome.SHUTDOWN_REQUESTED
            if shutdown_requested
            else DemucsSupervisorActionOutcome.CONTINUE
        ),
        action=decision.action,
    )
