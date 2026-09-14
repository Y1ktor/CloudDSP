"""Manual-ack AMQP adapter for one ADTOF request delivery.

This Pika-shaped transport boundary reads at most one message from the
already-verified private ADTOF request queue with ``auto_ack=False``. It gives
the raw envelope to the parser-plus-first-claim bridge and selects the reviewed
RabbitMQ action only after the bridge has returned a committed durable result.

It does not open a connection, load settings, declare topology, start a loop,
publish a retry, recover expired tasks, read MinIO, invoke ADTOF, or change a
Kubernetes resource. Keeping this single-delivery state machine separate makes
the at-least-once acknowledgement rules small, explicit, and testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.adtof_requested_message import ADTOFRequestContractError, ADTOFRequestedMessage
from app.amqp_connection import ADTOF_REQUEST_QUEUE
from app.delivery_claim import ADTOFDeliveryClaim, claim_adtof_requested_delivery
from app.first_claim import ADTOFTaskClaimDatabase
from app.task_claim import (
    ADTOFTaskClaimDisposition,
    ADTOFTaskClaimProtocolError,
    ADTOFTaskLease,
)


class ADTOFAMQPUnavailable(RuntimeError):
    """A retryable receive/ack/nack failure without RabbitMQ diagnostics.

    A future supervisor must close the affected channel/connection and retry
    with bounded backoff. A delivery whose acknowledgement action failed stays
    unacknowledged, allowing RabbitMQ redelivery plus PostgreSQL idempotency to
    preserve the work rather than trusting an uncertain client-side outcome.
    """


class ADTOFConsumeOneOutcome(StrEnum):
    """The complete normal outcomes from one manual acknowledgement attempt."""

    # RabbitMQ assigned no message, so no parser/claim/broker-ack action ran.
    # A later supervisor can use only this result as its bounded idle cue.
    IDLE = "idle"
    # PostgreSQL committed a new drums-task lease and RabbitMQ accepted the
    # acknowledgement. Only this state exposes execution ownership evidence.
    ACKNOWLEDGED_LEASE = "acknowledged_lease"
    # PostgreSQL has already committed a duplicate/stale fact. It needs no
    # MinIO/CPU work and the historic delivery can be safely acknowledged.
    ACKNOWLEDGED_NO_WORK = "acknowledged_no_work"
    # A malformed route/properties/body cannot become valid on redelivery, so
    # it is nacked without requeueing into topology's configured DLQ path.
    MALFORMED_REJECTED = "malformed_rejected"


@dataclass(frozen=True)
class ADTOFConsumeOneResult:
    """One completed broker action with optional post-ack execution evidence.

    The lease/message pairing is intentionally strict. A later MinIO/ADTOF
    execution handoff may proceed only after both the durable claim and the
    RabbitMQ acknowledgement have succeeded. Idle/no-work/malformed outcomes
    carry no evidence, so they cannot accidentally start a duplicate CPU job.
    """

    outcome: ADTOFConsumeOneOutcome
    lease: ADTOFTaskLease | None = None
    message: ADTOFRequestedMessage | None = None

    def __post_init__(self) -> None:
        """Reject ambiguous result/evidence pairings at construction time."""

        has_lease = self.lease is not None
        has_message = self.message is not None
        requires_lease = self.outcome is ADTOFConsumeOneOutcome.ACKNOWLEDGED_LEASE
        if has_lease != requires_lease or has_message != requires_lease:
            raise ValueError("ADTOF receive result evidence does not match its outcome.")
        if has_message and not isinstance(self.message, ADTOFRequestedMessage):
            raise ValueError("ADTOF receive result message is invalid.")


def consume_one_adtof_requested_delivery(
    channel: Any,
    *,
    database: ADTOFTaskClaimDatabase,
) -> ADTOFConsumeOneResult:
    """Receive and resolve one delivery with the reviewed acknowledgement order.

    A normal bridge return proves its first-claim transaction committed, making
    a new lease, duplicate, or stale outcome safe to acknowledge. A malformed
    request is permanently nacked without requeue. Database unavailability,
    durable evidence disagreement, malformed claim evidence, and unexpected
    bridge failures deliberately propagate without ack/nack, preserving
    at-least-once redelivery for the later supervisor to recover.
    """

    if not callable(getattr(channel, "basic_get", None)):
        raise TypeError("channel must provide basic_get.")
    try:
        method_frame, properties, body = channel.basic_get(
            queue=ADTOF_REQUEST_QUEUE,
            auto_ack=False,
        )
    except Exception as error:
        raise ADTOFAMQPUnavailable("RabbitMQ ADTOF receive is unavailable.") from error

    if method_frame is None:
        return ADTOFConsumeOneResult(outcome=ADTOFConsumeOneOutcome.IDLE)

    # Delivery tags are RabbitMQ's per-channel acknowledgement handles. Validate
    # one before any claim transaction, otherwise the adapter might commit work
    # it cannot acknowledge (or later prove it owns) on this same channel.
    try:
        delivery_tag = method_frame.delivery_tag
    except (AttributeError, TypeError) as error:
        raise ADTOFAMQPUnavailable("RabbitMQ ADTOF delivery is unavailable.") from error
    if type(delivery_tag) is not int or delivery_tag < 1:
        raise ADTOFAMQPUnavailable("RabbitMQ ADTOF delivery is unavailable.")

    try:
        # The bridge validates exchange/routing/properties/body first, then
        # commits its short first-claim transaction. It receives no channel or
        # tag, so it cannot make a transport decision on this adapter's behalf.
        delivery_claim = claim_adtof_requested_delivery(
            database=database,
            delivery_exchange=method_frame.exchange,
            delivery_routing_key=method_frame.routing_key,
            properties=properties,
            body=body,
        )
    except ADTOFRequestContractError:
        # Only contract errors are permanent at this point. Do not catch every
        # exception: a database/integrity failure must remain unacknowledged for
        # redelivery, not be silently rerouted as malformed client data.
        if not callable(getattr(channel, "basic_nack", None)):
            raise TypeError("channel must provide basic_nack for malformed deliveries.")
        try:
            channel.basic_nack(delivery_tag, requeue=False)
        except Exception as error:
            raise ADTOFAMQPUnavailable("RabbitMQ ADTOF rejection is unavailable.") from error
        return ADTOFConsumeOneResult(outcome=ADTOFConsumeOneOutcome.MALFORMED_REJECTED)

    lease, message = _claimed_execution_evidence_or_error(delivery_claim)
    if not callable(getattr(channel, "basic_ack", None)):
        raise TypeError("channel must provide basic_ack for committed deliveries.")
    try:
        # An uncertain ack does not undo PostgreSQL's committed task row. A
        # later redelivery finds the durable duplicate, so this adapter never
        # launches a second ADTOF drums transcription for the same request.
        channel.basic_ack(delivery_tag)
    except Exception as error:
        raise ADTOFAMQPUnavailable("RabbitMQ ADTOF acknowledgement is unavailable.") from error
    return ADTOFConsumeOneResult(
        outcome=(
            ADTOFConsumeOneOutcome.ACKNOWLEDGED_LEASE
            if lease is not None
            else ADTOFConsumeOneOutcome.ACKNOWLEDGED_NO_WORK
        ),
        lease=lease,
        message=message,
    )


def _claimed_execution_evidence_or_error(
    delivery_claim: ADTOFDeliveryClaim,
) -> tuple[ADTOFTaskLease | None, ADTOFRequestedMessage | None]:
    """Extract a complete claimed lease/message pair before acknowledgement.

    The parser and pure first-claim decision normally guarantee that a
    ``claimed`` result has one validated message and one token/expiry-bearing
    lease. Rechecking both here prevents a future bridge regression or unsafe
    mock from acknowledging a delivery before the exact ADTOF ownership and
    stem-provenance evidence has safely crossed this transport boundary.
    """

    if not isinstance(delivery_claim, ADTOFDeliveryClaim):
        raise TypeError("delivery_claim must be ADTOFDeliveryClaim.")
    if delivery_claim.task_claim.disposition is not ADTOFTaskClaimDisposition.CLAIMED:
        return None, None
    lease = delivery_claim.task_claim.lease
    message = delivery_claim.message
    if not isinstance(lease, ADTOFTaskLease) or not isinstance(message, ADTOFRequestedMessage):
        raise ADTOFTaskClaimProtocolError("ADTOF task database state is invalid.")
    return lease, message
