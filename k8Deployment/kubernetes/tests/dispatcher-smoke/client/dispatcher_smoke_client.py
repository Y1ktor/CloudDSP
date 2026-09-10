"""Verify one controlled PostgreSQL-outbox-to-RabbitMQ dispatcher handoff.

This future disposable client is a *verifier*, not a dispatcher, an
upload-intake replacement, or a Demucs worker. A later smoke Job will use the
normal authenticated upload and source-intake path to create one durable event,
then give this client the event's stable contract.

The client proves two independent facts:

1. PostgreSQL records the exact outbox event as ``published``; and
2. the restricted smoke identity receives one matching persistent
   ``demucs.requested`` RabbitMQ delivery and acknowledges only that delivery.

It never writes an outbox row, publishes a RabbitMQ message, declares topology,
consumes a non-Demucs queue, runs audio processing, prints an event body/object
key/token/password, or calls the Kubernetes API. Its later Job must not run
while ordinary Demucs work is queued: a passive empty-queue preflight prevents
the narrow reader from touching normal processing messages.
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
from uuid import UUID


# These source-level values mirror the immutable version-1 dispatcher contract.
# They are intentionally not configurable to keep a smoke-reader identity from
# observing another worker stage simply through a manifest environment change.
DEMUCS_REQUEST_QUEUE = "clouddsp.demucs.requests"
DEMUCS_EVENT_TYPE = "demucs.requested"
LOCAL_UPLOADS_BUCKET = "clouddsp-uploads"
SUPPORTED_STEM_MODES = frozenset({"2-stems", "4-stems", "6-stems"})
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 120


class DispatcherSmokeConfigurationError(RuntimeError):
    """Raise a safe category for missing/invalid test configuration."""


class DispatcherSmokeAssertionError(RuntimeError):
    """Raise a fixed safe category when the dispatcher contract is not met."""


class DispatcherSmokeInfrastructureError(RuntimeError):
    """Hide driver diagnostics that could disclose endpoint/credential details."""


def _required_environment(name: str, *, default: str | None = None) -> str:
    """Read one required setting without placing its value in an error message."""

    value = os.environ.get(name, default)
    if value is None or not isinstance(value, str) or not value or "\x00" in value:
        raise DispatcherSmokeConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_integer(*, name: str, value: str, minimum: int, maximum: int) -> int:
    """Parse a bounded local timeout/port before it controls I/O."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise DispatcherSmokeConfigurationError(f"{name} must be an integer.") from error
    if not minimum <= parsed <= maximum:
        raise DispatcherSmokeConfigurationError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return parsed


def _canonical_uuid(value: str) -> str:
    """Accept only canonical lowercase UUID text used by durable records."""

    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise DispatcherSmokeConfigurationError("Dispatcher smoke UUID is invalid.") from error
    canonical = str(parsed)
    if value != canonical:
        raise DispatcherSmokeConfigurationError("Dispatcher smoke UUID is not canonical lowercase text.")
    return canonical


@dataclass(frozen=True)
class ExpectedDemucsRequested:
    """The one durable event a future smoke Job is authorized to observe.

    This in-memory contract holds stable identifiers, not a presigned URL or
    credential. Normal logs report pass/fail categories only and never
    serialize it, because the source object key is private application data.
    """

    event_id: str
    job_id: str
    object_key: str
    stem_mode: str
    bucket_name: str = LOCAL_UPLOADS_BUCKET

    def __post_init__(self) -> None:
        """Fail before broker/database access when the controlled target is invalid."""

        object.__setattr__(self, "event_id", _canonical_uuid(self.event_id))
        object.__setattr__(self, "job_id", _canonical_uuid(self.job_id))
        if self.bucket_name != LOCAL_UPLOADS_BUCKET:
            raise DispatcherSmokeConfigurationError("Dispatcher smoke bucket does not match local uploads.")
        if not isinstance(self.object_key, str):
            raise DispatcherSmokeConfigurationError("Dispatcher smoke object key does not match its job prefix.")
        expected_prefix = f"uploads/{self.job_id}/"
        nested_name = self.object_key[len(expected_prefix) :] if self.object_key.startswith(expected_prefix) else ""
        if not nested_name or "/" in nested_name:
            raise DispatcherSmokeConfigurationError("Dispatcher smoke object key does not match its job prefix.")
        if self.stem_mode not in SUPPORTED_STEM_MODES:
            raise DispatcherSmokeConfigurationError("Dispatcher smoke stem mode is unsupported.")

    @classmethod
    def from_environment(cls) -> "ExpectedDemucsRequested":
        """Read the immutable controlled contract a future Job supplies."""

        return cls(
            event_id=_required_environment("DISPATCHER_SMOKE_EVENT_ID"),
            job_id=_required_environment("DISPATCHER_SMOKE_JOB_ID"),
            object_key=_required_environment("DISPATCHER_SMOKE_OBJECT_KEY"),
            stem_mode=_required_environment("DISPATCHER_SMOKE_STEM_MODE"),
            bucket_name=_required_environment(
                "DISPATCHER_SMOKE_UPLOADS_BUCKET",
                default=LOCAL_UPLOADS_BUCKET,
            ),
        )


