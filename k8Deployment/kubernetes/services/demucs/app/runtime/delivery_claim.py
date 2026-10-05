"""Transport-neutral parse-and-claim bridge for one ``demucs.requested`` delivery.

This module joins two existing, independently testable boundaries in their
required order: first validate the exact AMQP envelope/body contract, then
make the short durable PostgreSQL first-claim decision.  It intentionally does
not import Pika, open a broker connection, call ``basic_ack``/``basic_nack``,
publish a retry, read MinIO, run media work, sleep, or create a Kubernetes
resource.

Keeping acknowledgement outside this bridge is deliberate.  A later AMQP
transport adapter must map a successful returned durable claim, a malformed
request exception, and a transient PostgreSQL exception to the reviewed broker
actions.  It may do that only after this bridge returns, because a normal
first-claim result is then already committed by its transaction scope.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.messaging.demucs_requested_message import DemucsRequestedMessage, parse_demucs_requested_delivery
from app.db.first_claim import DemucsTaskClaimDatabase, claim_first_demucs_task
from app.db.task_lease import DEFAULT_DEMUCS_LEASE_SECONDS, DemucsTaskClaimResult


@dataclass(frozen=True)
class DemucsDeliveryClaim:
    """The parsed private identifiers and committed durable first-claim result.

    The future transport receives this object without a broker delivery tag,
    raw body, AMQP properties, database cursor, password, or object bytes.  It
    therefore cannot accidentally log or reuse those untrusted/secret values
    while deciding its next broker action.  ``task_claim`` preserves the exact
    ``claimed``, ``duplicate``, or ``stale`` meaning assigned by PostgreSQL.
    """

    message: DemucsRequestedMessage
    task_claim: DemucsTaskClaimResult


def claim_demucs_requested_delivery(
    *,
    database: DemucsTaskClaimDatabase,
    delivery_exchange: object,
    delivery_routing_key: object,
    properties: object,
    body: object,
    lease_seconds: int = DEFAULT_DEMUCS_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> DemucsDeliveryClaim:
    """Parse then durably classify one delivery without making broker decisions.

    Contract parsing always happens before PostgreSQL is touched, so malformed
    broker data cannot create/inspect task state.  The first-claim composition
    then commits a new lease or its duplicate/stale classification before this
    function returns.  Exceptions are deliberately not translated here:
    malformed AMQP data, a durable inconsistency, and a temporary database
    outage require different explicit policy in the future Pika-only adapter.
    """

    # The parser receives raw envelope/properties/body directly rather than a
    # Pika channel or delivery tag.  This keeps the trust boundary visible and
    # prevents this application layer from accidentally acknowledging a message.
    message = parse_demucs_requested_delivery(
        delivery_exchange=delivery_exchange,
        delivery_routing_key=delivery_routing_key,
        properties=properties,
        body=body,
    )

    # `claim_first_demucs_task` owns the only PostgreSQL transaction in this
    # call.  Its return value is safe to expose only after that context commits;
    # no broker acknowledgement or external processing begins inside this scope.
    task_claim = claim_first_demucs_task(
        database=database,
        message=message,
        lease_seconds=lease_seconds,
        uuid_factory=uuid_factory,
    )
    return DemucsDeliveryClaim(message=message, task_claim=task_claim)
