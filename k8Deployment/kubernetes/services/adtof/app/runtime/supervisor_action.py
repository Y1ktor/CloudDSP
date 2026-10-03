"""Apply one ADTOF supervisor decision through an interruptible waiter.

``supervisor_step.py`` decides what should happen next but intentionally does
not wait, exit, or manage resource lifecycles. This narrow adapter supplies one
future worker-loop control boundary:

* immediate checks continue without waiting;
* idle/backoff actions call an injected shutdown-aware waiter once; and
* fatal configuration returns an explicit exit result without waiting.

The injected protocol can be implemented by ``threading.Event.wait`` or an
equivalent entrypoint primitive. It returns ``True`` only when shutdown happens
before the timeout, and ``False`` after the whole bounded timeout. This module
does not install signal handlers, receive messages, reconnect RabbitMQ, modify
PostgreSQL, invoke ADTOF, create a loop, or call Kubernetes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.runtime.supervisor_backoff import ADTOFSupervisorAction, ADTOFSupervisorDecision


class ADTOFShutdownWaiter(Protocol):
    """A future entrypoint's one interruptible timeout primitive.

    ``threading.Event.wait(timeout)`` has this boolean contract. Keeping the
    interface narrow lets source tests use a deterministic fake and keeps all
    process-signal machinery outside the policy/action boundaries.
    """

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        """Wait no longer than the policy delay; report interrupted shutdown."""


class ADTOFSupervisorActionOutcome(StrEnum):
    """The control facts a future long-running loop receives after one action."""

    # Either no delay was needed or the injected wait elapsed normally.
    CONTINUE = "continue"
    # Shutdown interrupted an idle/backoff wait before a subsequent iteration.
    SHUTDOWN_REQUESTED = "shutdown_requested"
    # Static configuration cannot be repaired by waiting; exit visibly.
    EXIT_FATAL = "exit_fatal"


@dataclass(frozen=True)
class ADTOFSupervisorActionResult:
    """Compact result after applying one prevalidated decision exactly once."""

    outcome: ADTOFSupervisorActionOutcome
    action: ADTOFSupervisorAction

    def __post_init__(self) -> None:
        """Keep continue/shutdown/fatal result pairings explicit for a loop."""

        if not isinstance(self.outcome, ADTOFSupervisorActionOutcome):
            raise TypeError("ADTOF supervisor action outcome is invalid.")
        if not isinstance(self.action, ADTOFSupervisorAction):
            raise TypeError("ADTOF supervisor action result is invalid.")
        if (
            self.outcome is ADTOFSupervisorActionOutcome.EXIT_FATAL
            and self.action is not ADTOFSupervisorAction.EXIT_FATAL
        ) or (
            self.outcome is ADTOFSupervisorActionOutcome.SHUTDOWN_REQUESTED
            and self.action not in (
                ADTOFSupervisorAction.WAIT_IDLE,
                ADTOFSupervisorAction.RETRY_AFTER_BACKOFF,
            )
        ) or (
            self.outcome is ADTOFSupervisorActionOutcome.CONTINUE
            and self.action is ADTOFSupervisorAction.EXIT_FATAL
        ):
            raise ValueError("ADTOF supervisor action/result pairing is invalid.")


def apply_adtof_supervisor_decision(
    decision: ADTOFSupervisorDecision,
    *,
    shutdown_waiter: ADTOFShutdownWaiter,
) -> ADTOFSupervisorActionResult:
    """Apply one decision without opening clients or performing lifecycle work.

    Decision construction already guarantees the action/delay contract. This
    function calls the injected waiter exactly once only for idle/backoff
    actions. Any non-boolean waiter output is rejected instead of relying on
    Python truthiness to accidentally terminate or resume a future worker.
    """

    if not isinstance(decision, ADTOFSupervisorDecision):
        raise TypeError("decision must be ADTOFSupervisorDecision.")
    if not callable(getattr(shutdown_waiter, "wait_for_shutdown", None)):
        raise TypeError("shutdown_waiter must provide wait_for_shutdown.")

    if decision.action is ADTOFSupervisorAction.EXIT_FATAL:
        return ADTOFSupervisorActionResult(
            outcome=ADTOFSupervisorActionOutcome.EXIT_FATAL,
            action=decision.action,
        )
    if decision.action is ADTOFSupervisorAction.CHECK_IMMEDIATELY:
        return ADTOFSupervisorActionResult(
            outcome=ADTOFSupervisorActionOutcome.CONTINUE,
            action=decision.action,
        )

    # The only remaining actions are WAIT_IDLE and RETRY_AFTER_BACKOFF. The
    # injected waiter owns actual blocking and signal/event integration.
    shutdown_requested = shutdown_waiter.wait_for_shutdown(decision.delay_seconds)
    if type(shutdown_requested) is not bool:
        raise TypeError("ADTOF shutdown waiter must return a boolean.")
    return ADTOFSupervisorActionResult(
        outcome=(
            ADTOFSupervisorActionOutcome.SHUTDOWN_REQUESTED
            if shutdown_requested
            else ADTOFSupervisorActionOutcome.CONTINUE
        ),
        action=decision.action,
    )