@dataclass(frozen=True)
class DispatcherSmokeAmqpSettings:
    """Private AMQP settings for the separate test-only queue reader."""

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    virtual_host: str = "/clouddsp"
    queue_name: str = DEMUCS_REQUEST_QUEUE

    @classmethod
    def from_environment(cls) -> "DispatcherSmokeAmqpSettings":
        """Load private broker DNS and the least-privilege reader identity."""

        host = _required_environment(
            "DISPATCHER_SMOKE_AMQP_HOST",
            default="clouddsp-rabbitmq.clouddsp-data.svc",
        )
        if host == "localhost" or host.endswith(".localhost"):
            raise DispatcherSmokeConfigurationError("Dispatcher smoke AMQP host must be private Service DNS.")
        virtual_host = _required_environment("DISPATCHER_SMOKE_AMQP_VHOST", default="/clouddsp")
        queue_name = _required_environment(
            "DISPATCHER_SMOKE_AMQP_QUEUE",
            default=DEMUCS_REQUEST_QUEUE,
        )
        if virtual_host != "/clouddsp" or queue_name != DEMUCS_REQUEST_QUEUE:
            raise DispatcherSmokeConfigurationError("Dispatcher smoke AMQP route does not match the reviewed queue.")
        return cls(
            host=host,
            port=_bounded_integer(
                name="DISPATCHER_SMOKE_AMQP_PORT",
                value=os.environ.get("DISPATCHER_SMOKE_AMQP_PORT", "5672"),
                minimum=1,
                maximum=65_535,
            ),
            username=_required_environment("RABBITMQ_DISPATCHER_SMOKE_USERNAME"),
            password=_required_environment("RABBITMQ_DISPATCHER_SMOKE_PASSWORD"),
            virtual_host=virtual_host,
            queue_name=queue_name,
        )


@dataclass(frozen=True)
class DispatcherSmokeDatabaseSettings:
    """Private database route used solely for read-only smoke assertions."""

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> "DispatcherSmokeDatabaseSettings":
        """Load a future Job's database assertion route and credential."""

        return cls(
            host=_required_environment(
                "DISPATCHER_SMOKE_DB_HOST",
                default="clouddsp-postgresql.clouddsp-data.svc",
            ),
            port=_bounded_integer(
                name="DISPATCHER_SMOKE_DB_PORT",
                value=os.environ.get("DISPATCHER_SMOKE_DB_PORT", "5432"),
                minimum=1,
                maximum=65_535,
            ),
            database=_required_environment("DISPATCHER_SMOKE_DB_NAME"),
            username=_required_environment("DISPATCHER_SMOKE_DB_USERNAME"),
            password=_required_environment("DISPATCHER_SMOKE_DB_PASSWORD"),
        )


def _load_pika() -> Any:
    """Import the hash-locked AMQP dependency only during real broker I/O."""

    try:
        import pika
    except ImportError as error:
        raise DispatcherSmokeInfrastructureError("Pinned AMQP smoke dependency is unavailable.") from error
    return pika


def _load_psycopg() -> Any:
    """Import the hash-locked PostgreSQL dependency only during SQL I/O."""

    try:
        import psycopg
    except ImportError as error:
        raise DispatcherSmokeInfrastructureError("Pinned PostgreSQL smoke dependency is unavailable.") from error
    return psycopg


def open_amqp_connection(settings: DispatcherSmokeAmqpSettings) -> Any:
    """Open one bounded AMQP connection as the restricted smoke reader."""

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
        raise DispatcherSmokeInfrastructureError("RabbitMQ smoke connection is unavailable.") from error


def _close_connection_quietly(connection: Any | None) -> None:
    """Release one test connection without replacing an assertion result."""

    if connection is not None:
        with suppress(Exception):
            connection.close()


