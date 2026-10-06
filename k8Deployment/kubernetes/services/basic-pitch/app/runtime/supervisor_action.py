"""Apply one Basic Pitch supervisor decision through an interruptible waiter.

``supervisor_step.py`` decides *what should happen next* but deliberately
does not sleep or exit.  This narrow adapter supplies the missing control
boundary for a future worker loop:

* immediate progress continues without waiting;
* idle/backoff waits through an injected shutdown-aware waiter; and
* fatal configuration returns an explicit exit result without waiting.

The caller provides the waiter instead of this module importing ``time`` or
installing signal handlers.  A real entrypoint can adapt a ``threading.Event``
or another process-shutdown primitive: the waiter returns ``True`` only when
shutdown was requested before the timeout and ``False`` when the full timeout
elapsed.  Tests can use a deterministic fake.

This is still not a worker loop. It does not execute work, reconnect a broker,
close resources, mutate state, invoke the model, call Kubernetes, or terminate
the process. The eventual entrypoint will call one supervisor step, apply this
result, and own those lifecycle actions explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.runtime.supervisor_backoff import BasicPitchSupervisorAction, BasicPitchSupervisorDecision


class BasicPitchShutdownWaiter(Protocol):
    """A future entrypoint's interruptible timeout primitive.

    ``threading.Event.wait(timeout)`` has this exact boolean shape: it returns
    `True` if the event becomes set before timeout and `False` when the timeout
    expires. The protocol keeps signal/event implementation outside the pure
    worker control contract and makes no promise about a particular threading
    library.
    """

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        """Wait up to one bounded delay; return whether shutdown was requested."""


class BasicPitchSupervisorActionOutcome(StrEnum):
    """The only control facts a future worker loop receives after one decision."""

    # The decision needs no delay, or its bounded delay elapsed normally.
    CONTINUE = "continue"
    # Shutdown interrupted idle/backoff waiting. The loop should close its
    # outer resources and exit cleanly without another work-source attempt.
    SHUTDOWN_REQUESTED = "shutdown_requested"
    # Configuration is deterministically invalid; exit visibly rather than
    # retrying. The caller owns process exit code and resource cleanup.
    EXIT_FATAL = "exit_fatal"


@dataclass(frozen=True)
class BasicPitchSupervisorActionResult:
    """Compact outcome after applying one reviewed decision exactly once."""

    outcome: BasicPitchSupervisorActionOutcome
    action: BasicPitchSupervisorAction

    def __post_init__(self) -> None:
        """Keep fatal/continue/shutdown pairings explicit for the later loop."""

        if not isinstance(self.outcome, BasicPitchSupervisorActionOutcome):
            raise TypeError("Basic Pitch supervisor action outcome is invalid.")
        if not isinstance(self.action, BasicPitchSupervisorAction):
            raise TypeError("Basic Pitch supervisor action result is invalid.")
        if (
            self.outcome is BasicPitchSupervisorActionOutcome.EXIT_FATAL
            and self.action is not BasicPitchSupervisorAction.EXIT_FATAL
        ) or (
            self.outcome is BasicPitchSupervisorActionOutcome.SHUTDOWN_REQUESTED
            and self.action not in (
                BasicPitchSupervisorAction.WAIT_IDLE,
                BasicPitchSupervisorAction.RETRY_AFTER_BACKOFF,
            )
        ) or (
            self.outcome is BasicPitchSupervisorActionOutcome.CONTINUE
            and self.action is BasicPitchSupervisorAction.EXIT_FATAL
        ):
            raise ValueError("Basic Pitch supervisor action/result pairing is invalid.")


def apply_basic_pitch_supervisor_decision(
    decision: BasicPitchSupervisorDecision,
    *,
    shutdown_waiter: BasicPitchShutdownWaiter,
) -> BasicPitchSupervisorActionResult:
    """Apply one delay/exit decision without sleeping or performing lifecycle I/O.

    The decision dataclass has already enforced finite delay/action pairing.
    This function calls the injected waiter exactly once only for idle/backoff
    decisions. A true return means the future loop must stop before another
    iteration; a false return means the reviewed delay elapsed normally and it
    may continue. Invalid waiter output is rejected rather than treated as a
    truthy shutdown signal.
    """

    if not isinstance(decision, BasicPitchSupervisorDecision):
        raise TypeError("decision must be BasicPitchSupervisorDecision.")
    if not callable(getattr(shutdown_waiter, "wait_for_shutdown", None)):
        raise TypeError("shutdown_waiter must provide wait_for_shutdown.")

    if decision.action is BasicPitchSupervisorAction.EXIT_FATAL:
        return BasicPitchSupervisorActionResult(
            outcome=BasicPitchSupervisorActionOutcome.EXIT_FATAL,
            action=decision.action,
        )
    if decision.action is BasicPitchSupervisorAction.CHECK_IMMEDIATELY:
        return BasicPitchSupervisorActionResult(
            outcome=BasicPitchSupervisorActionOutcome.CONTINUE,
            action=decision.action,
        )

    # The decision's constructor guarantees the only two remaining actions
    # have positive, bounded delays. The injected waiter—not this module—owns
    # actual blocking and signal integration.
    shutdown_requested = shutdown_waiter.wait_for_shutdown(decision.delay_seconds)
    if type(shutdown_requested) is not bool:
        raise TypeError("Basic Pitch shutdown waiter must return a boolean.")
    return BasicPitchSupervisorActionResult(
        outcome=(
            BasicPitchSupervisorActionOutcome.SHUTDOWN_REQUESTED
            if shutdown_requested
            else BasicPitchSupervisorActionOutcome.CONTINUE
        ),
        action=decision.action,
    )
