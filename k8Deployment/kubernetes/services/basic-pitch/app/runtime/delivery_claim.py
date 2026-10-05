"""Transport-neutral parse-and-claim bridge for one Basic Pitch AMQP delivery.

This module joins two existing, independently tested boundaries in their
required order: it first validates the exact persistent
``basic-pitch.requested`` AMQP contract, then makes the short PostgreSQL
first-claim decision. It deliberately does not import Pika, open a connection,
receive a delivery tag, acknowledge/reject/retry a message, access MinIO, run
Basic Pitch, sleep, or change a Kubernetes resource.

The future Pika transport adapter must decide what to do with a malformed
request exception, a committed claim/duplicate/stale result, or a transient
database exception. It may make that decision only after this bridge returns:
the first-claim composition has then either committed or rolled back, and this
transport-neutral layer cannot accidentally perform a broker action itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.messaging.basic_pitch_requested_message import (
    BasicPitchRequestedMessage,
    parse_basic_pitch_requested_delivery,
)
from app.db.first_claim import BasicPitchTaskClaimDatabase, claim_first_basic_pitch_task
from app.db.task_lease import DEFAULT_BASIC_PITCH_LEASE_SECONDS, BasicPitchTaskClaimResult


@dataclass(frozen=True)
class BasicPitchDeliveryClaim:
    """The validated request and its committed durable first-claim outcome.

    A future AMQP layer receives no raw body, AMQP properties, delivery tag,
    database cursor, password, object stream, or broker client in this value.
    That makes the narrow handoff safe to inspect and difficult to misuse while
    it later maps PostgreSQL's exact `claimed`, duplicate, or stale result to a
    manual RabbitMQ acknowledgement policy.
    """

    message: BasicPitchRequestedMessage
    task_claim: BasicPitchTaskClaimResult


def claim_basic_pitch_requested_delivery(
    *,
    database: BasicPitchTaskClaimDatabase,
    delivery_exchange: object,
    delivery_routing_key: object,
    properties: object,
    body: object,
    lease_seconds: int = DEFAULT_BASIC_PITCH_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> BasicPitchDeliveryClaim:
    """Parse then durably classify one delivery without a broker decision.

    Parsing always precedes PostgreSQL access, so malformed exchange/routing
    metadata, AMQP properties, or JSON cannot create/inspect durable task state.
    The first-claim composition commits a new lease or a no-mutation
    duplicate/stale result before this function returns. Exceptions are kept in
    their original reviewed categories: the later Pika-only layer needs to
    distinguish permanent contract failure from a database retry condition and
    must not infer either policy here.
    """

    # Pass only the parser's raw transport inputs. There is intentionally no
    # channel or delivery tag parameter, so this application boundary cannot
    # call `basic_ack`, `basic_nack`, or receive a second broker message.
    message = parse_basic_pitch_requested_delivery(
        delivery_exchange=delivery_exchange,
        delivery_routing_key=delivery_routing_key,
        properties=properties,
        body=body,
    )

    # This call owns the only PostgreSQL transaction in the bridge. Its normal
    # return is visible only after commit, before any future acknowledgement,
    # MinIO operation, temporary file, or CPU model work can start.
    task_claim = claim_first_basic_pitch_task(
        database=database,
        message=message,
        lease_seconds=lease_seconds,
        uuid_factory=uuid_factory,
    )
    return BasicPitchDeliveryClaim(message=message, task_claim=task_claim)
