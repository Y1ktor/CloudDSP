"""Unit tests for the restricted generic-dispatcher Basic Pitch smoke client.

These tests use fake PostgreSQL cursors, RabbitMQ channels, clocks, and client
callbacks.  They do not install Pika/Psycopg, contact a cluster service,
create a Job/outbox row, consume an actual message, or invoke Kubernetes.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

from generic_dispatcher_basic_pitch_smoke import (
    BASIC_PITCH_EVENT_TYPE,
    BASIC_PITCH_REQUEST_QUEUE,
    CLEANUP_CONTROLLED_EVENT_SQL,
    CREATE_CONTROLLED_EVENT_SQL,
    READ_CONTROLLED_STATUS_SQL,
    BasicPitchSmokeAmqpSettings,
    ControlledBasicPitchEvent,
    GenericDispatcherSmokeAssertionError,
    GenericDispatcherSmokeConfigurationError,
    GenericDispatcherSmokeDatabaseSettings,
    cleanup_controlled_event,
    consume_controlled_basic_pitch_request,
    controlled_event_is_published,
    create_controlled_event,
    run_smoke,
    wait_for_controlled_event_published,
)


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"


def controlled_event() -> ControlledBasicPitchEvent:
    """Return one realistic opaque scope without reading a live database."""

    return ControlledBasicPitchEvent(job_id=JOB_ID, event_id=EVENT_ID)


def message_body() -> bytes:
    """Encode the exact immutable v004 Basic Pitch request contract."""

    expected = controlled_event()
    return json.dumps(
        {
            "schema_version": 1,
            "job_id": expected.job_id,
            "stem_name": "vocals",
            "stem": {
                "bucket": "clouddsp-uploads",
                "object_key": f"stems/{expected.job_id}/vocals.wav",
                "content_type": "audio/wav",
                "size_bytes": 1,
                "sha256": "0" * 64,
            },
        }
    ).encode("utf-8")


def properties() -> SimpleNamespace:
    """Return only AMQP properties the persistent dispatcher contract needs."""

    return SimpleNamespace(
        content_type="application/json",
        content_encoding="utf-8",
        delivery_mode=2,
        type=BASIC_PITCH_EVENT_TYPE,
        message_id=EVENT_ID,
        correlation_id=JOB_ID,
    )


def database_settings() -> GenericDispatcherSmokeDatabaseSettings:
    """Build non-secret fake settings without consulting process environment."""

    return GenericDispatcherSmokeDatabaseSettings(
        host="postgresql.test",
        port=5432,
        database="clouddsp_job_api",
        username="test-smoke",
        password="test-password",
    )


def amqp_settings() -> BasicPitchSmokeAmqpSettings:
    """Build non-secret fake AMQP settings without a broker connection."""

    return BasicPitchSmokeAmqpSettings(
        host="rabbitmq.test",
        port=5672,
        username="test-smoke",
        password="test-password",
    )


class ControlledEventTests(unittest.TestCase):
    """Prove the client has exactly one generated Job/event correlation scope."""

    def test_rejects_matching_job_and_event_identifiers(self) -> None:
        """One UUID cannot safely identify both durable records."""

        with self.assertRaises(GenericDispatcherSmokeAssertionError):
            ControlledBasicPitchEvent(job_id=JOB_ID, event_id=JOB_ID)

    def test_rejects_noncanonical_identifier(self) -> None:
        """Canonical text prevents UUID spelling aliases in equality checks."""

        with self.assertRaises(GenericDispatcherSmokeAssertionError):
            ControlledBasicPitchEvent(job_id=JOB_ID.upper(), event_id=EVENT_ID)

    def test_new_generates_two_distinct_identifiers(self) -> None:
        """The client supplies only two opaque random values to PostgreSQL."""

        values = iter((UUID(JOB_ID), UUID(EVENT_ID)))

        self.assertEqual(
            ControlledBasicPitchEvent.new(uuid_factory=lambda: next(values)),
            controlled_event(),
        )


class RestrictedDatabaseFunctionTests(unittest.TestCase):
    """Prove every database operation stays within its reviewed function boundary."""

    def test_sql_uses_only_the_three_smoke_functions_not_raw_tables(self) -> None:
        """A future change must not quietly grant the test direct durable access."""

        for statement in (
            CREATE_CONTROLLED_EVENT_SQL,
            READ_CONTROLLED_STATUS_SQL,
            CLEANUP_CONTROLLED_EVENT_SQL,
        ):
            self.assertNotIn("public.jobs", statement)
            self.assertNotIn("public.outbox_events", statement)
        self.assertIn("clouddsp_generic_dispatcher_smoke_create", CREATE_CONTROLLED_EVENT_SQL)
        self.assertIn("clouddsp_generic_dispatcher_smoke_status", READ_CONTROLLED_STATUS_SQL)
        self.assertIn("clouddsp_generic_dispatcher_smoke_cleanup", CLEANUP_CONTROLLED_EVENT_SQL)

    def test_create_accepts_only_the_function_response_for_its_exact_ids(self) -> None:
        """A mismatched row cannot cause the client to consume another event."""

        cursor = MagicMock()
        cursor.fetchone.return_value = (JOB_ID, EVENT_ID)

        create_controlled_event(cursor, controlled_event())

        cursor.execute.assert_called_once_with(CREATE_CONTROLLED_EVENT_SQL, (JOB_ID, EVENT_ID))

    def test_create_rejects_a_mismatched_function_response(self) -> None:
        """The function's result is a correlation assertion, not trusted input."""

        cursor = MagicMock()
        cursor.fetchone.return_value = (JOB_ID, "11111111-1111-4111-8111-111111111111")

        with self.assertRaises(GenericDispatcherSmokeAssertionError):
            create_controlled_event(cursor, controlled_event())

    def test_status_accepts_only_published_for_its_exact_event(self) -> None:
        """A successful queue publish still needs the authoritative DB state."""

        cursor = MagicMock()
        cursor.fetchone.return_value = (EVENT_ID, "published")

        self.assertTrue(controlled_event_is_published(cursor, controlled_event()))
        cursor.execute.assert_called_once_with(READ_CONTROLLED_STATUS_SQL, (JOB_ID,))

    def test_status_waits_through_pending_and_rejects_foreign_event(self) -> None:
        """The dispatcher may lease normally, but another event never passes."""

        cursor = MagicMock()
        cursor.fetchone.return_value = (EVENT_ID, "leased")
        self.assertFalse(controlled_event_is_published(cursor, controlled_event()))

        cursor.fetchone.return_value = ("11111111-1111-4111-8111-111111111111", "published")
        with self.assertRaises(GenericDispatcherSmokeAssertionError):
            controlled_event_is_published(cursor, controlled_event())

    def test_cleanup_requires_the_function_to_authorize_deletion(self) -> None:
        """The client cannot turn a false cleanup result into a successful pass."""

        cursor = MagicMock()
        cursor.fetchone.return_value = (True,)
        cleanup_controlled_event(cursor, controlled_event())
        cursor.execute.assert_called_once_with(CLEANUP_CONTROLLED_EVENT_SQL, (JOB_ID,))

        cursor.fetchone.return_value = (False,)
        with self.assertRaises(GenericDispatcherSmokeAssertionError):
            cleanup_controlled_event(cursor, controlled_event())