def preflight_demucs_queue_is_empty(channel: Any, *, queue_name: str = DEMUCS_REQUEST_QUEUE) -> None:
    """Refuse to run if ordinary Demucs work could be consumed by this test.

    Passive declaration requests metadata without creating/changing the queue.
    A nonzero count is a safety failure, not a request to inspect, drain, or
    reorder messages that belong to a genuine worker.
    """

    if queue_name != DEMUCS_REQUEST_QUEUE:
        raise DispatcherSmokeConfigurationError("Dispatcher smoke preflight queue is not approved.")
    try:
        declaration = channel.queue_declare(queue=queue_name, passive=True)
        message_count = declaration.method.message_count
    except Exception as error:
        raise DispatcherSmokeInfrastructureError("RabbitMQ smoke queue preflight is unavailable.") from error
    if not isinstance(message_count, int) or message_count != 0:
        raise DispatcherSmokeAssertionError("Demucs queue is not empty; smoke test would be unsafe.")


def _assert_exact_demucs_payload(body: bytes, expected: ExpectedDemucsRequested) -> None:
    """Validate the immutable JSON body without exposing it in a failure."""

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DispatcherSmokeAssertionError("Dispatcher message body is not valid UTF-8 JSON.") from error
    if not isinstance(payload, Mapping) or set(payload) != {"schema_version", "job_id", "source", "stem_mode"}:
        raise DispatcherSmokeAssertionError("Dispatcher message body has an incompatible contract.")
    source = payload.get("source")
    if (
        payload.get("schema_version") != 1
        or payload.get("job_id") != expected.job_id
        or payload.get("stem_mode") != expected.stem_mode
        or not isinstance(source, Mapping)
        or set(source) != {"bucket", "object_key"}
        or source.get("bucket") != expected.bucket_name
        or source.get("object_key") != expected.object_key
    ):
        raise DispatcherSmokeAssertionError("Dispatcher message body does not match the controlled event.")


def _assert_required_message_properties(properties: Any, expected: ExpectedDemucsRequested) -> None:
    """Validate persistent publisher metadata without trusting the body alone."""

    if (
        getattr(properties, "content_type", None) != "application/json"
        or getattr(properties, "content_encoding", None) != "utf-8"
        or getattr(properties, "delivery_mode", None) != 2
        or getattr(properties, "type", None) != DEMUCS_EVENT_TYPE
        or getattr(properties, "message_id", None) != expected.event_id
        or getattr(properties, "correlation_id", None) != expected.job_id
    ):
        raise DispatcherSmokeAssertionError("Dispatcher message properties do not match the version-1 contract.")


