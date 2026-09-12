"""Verify one restricted generic-dispatcher Basic Pitch routing handoff.

This disposable client proves only this durable route:

    PostgreSQL v004 outbox -> generic dispatcher -> RabbitMQ Basic Pitch queue

It is intentionally neither a dispatcher nor a Basic Pitch worker.  It asks
three SECURITY DEFINER PostgreSQL functions to create, observe, and remove one
synthetic event.  The database role has no direct table privilege.  Its second
identity can read and acknowledge only ``clouddsp.basic-pitch.requests``; it
cannot configure RabbitMQ resources or publish/retry/dead-letter a message.

The later Kubernetes Job must be run only while that queue has no ordinary
Basic Pitch work.  If an unexpected delivery appears, this client negatively
acknowledges it with ``requeue=True`` and exits rather than consuming work it
does not own.  It never logs message bodies, private object keys, generated
identifiers, passwords, or driver diagnostics.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4


# These are source-level protocol constants, not manifest-configurable values.
# Keeping the queue, stage, event type, and stem name fixed stops a future Job
# environment variable from broadening this narrowly granted test identity.
BASIC_PITCH_REQUEST_QUEUE = "clouddsp.basic-pitch.requests"
BASIC_PITCH_EVENT_TYPE = "basic-pitch.requested"
BASIC_PITCH_STAGE = "basic-pitch"
BASIC_PITCH_STEM_NAME = "vocals"
LOCAL_UPLOADS_BUCKET = "clouddsp-uploads"
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 120


class GenericDispatcherSmokeConfigurationError(RuntimeError):
    """Report missing or unsafe local test configuration without its value."""


class GenericDispatcherSmokeAssertionError(RuntimeError):
    """Report a fixed, safe category when the routing contract is not met."""


class GenericDispatcherSmokeInfrastructureError(RuntimeError):
    """Hide database/broker client diagnostics that may disclose credentials."""


def _required_environment(name: str, *, default: str | None = None) -> str:
    """Read one required environment setting without echoing its value on failure."""

    value = os.environ.get(name, default)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise GenericDispatcherSmokeConfigurationError(
            f"Required environment variable {name} is absent."
        )
    return value


def _bounded_integer(*, name: str, value: str, minimum: int, maximum: int) -> int:
    """Parse a bounded timeout or port before it can control an I/O operation."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise GenericDispatcherSmokeConfigurationError(f"{name} must be an integer.") from error
    if not minimum <= parsed <= maximum:
        raise GenericDispatcherSmokeConfigurationError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return parsed


def _canonical_uuid(value: object, *, message: str) -> str:
    """Require canonical lower-case UUID text for durable event correlation."""

    if not isinstance(value, str):
        raise GenericDispatcherSmokeAssertionError(message)
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise GenericDispatcherSmokeAssertionError(message) from error
    if value != canonical:
        raise GenericDispatcherSmokeAssertionError(message)
    return canonical


@dataclass(frozen=True)
class GenericDispatcherSmokeDatabaseSettings:
    """Private PostgreSQL route for only the three test-owned SQL functions."""

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> "GenericDispatcherSmokeDatabaseSettings":
        """Load the permanent app-namespace restricted database Secret."""

        host = _required_environment(
            "GENERIC_DISPATCHER_SMOKE_DB_HOST",
            default="clouddsp-postgresql.clouddsp-data.svc",
        )
        if host == "localhost" or host.endswith(".localhost"):
            raise GenericDispatcherSmokeConfigurationError(
                "Generic dispatcher smoke database host must be private Service DNS."
            )
        return cls(
            host=host,
            port=_bounded_integer(
                name="GENERIC_DISPATCHER_SMOKE_DB_PORT",
                value=os.environ.get("GENERIC_DISPATCHER_SMOKE_DB_PORT", "5432"),
                minimum=1,
                maximum=65_535,
            ),
            database=_required_environment("GENERIC_DISPATCHER_SMOKE_DB_NAME"),
            username=_required_environment("GENERIC_DISPATCHER_SMOKE_DB_USERNAME"),
            password=_required_environment("GENERIC_DISPATCHER_SMOKE_DB_PASSWORD"),
        )