class PublicationWaitTests(unittest.TestCase):
    """Prove polling is bounded and tolerates the normal pending-to-published delay."""

    def test_wait_retries_until_published(self) -> None:
        """AMQP confirmation can precede the dispatcher's status update briefly."""

        calls: list[bool] = []
        clock = iter((0.0, 0.0, 1.0, 1.0))

        def read_published(_settings, _event):
            calls.append(True)
            return len(calls) == 2

        wait_for_controlled_event_published(
            database_settings(),
            controlled_event(),
            timeout_seconds=5,
            sleep_function=MagicMock(),
            monotonic=lambda: next(clock),
            read_published=read_published,
        )

        self.assertEqual(len(calls), 2)

    def test_wait_rejects_timeout_without_unsafe_cleanup(self) -> None:
        """A stuck event remains durable for diagnosis rather than being erased."""

        clock = iter((0.0, 0.0, 1.0, 1.0, 2.0))

        with self.assertRaises(GenericDispatcherSmokeAssertionError):
            wait_for_controlled_event_published(
                database_settings(),
                controlled_event(),
                timeout_seconds=1,
                sleep_function=MagicMock(),
                monotonic=lambda: next(clock),
                read_published=lambda _settings, _event: False,
            )


class BasicPitchQueueTests(unittest.TestCase):
    """Prove the restricted reader acknowledges only its exact durable delivery."""

    def test_matching_delivery_is_acknowledged_after_full_validation(self) -> None:
        """The Basic Pitch request is removed only when its full envelope matches."""

        channel = MagicMock()
        channel.basic_get.return_value = (SimpleNamespace(delivery_tag=9), properties(), message_body())

        consume_controlled_basic_pitch_request(
            channel,
            expected=controlled_event(),
            timeout_seconds=5,
        )

        channel.basic_get.assert_called_once_with(queue=BASIC_PITCH_REQUEST_QUEUE, auto_ack=False)
        channel.basic_ack.assert_called_once_with(9)
        channel.basic_nack.assert_not_called()

    def test_foreign_delivery_is_requeued_and_never_acknowledged(self) -> None:
        """The test refuses to take a normal worker message from the shared queue."""

        channel = MagicMock()
        foreign_properties = properties()
        foreign_properties.message_id = "11111111-1111-4111-8111-111111111111"
        channel.basic_get.return_value = (
            SimpleNamespace(delivery_tag=10),
            foreign_properties,
            message_body(),
        )

        with self.assertRaises(GenericDispatcherSmokeAssertionError):
            consume_controlled_basic_pitch_request(
                channel,
                expected=controlled_event(),
                timeout_seconds=5,
            )

        channel.basic_nack.assert_called_once_with(10, requeue=True)
        channel.basic_ack.assert_not_called()

    def test_wrong_stem_contract_is_requeued(self) -> None:
        """A correct event ID cannot bypass v004 body validation."""

        channel = MagicMock()
        body = json.loads(message_body())
        body["stem_name"] = "bass"
        channel.basic_get.return_value = (
            SimpleNamespace(delivery_tag=11),
            properties(),
            json.dumps(body).encode("utf-8"),
        )

        with self.assertRaises(GenericDispatcherSmokeAssertionError):
            consume_controlled_basic_pitch_request(
                channel,
                expected=controlled_event(),
                timeout_seconds=5,
            )

        channel.basic_nack.assert_called_once_with(11, requeue=True)