def consume_expected_demucs_request(
    channel: Any,
    *,
    expected: ExpectedDemucsRequested,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Wait for, validate, and acknowledge exactly one controlled message.

    An unexpected delivery is negatively acknowledged with ``requeue=True``
    and this test fails immediately. The empty queue preflight must occur
    before a controlled source event is created.
    """

    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise DispatcherSmokeConfigurationError("Dispatcher smoke timeout is outside its safe bounds.")
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        method_frame, properties, body = channel.basic_get(
            queue=DEMUCS_REQUEST_QUEUE,
            auto_ack=False,
        )
        if method_frame is None:
            sleep_function(1)
            continue
        try:
            if not isinstance(body, bytes):
                raise DispatcherSmokeAssertionError("Dispatcher message body has an invalid client type.")
            _assert_exact_demucs_payload(body, expected)
            _assert_required_message_properties(properties, expected)
        except DispatcherSmokeAssertionError:
            # The reader cannot write/re-route another message. Requeue keeps
            # it for a real Demucs worker after this controlled test exits.
            channel.basic_nack(method_frame.delivery_tag, requeue=True)
            raise
        channel.basic_ack(method_frame.delivery_tag)
        return
    raise DispatcherSmokeAssertionError("Timed out waiting for the controlled Demucs request.")


PUBLISHED_OUTBOX_EVENT_SQL = """
    SELECT
      publication_status,
      published_at IS NOT NULL AS has_published_at,
      lease_token IS NULL AS lease_is_clear,
      lease_expires_at IS NULL AS lease_expiry_is_clear
    FROM public.outbox_events
    WHERE event_id = %s::uuid
      AND job_id = %s::uuid
      AND stage = 'demucs'
      AND stem_name = ''
      AND event_type = 'demucs.requested'
"""


def outbox_event_is_published(cursor: Any, expected: ExpectedDemucsRequested) -> bool:
    """Read only non-sensitive completion flags for the controlled event."""

    cursor.execute(PUBLISHED_OUTBOX_EVENT_SQL, (expected.event_id, expected.job_id))
    row = cursor.fetchone()
    if not isinstance(row, tuple) or len(row) != 4:
        raise DispatcherSmokeInfrastructureError("PostgreSQL smoke assertion returned an unexpected row shape.")
    return row == ("published", True, True, True)


def _read_published_state(settings: DispatcherSmokeDatabaseSettings, expected: ExpectedDemucsRequested) -> bool:
    """Open one short read-only assertion connection with no retained transaction."""

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
            application_name="clouddsp-dispatcher-smoke",
        ) as connection:
            with connection.cursor() as cursor:
                return outbox_event_is_published(cursor, expected)
    except DispatcherSmokeInfrastructureError:
        raise
    except Exception as error:
        raise DispatcherSmokeInfrastructureError("PostgreSQL smoke assertion is unavailable.") from error


def wait_for_outbox_event_published(
    settings: DispatcherSmokeDatabaseSettings,
    expected: ExpectedDemucsRequested,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    read_published: Callable[[DispatcherSmokeDatabaseSettings, ExpectedDemucsRequested], bool] = _read_published_state,
) -> None:
    """Wait for durable publication before consuming the matching queue delivery.

    RabbitMQ can expose a confirmed message just before the dispatcher commits
    the matching PostgreSQL state. Waiting for PostgreSQL first avoids that
    harmless ordering race causing a false integration-test failure.
    """

    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise DispatcherSmokeConfigurationError("Dispatcher smoke timeout is outside its safe bounds.")
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if read_published(settings, expected):
            return
        sleep_function(1)
    raise DispatcherSmokeAssertionError("Timed out waiting for the outbox event to become published.")


def run_preflight(settings: DispatcherSmokeAmqpSettings) -> None:
    """Perform the non-mutating empty-queue safety check for a future Job."""

    connection: Any | None = None
    channel: Any | None = None
    try:
        connection = open_amqp_connection(settings)
        channel = connection.channel()
        preflight_demucs_queue_is_empty(channel, queue_name=settings.queue_name)
    finally:
        if channel is not None and getattr(channel, "is_open", False):
            with suppress(Exception):
                channel.close()
        _close_connection_quietly(connection)


def run_verification(
    *,
    amqp_settings: DispatcherSmokeAmqpSettings,
    database_settings: DispatcherSmokeDatabaseSettings,
    expected: ExpectedDemucsRequested,
    timeout_seconds: int,
) -> None:
    """Assert durable publication, then consume/ack the matching message."""

    wait_for_outbox_event_published(database_settings, expected, timeout_seconds=timeout_seconds)
    connection: Any | None = None
    channel: Any | None = None
    try:
        connection = open_amqp_connection(amqp_settings)
        channel = connection.channel()
        consume_expected_demucs_request(channel, expected=expected, timeout_seconds=timeout_seconds)
    finally:
        if channel is not None and getattr(channel, "is_open", False):
            with suppress(Exception):
                channel.close()
        _close_connection_quietly(connection)


def main() -> int:
    """Run a bounded preflight or post-upload dispatcher verification phase."""

    try:
        mode = _required_environment("DISPATCHER_SMOKE_MODE")
        amqp_settings = DispatcherSmokeAmqpSettings.from_environment()
        if mode == "preflight":
            run_preflight(amqp_settings)
            print("Dispatcher smoke preflight passed: Demucs queue is empty")
            return 0
        if mode != "verify":
            raise DispatcherSmokeConfigurationError("Dispatcher smoke mode must be preflight or verify.")
        database_settings = DispatcherSmokeDatabaseSettings.from_environment()
        expected = ExpectedDemucsRequested.from_environment()
        timeout_seconds = _bounded_integer(
            name="DISPATCHER_SMOKE_TIMEOUT_SECONDS",
            value=os.environ.get("DISPATCHER_SMOKE_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)),
            minimum=1,
            maximum=MAX_TIMEOUT_SECONDS,
        )
        run_verification(
            amqp_settings=amqp_settings,
            database_settings=database_settings,
            expected=expected,
            timeout_seconds=timeout_seconds,
        )
    except (DispatcherSmokeConfigurationError, DispatcherSmokeAssertionError, DispatcherSmokeInfrastructureError) as error:
        # Every public message above is fixed and safe. Never include raw driver
        # errors, message bodies, object keys, tokens, passwords, or SQL rows.
        print(f"Dispatcher smoke test failed: {error}", file=sys.stderr)
        return 1
    print("Dispatcher smoke verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