@dataclass(frozen=True)
class BasicPitchSmokeAmqpSettings:
    """Private AMQP route for the read-only Basic Pitch smoke identity."""

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    virtual_host: str = "/clouddsp"
    queue_name: str = BASIC_PITCH_REQUEST_QUEUE

    @classmethod
    def from_environment(cls) -> "BasicPitchSmokeAmqpSettings":
        """Load the permanent app-namespace no-publish RabbitMQ Secret."""

        host = _required_environment(
            "BASIC_PITCH_SMOKE_AMQP_HOST",
            default="clouddsp-rabbitmq.clouddsp-data.svc",
        )
        if host == "localhost" or host.endswith(".localhost"):
            raise GenericDispatcherSmokeConfigurationError(
                "Basic Pitch smoke AMQP host must be private Service DNS."
            )
        return cls(
            host=host,
            port=_bounded_integer(
                name="BASIC_PITCH_SMOKE_AMQP_PORT",
                value=os.environ.get("BASIC_PITCH_SMOKE_AMQP_PORT", "5672"),
                minimum=1,
                maximum=65_535,
            ),
            username=_required_environment("RABBITMQ_BASIC_PITCH_SMOKE_USERNAME"),
            password=_required_environment("RABBITMQ_BASIC_PITCH_SMOKE_PASSWORD"),
        )


@dataclass(frozen=True)
class ControlledBasicPitchEvent:
    """The two opaque identifiers shared by the safe database functions and AMQP.

    The PostgreSQL function fixes the stage, stem, private object contract, and
    expiry.  The client supplies unique UUIDs only so it can correlate exactly
    one created row with exactly one AMQP delivery.
    """

    job_id: str
    event_id: str

    def __post_init__(self) -> None:
        """Reject malformed/colliding IDs before a database function is called."""

        canonical_job_id = _canonical_uuid(
            self.job_id,
            message="Generic dispatcher smoke Job identifier is invalid.",
        )
        canonical_event_id = _canonical_uuid(
            self.event_id,
            message="Generic dispatcher smoke event identifier is invalid.",
        )
        if canonical_job_id == canonical_event_id:
            raise GenericDispatcherSmokeAssertionError(
                "Generic dispatcher smoke Job and event identifiers must differ."
            )
        object.__setattr__(self, "job_id", canonical_job_id)
        object.__setattr__(self, "event_id", canonical_event_id)

    @classmethod
    def new(cls, *, uuid_factory: Callable[[], UUID] = uuid4) -> "ControlledBasicPitchEvent":
        """Generate the only two values this test is allowed to create itself."""

        return cls(job_id=str(uuid_factory()), event_id=str(uuid_factory()))


# Every statement invokes a reviewed SECURITY DEFINER function.  The test
# identity intentionally has no SELECT/INSERT/UPDATE/DELETE privilege on
# ``jobs`` or ``outbox_events``, so these statements cannot evolve into a raw
# data-access path by merely changing an input value.
CREATE_CONTROLLED_EVENT_SQL = """
    SELECT smoke_job_id::text, smoke_event_id::text
    FROM public.clouddsp_generic_dispatcher_smoke_create(%s::uuid, %s::uuid)
"""
READ_CONTROLLED_STATUS_SQL = """
    SELECT smoke_event_id::text, publication_status
    FROM public.clouddsp_generic_dispatcher_smoke_status(%s::uuid)
"""
CLEANUP_CONTROLLED_EVENT_SQL = """
    SELECT public.clouddsp_generic_dispatcher_smoke_cleanup(%s::uuid)
"""


def create_controlled_event(cursor: Any, expected: ControlledBasicPitchEvent) -> None:
    """Call only the restricted create function and verify its exact response."""

    try:
        cursor.execute(CREATE_CONTROLLED_EVENT_SQL, (expected.job_id, expected.event_id))
        row = cursor.fetchone()
    except Exception as error:
        raise GenericDispatcherSmokeInfrastructureError(
            "PostgreSQL smoke event creation is unavailable."
        ) from error
    if not isinstance(row, tuple) or len(row) != 2:
        raise GenericDispatcherSmokeAssertionError(
            "PostgreSQL smoke event creation returned an unexpected result."
        )
    returned_job_id = _canonical_uuid(
        row[0],
        message="PostgreSQL smoke event creation returned an invalid Job identifier.",
    )
    returned_event_id = _canonical_uuid(
        row[1],
        message="PostgreSQL smoke event creation returned an invalid event identifier.",
    )
    if (returned_job_id, returned_event_id) != (expected.job_id, expected.event_id):
        raise GenericDispatcherSmokeAssertionError(
            "PostgreSQL smoke event creation returned a different controlled event."
        )


