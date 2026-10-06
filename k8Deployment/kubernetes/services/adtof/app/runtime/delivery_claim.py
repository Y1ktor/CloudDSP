"""Transport-neutral parse-and-claim bridge for one ``adtof.requested`` delivery.

This module joins two independently tested ADTOF boundaries in their required
order. It first validates the exact persistent AMQP envelope/body contract,
then makes the short PostgreSQL first-claim decision. It deliberately does not
import Pika, open a connection, receive a delivery tag, acknowledge/reject/
retry a message, access MinIO, invoke ADTOF, sleep, or change Kubernetes.

The later Pika-only manual-ack adapter must decide what to do with a malformed
request exception, a committed claim/duplicate/stale result, or a transient
database exception. It may decide only after this bridge returns: a normal
first-claim result is already committed, while an exception has rolled back.
This keeps RabbitMQ delivery policy separate from trusted parsing and durable
database authority.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.messaging.adtof_requested_message import ADTOFRequestedMessage, parse_adtof_requested_delivery
from app.db.first_claim import ADTOFTaskClaimDatabase, claim_first_adtof_task
from app.db.task_claim import DEFAULT_ADTOF_LEASE_SECONDS, ADTOFTaskClaimResult


@dataclass(frozen=True)
class ADTOFDeliveryClaim:
    """The parsed private request identifiers and one committed claim result.

    A later AMQP layer receives no raw message body, AMQP properties, delivery
    tag, database cursor, password, bucket client, or source bytes in this
    value. It can therefore decide its broker action from PostgreSQL's exact
    ``claimed``, ``duplicate``, or ``stale`` fact without accidentally reusing
    untrusted transport values or obtaining broader database/broker access.
    """

    message: ADTOFRequestedMessage
    task_claim: ADTOFTaskClaimResult


def claim_adtof_requested_delivery(
    *,
    database: ADTOFTaskClaimDatabase,
    delivery_exchange: object,
    delivery_routing_key: object,
    properties: object,
    body: object,
    lease_seconds: int = DEFAULT_ADTOF_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> ADTOFDeliveryClaim:
    """Parse then durably classify one request without a broker action.

    Parsing always happens before PostgreSQL access, preventing malformed
    exchange/routing metadata, properties, or JSON from inspecting or creating
    a task. The first-claim composition commits a new lease or no-mutation
    duplicate/stale fact before normal return. Reviewed exception categories
    intentionally propagate unchanged: the later manual-ack adapter must
    distinguish a permanent request contract failure from a transient database
    failure, rather than guessing that policy in this transport-neutral code.
    """

    # The parser receives raw delivery evidence, but never a channel or
    # delivery tag. That API shape makes it impossible for this module to
    # acknowledge/reject a delivery or obtain another message from RabbitMQ.
    message = parse_adtof_requested_delivery(
        delivery_exchange=delivery_exchange,
        delivery_routing_key=delivery_routing_key,
        properties=properties,
        body=body,
    )

    # This is the bridge's only durable operation. The first-claim composition
    # holds its short transaction internally and returns only after commit; no
    # broker acknowledgement, object access, scratch file, or CPU model work
    # can overlap its transaction/row-lock scope.
    task_claim = claim_first_adtof_task(
        database=database,
        message=message,
        lease_seconds=lease_seconds,
        uuid_factory=uuid_factory,
    )
    return ADTOFDeliveryClaim(message=message, task_claim=task_claim)
