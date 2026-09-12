"""Choose Basic Pitch's next work source fairly without performing work.

One worker Pod has two independent sources of legitimate work:

* a newly delivered ``basic-pitch.requested`` message from RabbitMQ; and
* a PostgreSQL ``retry_scheduled`` task that has become due after a temporary
  pre-model storage outage.

Neither source is authoritative for the other, and a permanently busy source
must not starve the other.  This pure round-robin policy alternates **attempts**
between them.  If RabbitMQ is empty, the next immediate selection is therefore
due-retry recovery; if no retry is due, the next is RabbitMQ.  A later bounded
runtime will decide whether *both* consecutive attempts were idle before it
uses the existing short supervisor idle delay.

This module has no loop, sleep, clock, RabbitMQ/MinIO/PostgreSQL call, model
invocation, transaction, image entrypoint, or Kubernetes API action.  It only
returns immutable source-selection state, making fairness reviewable before a
runtime holds real credentials or network clients.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BasicPitchWorkSource(StrEnum):
    """The two reviewed inputs a future local Basic Pitch worker may inspect."""

    # The ordinary at-least-once path: parse, first-claim, broker-acknowledge,
    # then execute a committed lease only when that path supplies one.
    RABBITMQ_DELIVERY = "rabbitmq_delivery"
    # The durable retry path: claim one due task and reconstruct the matching
    # immutable published outbox request before it can execute.
    DUE_RETRY_RECOVERY = "due_retry_recovery"


def _other_source(source: BasicPitchWorkSource) -> BasicPitchWorkSource:
    """Return the single alternate source without accepting arbitrary strings."""

    if not isinstance(source, BasicPitchWorkSource):
        raise TypeError("Basic Pitch work source is invalid.")
    if source is BasicPitchWorkSource.RABBITMQ_DELIVERY:
        return BasicPitchWorkSource.DUE_RETRY_RECOVERY
    return BasicPitchWorkSource.RABBITMQ_DELIVERY


@dataclass(frozen=True)
class BasicPitchWorkScheduleState:
    """The one in-memory preference retained between bounded attempts.

    This is deliberately local, tiny, and disposable.  A Pod restart merely
    starts with a RabbitMQ attempt; durable PostgreSQL retry rows and
    RabbitMQ's messages remain discoverable, so correctness never depends on
    persisting this fairness preference.
    """

    next_source: BasicPitchWorkSource = BasicPitchWorkSource.RABBITMQ_DELIVERY

    def __post_init__(self) -> None:
        """Reject a direct string/object before it can steer worker authority."""

        if not isinstance(self.next_source, BasicPitchWorkSource):
            raise TypeError("Basic Pitch next work source is invalid.")


@dataclass(frozen=True)
class BasicPitchWorkSelection:
    """One source to inspect and the preference to use after that attempt.

    The state advances at selection time—not only when work is found.  That is
    what makes an empty preferred source a cheap immediate fallback rather
    than a reason to repeatedly poll it while the other source has backlog.
    A future runtime must execute at most the selected source once, then use
    ``next_state`` for its next bounded iteration.
    """

    source: BasicPitchWorkSource
    next_state: BasicPitchWorkScheduleState

    def __post_init__(self) -> None:
        """Ensure a hand-built result cannot disable the round-robin advance."""

        if not isinstance(self.source, BasicPitchWorkSource) or not isinstance(
            self.next_state, BasicPitchWorkScheduleState
        ):
            raise TypeError("Basic Pitch work selection is invalid.")
        if self.next_state.next_source is not _other_source(self.source):
            raise ValueError("Basic Pitch work selection must advance to the other source.")


def select_next_basic_pitch_work_source(
    state: BasicPitchWorkScheduleState,
) -> BasicPitchWorkSelection:
    """Select one bounded attempt and advance local round-robin preference.

    This function does not know whether the selected RabbitMQ/basic-retry
    attempt will find work, succeed, fail, or lose its lease.  It still moves
    preference to the other source so a high backlog on one side cannot starve
    the other.  The later runtime remains responsible for error classification,
    shutdown, and waiting only when both sources have been checked idle.
    """

    if not isinstance(state, BasicPitchWorkScheduleState):
        raise TypeError("Basic Pitch work schedule state is invalid.")
    source = state.next_source
    return BasicPitchWorkSelection(
        source=source,
        next_state=BasicPitchWorkScheduleState(next_source=_other_source(source)),
    )