def controlled_event_is_published(cursor: Any, expected: ControlledBasicPitchEvent) -> bool:
    """Read the one event's safe state through the restricted status function."""

    try:
        cursor.execute(READ_CONTROLLED_STATUS_SQL, (expected.job_id,))
        row = cursor.fetchone()
    except Exception as error:
        raise GenericDispatcherSmokeInfrastructureError(
            "PostgreSQL smoke event status is unavailable."
        ) from error
    if row is None:
        # A row should remain visible from creation through successful cleanup.
        # Treat its absence as an assertion failure rather than polling an
        # unbounded unknown state.
        raise GenericDispatcherSmokeAssertionError("PostgreSQL smoke event disappeared before publication.")
    if not isinstance(row, tuple) or len(row) != 2:
        raise GenericDispatcherSmokeAssertionError(
            "PostgreSQL smoke event status returned an unexpected result."
        )
    returned_event_id = _canonical_uuid(
        row[0],
        message="PostgreSQL smoke event status returned an invalid event identifier.",
    )
    publication_status = row[1]
    if returned_event_id != expected.event_id or not isinstance(publication_status, str):
        raise GenericDispatcherSmokeAssertionError(
            "PostgreSQL smoke event status did not match the controlled event."
        )
    if publication_status == "published":
        return True
    if publication_status in {"pending", "leased"}:
        return False
    raise GenericDispatcherSmokeAssertionError(
        "PostgreSQL smoke event reached an unexpected publication state."
    )


def cleanup_controlled_event(cursor: Any, expected: ControlledBasicPitchEvent) -> None:
    """Delete only the successfully published synthetic Job through its function."""

    try:
        cursor.execute(CLEANUP_CONTROLLED_EVENT_SQL, (expected.job_id,))
        row = cursor.fetchone()
    except Exception as error:
        raise GenericDispatcherSmokeInfrastructureError(
            "PostgreSQL smoke event cleanup is unavailable."
        ) from error
    if row != (True,):
        # Cleanup is deliberately false before publication, preventing this
        # client from erasing a durable event whose delivery was not observed.
        raise GenericDispatcherSmokeAssertionError(
            "PostgreSQL smoke event cleanup was not authorized after verification."
        )


def _load_psycopg() -> Any:
    """Import the future hash-locked PostgreSQL dependency only during I/O."""

    try:
        import psycopg
    except ImportError as error:
        raise GenericDispatcherSmokeInfrastructureError(
            "Pinned PostgreSQL smoke dependency is unavailable."
        ) from error
    return psycopg


def _load_pika() -> Any:
    """Import the future hash-locked AMQP dependency only during I/O."""

    try:
        import pika
    except ImportError as error:
        raise GenericDispatcherSmokeInfrastructureError(
            "Pinned AMQP smoke dependency is unavailable."
        ) from error
    return pika


def _with_database_cursor(
    settings: GenericDispatcherSmokeDatabaseSettings,
    operation: Callable[[Any], Any],
) -> Any:
    """Run one tiny function-only operation with autocommit and bounded SQL time.

    Each operation gets its own short connection.  It leaves no client-owned
    transaction open while the dispatcher independently leases/publishes the
    outbox row, and `statement_timeout` bounds an unavailable database call.
    """

    psycopg = _load_psycopg()
    try:
        with psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=settings.database,
            user=settings.username,
            password=settings.password,
            connect_timeout=5,
            options="-c statement_timeout=5000",
            autocommit=True,
            application_name="clouddsp-generic-dispatcher-basic-pitch-smoke",
        ) as connection:
            with connection.cursor() as cursor:
                return operation(cursor)
    except (
        GenericDispatcherSmokeAssertionError,
        GenericDispatcherSmokeConfigurationError,
        GenericDispatcherSmokeInfrastructureError,
    ):
        raise
    except Exception as error:
        raise GenericDispatcherSmokeInfrastructureError(
            "PostgreSQL generic dispatcher smoke connection is unavailable."
        ) from error


def create_event_in_database(
    settings: GenericDispatcherSmokeDatabaseSettings,
    expected: ControlledBasicPitchEvent,
) -> None:
    """Create the controlled event through one restricted database call."""

    _with_database_cursor(settings, lambda cursor: create_controlled_event(cursor, expected))


def wait_for_controlled_event_published(
    settings: GenericDispatcherSmokeDatabaseSettings,
    expected: ControlledBasicPitchEvent,
    *,
    timeout_seconds: int,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    read_published: Callable[
        [GenericDispatcherSmokeDatabaseSettings, ControlledBasicPitchEvent], bool
    ] | None = None,
) -> None:
    """Poll only the controlled event until generic-dispatcher records publication."""

    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise GenericDispatcherSmokeConfigurationError(
            "Generic dispatcher smoke timeout is outside its safe bounds."
        )
    if read_published is None:
        read_published = lambda database_settings, controlled_event: _with_database_cursor(
            database_settings,
            lambda cursor: controlled_event_is_published(cursor, controlled_event),
        )
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if read_published(settings, expected):
            return
        sleep_function(1)
    raise GenericDispatcherSmokeAssertionError(
        "Timed out waiting for the controlled Basic Pitch event to be published."
    )


