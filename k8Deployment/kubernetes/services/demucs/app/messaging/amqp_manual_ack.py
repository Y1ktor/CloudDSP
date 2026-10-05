"""Manual-ack AMQP adapter for one already-topologized Demucs request delivery.

This is the first Pika-shaped transport boundary for the worker.  It reads at
most one message from the fixed private request queue with ``auto_ack=False``,
hands its raw envelope to ``delivery_claim.py``, and performs the reviewed
broker action only after the database-backed bridge returns.

It deliberately does not open a connection, read environment settings, declare
topology, publish retry work, start a consumer loop, run recovery scans, read
MinIO, invoke FFprobe/Demucs, or create a Kubernetes resource.  Those are
separate steps; keeping this one-delivery state machine small makes its
at-least-once acknowledgement boundary inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.runtime.delivery_claim import DemucsDeliveryClaim, claim_demucs_requested_delivery
from app.messaging.demucs_requested_message import DemucsRequestContractError
from app.db.first_claim import DemucsTaskClaimDatabase
from app.db.task_lease import DemucsTaskClaimDisposition, DemucsTaskLease, DemucsTaskLeaseProtocolError


# This is the exact queue bound by the versioned processing topology.  It is a
# source-level contract constant, not a Deployment variable: the restricted
# RabbitMQ identity may only read/ack this queue, and a route change requires a
# reviewed topology/message-compatibility change rather than a runtime typo.
DEMUCS_REQUEST_QUEUE = "clouddsp.demucs.requests"


class DemucsAMQPUnavailable(RuntimeError):
    """A retryable receive/ack/nack channel failure with a safe public message.

    The original Pika exception remains the exception cause for local
    diagnostics.  The future connection/supervisor layer must log only this
    fixed category, close the channel, and let RabbitMQ redeliver any unacked
    delivery instead of trusting a failed acknowledgement attempt.
    """


class DemucsConsumeOneOutcome(StrEnum):
    """The only normal results of one manual-ack receive attempt."""

    # No delivery was received, so neither the bridge nor RabbitMQ ack methods
    # ran. A later supervisor may use this as its bounded idle-sleep signal.
    IDLE = "idle"
    # A parsed request acquired a durable new lease, then RabbitMQ accepted the
    # acknowledgement. The returned result carries that exact committed lease
    # for the future source-preflight/model layer.
    ACKNOWLEDGED_LEASE = "acknowledged_lease"
    # A duplicate or stale request reached a durable no-work result, then
    # RabbitMQ accepted the acknowledgement. No lease may be processed here.
    ACKNOWLEDGED_NO_WORK = "acknowledged_no_work"
    # A permanently malformed envelope/properties/body was explicitly nacked
    # without requeue, allowing the preconfigured main-queue DLX to retain it.
    MALFORMED_REJECTED = "malformed_rejected"


@dataclass(frozen=True)
class DemucsConsumeOneResult:
    """One completed broker action plus the lease available for later work.

    Only ``ACKNOWLEDGED_LEASE`` carries a lease. That makes the next runtime
    layer's branch explicit: it may start source preflight only after RabbitMQ
    accepted the acknowledgement *and* PostgreSQL supplied this exact durable
    task token. Duplicate/stale/idled/rejected outcomes never contain a lease,
    preventing a caller from accidentally reprocessing historical work.
    """

    outcome: DemucsConsumeOneOutcome
    lease: DemucsTaskLease | None = None

    def __post_init__(self) -> None:
        """Keep the outcome/lease pairing impossible to misinterpret later."""

        has_lease = self.lease is not None
        requires_lease = self.outcome is DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE
        if has_lease != requires_lease:
            raise ValueError("Demucs receive result lease does not match its outcome.")


def consume_one_demucs_requested_delivery(
    channel: Any,
    *,
    database: DemucsTaskClaimDatabase,
) -> DemucsConsumeOneResult:
    """Receive and resolve at most one delivery with the reviewed ack ordering.

    A successfully returned bridge result has already committed PostgreSQL's
    first-claim outcome, so it is safe to ``basic_ack`` whether the result is a
    newly owned lease, a duplicate, or harmless stale history. A successfully
    acknowledged new lease is returned to the caller; duplicate/stale results
    return an explicit no-work outcome. A malformed broker contract can never
    become valid by redelivery, so it receives ``basic_nack(requeue=False)``
    and enters the configured DLQ path.

    Database unavailability, durable-identity inconsistency, and every other
    unexpected bridge error intentionally escape without ``ack`` or ``nack``.
    The future supervisor will close/reconnect with bounded backoff, allowing
    RabbitMQ's at-least-once redelivery and delivery-limit policy to operate.
    """

    try:
        method_frame, properties, body = channel.basic_get(
            queue=DEMUCS_REQUEST_QUEUE,
            auto_ack=False,
        )
    except Exception as error:
        raise DemucsAMQPUnavailable("RabbitMQ Demucs receive is unavailable.") from error

    if method_frame is None:
        return DemucsConsumeOneResult(outcome=DemucsConsumeOneOutcome.IDLE)

    try:
        # The bridge validates `exchange`, `routing_key`, Pika properties, and
        # raw body before its short PostgreSQL claim transaction can begin.
        delivery_claim = claim_demucs_requested_delivery(
            database=database,
            delivery_exchange=method_frame.exchange,
            delivery_routing_key=method_frame.routing_key,
            properties=properties,
            body=body,
        )
    except DemucsRequestContractError:
        # This narrow exception is a permanent input fault. Do not use
        # `basic_reject` or a broad exception handler: errors from PostgreSQL or
        # a missing terminal-inconsistency transition must remain unacknowledged
        # for redelivery, rather than being discarded accidentally.
        try:
            channel.basic_nack(method_frame.delivery_tag, requeue=False)
        except Exception as error:
            raise DemucsAMQPUnavailable("RabbitMQ Demucs rejection is unavailable.") from error
        return DemucsConsumeOneResult(outcome=DemucsConsumeOneOutcome.MALFORMED_REJECTED)

    lease = _claimed_lease_or_error(delivery_claim)

    # A normal return means the short claim transaction has committed. An ack
    # failure is still safe: RabbitMQ may redeliver later, and the canonical
    # PostgreSQL task key turns it into a duplicate rather than a second run.
    try:
        channel.basic_ack(method_frame.delivery_tag)
    except Exception as error:
        raise DemucsAMQPUnavailable("RabbitMQ Demucs acknowledgement is unavailable.") from error
    return DemucsConsumeOneResult(
        outcome=(
            DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE
            if lease is not None
            else DemucsConsumeOneOutcome.ACKNOWLEDGED_NO_WORK
        ),
        lease=lease,
    )


def _claimed_lease_or_error(delivery_claim: DemucsDeliveryClaim) -> DemucsTaskLease | None:
    """Extract only a complete first lease before an acknowledgement is sent.

    The pure claim adapter normally guarantees this pairing. Re-checking here
    makes a future regression/mocked adapter unable to acknowledge a delivery
    and then lose the token needed for ownership-safe source/model work. The
    exception intentionally leaves the broker delivery unacknowledged.
    """

    if delivery_claim.task_claim.disposition is not DemucsTaskClaimDisposition.CLAIMED:
        return None
    lease = delivery_claim.task_claim.lease
    if lease is None:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return lease
