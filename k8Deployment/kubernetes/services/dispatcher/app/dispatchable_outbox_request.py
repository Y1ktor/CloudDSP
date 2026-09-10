"""Choose one fixed AMQP contract for a generically leased outbox event.

This is a deliberately small pure bridge between two already-reviewed layers:

* ``app.outbox_lease`` atomically leases a finite durable event triple; and
* the Demucs/downstream contract builders validate and render AMQP requests.

It has no Pika import, PostgreSQL connection, RabbitMQ publish, retry policy,
runtime loop, Kubernetes API call, configuration reader, or container
entrypoint.  The next composition task can use this bridge before it calls a
generic Pika publisher, while the current Demucs-only dispatcher remains on
its existing, narrower composition path.
"""

from __future__ import annotations

from app.amqp_publisher import (
    DemucsAMQPRequest,
    DispatcherPublisherContractError,
    build_demucs_amqp_request,
)
from app.downstream_outbox_request import (
    DownstreamAMQPRequest,
    DownstreamOutboxRequestContractError,
    build_downstream_amqp_request,
)
from app.outbox_lease import LeasedOutboxEvent


class DispatchableOutboxRequestContractError(RuntimeError):
    """Reject a leased event that cannot become one reviewed AMQP request.

    The safe fixed category intentionally omits the Job ID, outbox ID, object
    key, and lower-layer exception.  A later dispatcher composition can turn
    it into the existing bounded outbox ``invalid_event_contract`` terminal
    state without leaking private storage identifiers in routine Pod logs.
    """


DispatchableAMQPRequest = DemucsAMQPRequest | DownstreamAMQPRequest


def _contract_error() -> DispatchableOutboxRequestContractError:
    """Return one public-safe error category for every rejected lease shape."""

    return DispatchableOutboxRequestContractError("Dispatcher outbox event contract is invalid.")


def build_dispatchable_amqp_request(event: LeasedOutboxEvent) -> DispatchableAMQPRequest:
    """Render the one permitted AMQP request for an already-leased event.

    The explicit Demucs check is deliberately narrow.  Every other reviewed
    triple is delegated to the downstream builder, which independently checks
    Basic Pitch versus drums-only ADTOF routing and the private WAV evidence.
    Thus no string concatenation, environment value, or caller-provided queue
    name can expand the dispatcher's broker authority.
    """

    if not isinstance(event, LeasedOutboxEvent):
        raise _contract_error()
    try:
        if (event.stage, event.stem_name, event.event_type) == (
            "demucs",
            "",
            "demucs.requested",
        ):
            return build_demucs_amqp_request(
                event_id=event.event_id,
                job_id=event.job_id,
                payload=event.payload,
            )
        return build_downstream_amqp_request(
            event_id=event.event_id,
            job_id=event.job_id,
            stage=event.stage,
            stem_name=event.stem_name,
            event_type=event.event_type,
            payload=event.payload,
        )
    except (DispatcherPublisherContractError, DownstreamOutboxRequestContractError) as error:
        raise _contract_error() from error