class SmokeOrderTests(unittest.TestCase):
    """Prove cleanup comes only after publication and matching AMQP acknowledgement."""

    def test_run_orders_create_publish_delivery_then_cleanup(self) -> None:
        """The test must not delete the durable event before its message is verified."""

        calls: list[str] = []
        values = iter((UUID(JOB_ID), UUID(EVENT_ID)))

        def create(_database, _event):
            calls.append("create")

        def publish(_database, _event):
            calls.append("published")

        def delivery(_amqp, _event):
            calls.append("delivery")

        def cleanup(_database, _event):
            calls.append("cleanup")

        run_smoke(
            database_settings=database_settings(),
            amqp_settings=amqp_settings(),
            timeout_seconds=5,
            uuid_factory=lambda: next(values),
            create_event=create,
            wait_for_published=publish,
            verify_delivery=delivery,
            remove_event=cleanup,
        )

        self.assertEqual(calls, ["create", "published", "delivery", "cleanup"])

    def test_run_does_not_cleanup_when_queue_verification_fails(self) -> None:
        """A failed delivery must retain evidence rather than hide the problem."""

        cleaned = False
        values = iter((UUID(JOB_ID), UUID(EVENT_ID)))

        def fail_delivery(_amqp, _event):
            raise GenericDispatcherSmokeAssertionError("controlled test failure")

        def cleanup(_database, _event):
            nonlocal cleaned
            cleaned = True

        with self.assertRaises(GenericDispatcherSmokeAssertionError):
            run_smoke(
                database_settings=database_settings(),
                amqp_settings=amqp_settings(),
                timeout_seconds=5,
                uuid_factory=lambda: next(values),
                create_event=lambda _database, _event: None,
                wait_for_published=lambda _database, _event: None,
                verify_delivery=fail_delivery,
                remove_event=cleanup,
            )

        self.assertFalse(cleaned)


if __name__ == "__main__":
    unittest.main()