def open_amqp_connection(settings: BasicPitchSmokeAmqpSettings) -> Any:
    """Open one bounded connection as the test-only read/ack RabbitMQ user."""

    pika = _load_pika()
    try:
        return pika.BlockingConnection(
            pika.ConnectionParameters(
                host=settings.host,
                port=settings.port,
                virtual_host=settings.virtual_host,
                credentials=pika.PlainCredentials(settings.username, settings.password),
                connection_attempts=3,
                retry_delay=2,
                socket_timeout=5,
                blocked_connection_timeout=10,
                heartbeat=10,
            )
        )
    except Exception as error:
        raise GenericDispatcherSmokeInfrastructureError(
            "RabbitMQ Basic Pitch smoke connection is unavailable."
        ) from error


def _assert_basic_pitch_message_body(body: object, expected: ControlledBasicPitchEvent) -> None:
    """Validate the strict v004 Basic Pitch body without logging private fields."""

    if not isinstance(body, bytes):
        raise GenericDispatcherSmokeAssertionError("Basic Pitch message body has an invalid client type.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GenericDispatcherSmokeAssertionError(
            "Basic Pitch message body is not valid UTF-8 JSON."
        ) from error
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "job_id",
        "stem_name",
        "stem",
    }:
        raise GenericDispatcherSmokeAssertionError("Basic Pitch message body has an incompatible contract.")
    stem = payload.get("stem")
    if (
        payload.get("schema_version") != 1
        or payload.get("job_id") != expected.job_id
        or payload.get("stem_name") != BASIC_PITCH_STEM_NAME
        or not isinstance(stem, Mapping)
        or set(stem) != {"bucket", "object_key", "content_type", "size_bytes", "sha256"}
        or stem.get("bucket") != LOCAL_UPLOADS_BUCKET
        or stem.get("object_key") != f"stems/{expected.job_id}/{BASIC_PITCH_STEM_NAME}.wav"
        or stem.get("content_type") != "audio/wav"
        or stem.get("size_bytes") != 1
        or stem.get("sha256") != "0" * 64
    ):
        raise GenericDispatcherSmokeAssertionError(
            "Basic Pitch message body does not match the controlled event."
        )


def _assert_basic_pitch_message_properties(properties: Any, expected: ControlledBasicPitchEvent) -> None:
    """Validate the persistent AMQP envelope before acknowledging the delivery."""

    if (
        getattr(properties, "content_type", None) != "application/json"
        or getattr(properties, "content_encoding", None) != "utf-8"
        or getattr(properties, "delivery_mode", None) != 2
        or getattr(properties, "type", None) != BASIC_PITCH_EVENT_TYPE
        or getattr(properties, "message_id", None) != expected.event_id
        or getattr(properties, "correlation_id", None) != expected.job_id
    ):
        raise GenericDispatcherSmokeAssertionError(
            "Basic Pitch message properties do not match the controlled event."
        )


