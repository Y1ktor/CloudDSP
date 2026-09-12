"""Manual-ack AMQP adapter for one Basic Pitch request delivery.

This is the first Pika-shaped transport boundary for the Basic Pitch worker.
It reads at most one message from the already-verified private request queue
with ``auto_ack=False``, hands the raw envelope to the parser-plus-first-claim
bridge, and chooses the reviewed RabbitMQ action only after that bridge returns.

It deliberately does not open a connection, load settings, declare topology,
start a loop, publish a retry, recover expired tasks, read MinIO, invoke Basic
Pitch, or create Kubernetes resources. Keeping this one-delivery state machine
small makes its at-least-once acknowledgement rules explicit and testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.amqp_connection import BASIC_PITCH_REQUEST_QUEUE
from app.basic_pitch_requested_message import BasicPitchRequestContractError, BasicPitchRequestedMessage
from app.delivery_claim import BasicPitchDeliveryClaim, claim_basic_pitch_requested_delivery
from app.first_claim import BasicPitchTaskClaimDatabase
from app.task_lease import (
    BasicPitchTaskClaimDisposition,
    BasicPitchTaskLease,
    BasicPitchTaskLeaseProtocolError,
)


class BasicPitchAMQPUnavailable(RuntimeError):
    """A retryable receive/ack/nack failure without raw RabbitMQ diagnostics.

    The future supervisor closes the broken channel/connection and reconnects
    with bounded backoff. Any delivery whose acknowledgement failed remains
    unacknowledged, allowing RabbitMQ redelivery and PostgreSQL idempotency to
    preserve work rather than relying on a potentially failed client action.
    """


class BasicPitchConsumeOneOutcome(StrEnum):
    """The complete normal outcomes from one manual-ack receive attempt."""

    # No delivery was assigned, so neither the parser/claim bridge nor an ack
    # method ran. A later supervisor may use this only as its bounded idle cue.
    IDLE = "idle"
    # The task claim committed a newly owned lease and RabbitMQ accepted ack.
    # Only this outcome exposes a lease to the later execution handoff.
    ACKNOWLEDGED_LEASE = "acknowledged_lease"
    # A committed duplicate/stale classification needs no CPU/MinIO work, but
    # it is safe to acknowledge because PostgreSQL has already decided it.
    ACKNOWLEDGED_NO_WORK = "acknowledged_no_work"
    # A malformed envelope/properties/body cannot become valid on redelivery,
    # so it is nacked without requeue to enter the preconfigured DLQ path.
    MALFORMED_REJECTED = "malformed_rejected"


@dataclass(frozen=True)
class BasicPitchConsumeOneResult:
    """One completed broker action plus optional post-ack execution evidence.

    The lease/message pairing is deliberately strict: a later execution layer
    can act only when PostgreSQL committed the lease *and* RabbitMQ accepted the
    acknowledgement. The message has already passed the strict parser, so it
    carries stable stem size/checksum evidence without retaining raw AMQP body
    or properties. Idle/no-work/malformed outcomes carry neither value and
    therefore cannot accidentally trigger a duplicate model run.
    """

    outcome: BasicPitchConsumeOneOutcome
    lease: BasicPitchTaskLease | None = None
    message: BasicPitchRequestedMessage | None = None

    def __post_init__(self) -> None:
        """Reject ambiguous outcome/lease/message combinations at construction."""

        has_lease = self.lease is not None
        has_message = self.message is not None
        requires_lease = self.outcome is BasicPitchConsumeOneOutcome.ACKNOWLEDGED_LEASE
        if has_lease != requires_lease or has_message != requires_lease:
            raise ValueError("Basic Pitch receive result evidence does not match its outcome.")
        if has_message and not isinstance(self.message, BasicPitchRequestedMessage):
            raise ValueError("Basic Pitch receive result message is invalid.")


def consume_one_basic_pitch_requested_delivery(
    channel: Any,
    *,
    database: BasicPitchTaskClaimDatabase,
) -> BasicPitchConsumeOneResult:
    """Receive and resolve at most one delivery with the reviewed ack ordering.

    A normal bridge return proves the task claim transaction has committed, so
    a new lease, duplicate, or stale outcome can be acknowledged. A malformed
    request is permanently nacked without requeue. Database availability,
    durable identity disagreement, malformed claim result, and unexpected
    bridge exceptions intentionally propagate with no ack/nack; a later
    supervisor must reconnect and preserve at-least-once redelivery semantics.
    """

    if not callable(getattr(channel, "basic_get", None)):
        raise TypeError("channel must provide basic_get.")
    try:
        method_frame, properties, body = channel.basic_get(
            queue=BASIC_PITCH_REQUEST_QUEUE,
            auto_ack=False,
        )
    except Exception as error:
        raise BasicPitchAMQPUnavailable("RabbitMQ Basic Pitch receive is unavailable.") from error

    if method_frame is None:
        return BasicPitchConsumeOneResult(outcome=BasicPitchConsumeOneOutcome.IDLE)
    # A delivery tag is RabbitMQ's per-channel acknowledgement handle. Reject
    # a malformed frame before the bridge can commit a claim that this adapter
    # would be unable to acknowledge on the same channel.
    try:
        delivery_tag = method_frame.delivery_tag
    except (AttributeError, TypeError) as error:
        raise BasicPitchAMQPUnavailable("RabbitMQ Basic Pitch delivery is unavailable.") from error
    if type(delivery_tag) is not int or delivery_tag < 1:
        raise BasicPitchAMQPUnavailable("RabbitMQ Basic Pitch delivery is unavailable.")

    try:
        # The bridge validates exchange/routing/properties/body before opening
        # its short PostgreSQL claim transaction. No delivery tag or channel is
        # passed across that trust boundary.
        delivery_claim = claim_basic_pitch_requested_delivery(
            database=database,
            delivery_exchange=method_frame.exchange,
            delivery_routing_key=method_frame.routing_key,
            properties=properties,
            body=body,
        )
    except BasicPitchRequestContractError:
        # This is the one permanent transport-input category. Do not catch all
        # errors here: database/identity failures must stay unacknowledged for
        # redelivery rather than being lost to the DLQ as malformed data.
        if not callable(getattr(channel, "basic_nack", None)):
            raise TypeError("channel must provide basic_nack for malformed deliveries.")
        try:
            channel.basic_nack(delivery_tag, requeue=False)
        except Exception as error:
            raise BasicPitchAMQPUnavailable("RabbitMQ Basic Pitch rejection is unavailable.") from error
        return BasicPitchConsumeOneResult(outcome=BasicPitchConsumeOneOutcome.MALFORMED_REJECTED)

    lease, message = _claimed_execution_evidence_or_error(delivery_claim)
    if not callable(getattr(channel, "basic_ack", None)):
        raise TypeError("channel must provide basic_ack for committed deliveries.")
    try:
        # An ack failure deliberately does not undo the committed task row. A
        # future redelivery becomes a PostgreSQL duplicate instead of creating
        # a second Basic Pitch run, which is the essential at-least-once rule.
        channel.basic_ack(delivery_tag)
    except Exception as error:
        raise BasicPitchAMQPUnavailable("RabbitMQ Basic Pitch acknowledgement is unavailable.") from error
    return BasicPitchConsumeOneResult(
        outcome=(
            BasicPitchConsumeOneOutcome.ACKNOWLEDGED_LEASE
            if lease is not None
            else BasicPitchConsumeOneOutcome.ACKNOWLEDGED_NO_WORK
        ),
        lease=lease,
        message=message,
    )


def _claimed_execution_evidence_or_error(
    delivery_claim: BasicPitchDeliveryClaim,
) -> tuple[BasicPitchTaskLease | None, BasicPitchRequestedMessage | None]:
    """Extract a complete lease/message pair before any acknowledgement.

    The pure first-claim SQL normally guarantees that a `claimed` disposition
    includes one token/expiry-bearing lease, while the bridge normally
    guarantees its parser-validated message. Rechecking both values here
    prevents a future regression or unsafe mock from acknowledging a delivery
    and then losing the exact provenance/ownership evidence Basic Pitch needs.
    """

    if not isinstance(delivery_claim, BasicPitchDeliveryClaim):
        raise TypeError("delivery_claim must be BasicPitchDeliveryClaim.")
    if delivery_claim.task_claim.disposition is not BasicPitchTaskClaimDisposition.CLAIMED:
        return None, None
    lease = delivery_claim.task_claim.lease
    message = delivery_claim.message
    if not isinstance(lease, BasicPitchTaskLease) or not isinstance(message, BasicPitchRequestedMessage):
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    return lease, message