def consume_controlled_basic_pitch_request(
    channel: Any,
    *,
    expected: ControlledBasicPitchEvent,
    timeout_seconds: int,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Read, validate, and acknowledge exactly the synthetic Basic Pitch delivery.

    An unexpected delivery is immediately requeued and fails the test.  This
    client never loops past a foreign message or accepts it merely because the
    queue name matches; neither outcome would prove the intended outbox route.
    """

    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise GenericDispatcherSmokeConfigurationError(
            "Generic dispatcher smoke timeout is outside its safe bounds."
        )
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        try:
            method_frame, properties, body = channel.basic_get(
                queue=BASIC_PITCH_REQUEST_QUEUE,
                auto_ack=False,
            )
        except Exception as error:
            raise GenericDispatcherSmokeInfrastructureError(
                "RabbitMQ Basic Pitch smoke read is unavailable."
            ) from error
        if method_frame is None:
            sleep_function(1)
            continue
        try:
            _assert_basic_pitch_message_body(body, expected)
            _assert_basic_pitch_message_properties(properties, expected)
        except GenericDispatcherSmokeAssertionError:
            # The test identity cannot publish an alternate route. Requeue the
            # foreign delivery so a real worker can receive it after this test.
            channel.basic_nack(method_frame.delivery_tag, requeue=True)
            raise
        channel.basic_ack(method_frame.delivery_tag)
        return
    raise GenericDispatcherSmokeAssertionError(
        "Timed out waiting for the controlled Basic Pitch request."
    )


def verify_basic_pitch_delivery(
    settings: BasicPitchSmokeAmqpSettings,
    expected: ControlledBasicPitchEvent,
    *,
    timeout_seconds: int,
) -> None:
    """Connect with the reader identity and close it even if verification fails."""

    connection: Any | None = None
    channel: Any | None = None
    try:
        connection = open_amqp_connection(settings)
        channel = connection.channel()
        consume_controlled_basic_pitch_request(
            channel,
            expected=expected,
            timeout_seconds=timeout_seconds,
        )
    finally:
        if channel is not None and getattr(channel, "is_open", False):
            with suppress(Exception):
                channel.close()
        if connection is not None:
            with suppress(Exception):
                connection.close()


def remove_published_controlled_event(
    settings: GenericDispatcherSmokeDatabaseSettings,
    expected: ControlledBasicPitchEvent,
) -> None:
    """Remove the synthetic Job only after its exact AMQP delivery was acknowledged."""

    _with_database_cursor(settings, lambda cursor: cleanup_controlled_event(cursor, expected))


def run_smoke(
    *,
    database_settings: GenericDispatcherSmokeDatabaseSettings,
    amqp_settings: BasicPitchSmokeAmqpSettings,
    timeout_seconds: int,
    uuid_factory: Callable[[], UUID] = uuid4,
    create_event: Callable[
        [GenericDispatcherSmokeDatabaseSettings, ControlledBasicPitchEvent], None
    ] = create_event_in_database,
    wait_for_published: Callable[
        [GenericDispatcherSmokeDatabaseSettings, ControlledBasicPitchEvent], None
    ] | None = None,
    verify_delivery: Callable[[BasicPitchSmokeAmqpSettings, ControlledBasicPitchEvent], None] | None = None,
    remove_event: Callable[
        [GenericDispatcherSmokeDatabaseSettings, ControlledBasicPitchEvent], None
    ] = remove_published_controlled_event,
) -> None:
    """Run the three-boundary test in the only safe order.

    Cleanup intentionally runs *after* the exact AMQP acknowledgement.  If
    publication or consumption fails, the restricted cleanup function refuses
    unsafe deletion and the short-lived synthetic row remains available for
    diagnosis until its fixed expiry instead of hiding an unverified delivery.
    """

    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise GenericDispatcherSmokeConfigurationError(
            "Generic dispatcher smoke timeout is outside its safe bounds."
        )
    expected = ControlledBasicPitchEvent.new(uuid_factory=uuid_factory)
    if wait_for_published is None:
        wait_for_published = lambda settings, event: wait_for_controlled_event_published(
            settings,
            event,
            timeout_seconds=timeout_seconds,
        )
    if verify_delivery is None:
        verify_delivery = lambda settings, event: verify_basic_pitch_delivery(
            settings,
            event,
            timeout_seconds=timeout_seconds,
        )

    print("Generic dispatcher Basic Pitch smoke: creating controlled outbox event")
    create_event(database_settings, expected)
    print("Generic dispatcher Basic Pitch smoke: waiting for durable publication")
    wait_for_published(database_settings, expected)
    print("Generic dispatcher Basic Pitch smoke: verifying restricted queue delivery")
    verify_delivery(amqp_settings, expected)
    print("Generic dispatcher Basic Pitch smoke: removing acknowledged synthetic event")
    remove_event(database_settings, expected)


def main() -> int:
    """Load fixed local settings, run the one-shot test, and emit safe progress only."""

    try:
        timeout_seconds = _bounded_integer(
            name="GENERIC_DISPATCHER_SMOKE_TIMEOUT_SECONDS",
            value=os.environ.get(
                "GENERIC_DISPATCHER_SMOKE_TIMEOUT_SECONDS",
                str(DEFAULT_TIMEOUT_SECONDS),
            ),
            minimum=1,
            maximum=MAX_TIMEOUT_SECONDS,
        )
        run_smoke(
            database_settings=GenericDispatcherSmokeDatabaseSettings.from_environment(),
            amqp_settings=BasicPitchSmokeAmqpSettings.from_environment(),
            timeout_seconds=timeout_seconds,
        )
    except (
        GenericDispatcherSmokeConfigurationError,
        GenericDispatcherSmokeAssertionError,
        GenericDispatcherSmokeInfrastructureError,
    ) as error:
        # The public error categories above never interpolate endpoint details,
        # driver diagnostics, message payloads, private object keys, or Secrets.
        print(f"Generic dispatcher Basic Pitch smoke test failed: {error}", file=sys.stderr)
        return 1
    print("Generic dispatcher Basic Pitch smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
