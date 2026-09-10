"""Unit tests for the bounded dispatcher smoke verifier.

The tests inject fake channels, properties, clocks, and cursors. They do not
install Pika/Psycopg, contact RabbitMQ/PostgreSQL, consume a real message, or
run a Kubernetes Job. This proves the future test client itself is safe before
it is allowed to observe the production-like Demucs queue.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from dispatcher_smoke_client import (
    DEMUCS_REQUEST_QUEUE,
    DispatcherSmokeAssertionError,
    DispatcherSmokeConfigurationError,
    DispatcherSmokeDatabaseSettings,
    ExpectedDemucsRequested,
    consume_expected_demucs_request,
    outbox_event_is_published,
    preflight_demucs_queue_is_empty,
    wait_for_outbox_event_published,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


def expected_event() -> ExpectedDemucsRequested:
    """Return one realistic contract without reading a live object/event."""

    return ExpectedDemucsRequested(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        object_key=f"uploads/{JOB_ID}/controlled-smoke.wav",
        stem_mode="4-stems",
    )


def message_body() -> bytes:
    """Encode the exact version-1 payload expected from the dispatcher."""

    expected = expected_event()
    return json.dumps(
        {
            "schema_version": 1,
            "job_id": expected.job_id,
            "source": {"bucket": expected.bucket_name, "object_key": expected.object_key},
            "stem_mode": expected.stem_mode,
        }
    ).encode("utf-8")


def properties() -> SimpleNamespace:
    """Return only the persistent AMQP metadata the contract requires."""

    return SimpleNamespace(
        content_type="application/json",
        content_encoding="utf-8",
        delivery_mode=2,
        type="demucs.requested",
        message_id=EVENT_ID,
        correlation_id=JOB_ID,
    )


class ExpectedContractTests(unittest.TestCase):
    """Prove the expected event cannot broaden into another job/object."""

    def test_rejects_an_object_outside_the_controlled_job_prefix(self) -> None:
        """The reader must not inspect a stable object owned by another job."""

        with self.assertRaises(DispatcherSmokeConfigurationError):
            ExpectedDemucsRequested(
                event_id=EVENT_ID,
                job_id=JOB_ID,
                object_key="uploads/another-job/source.wav",
                stem_mode="4-stems",
            )

    def test_rejects_a_non_text_object_key_with_a_safe_configuration_error(self) -> None:
        """Untrusted test configuration must not leak an AttributeError trace."""

        with self.assertRaises(DispatcherSmokeConfigurationError):
            ExpectedDemucsRequested(
                event_id=EVENT_ID,
                job_id=JOB_ID,
                object_key=None,  # type: ignore[arg-type]
                stem_mode="4-stems",
            )

    def test_rejects_noncanonical_uppercase_uuid(self) -> None:
        """Canonical text avoids treating two UUID spellings as one target."""

        with self.assertRaises(DispatcherSmokeConfigurationError):
            ExpectedDemucsRequested(
                event_id=EVENT_ID.upper(),
                job_id=JOB_ID,
                object_key=f"uploads/{JOB_ID}/controlled-smoke.wav",
                stem_mode="4-stems",
            )


class QueueSafetyTests(unittest.TestCase):
    """Prove the reader refuses normal work and acks only its own delivery."""

    def test_preflight_rejects_a_non_empty_demucs_queue(self) -> None:
        """The smoke test must never drain, inspect, or reorder real work."""

        channel = MagicMock()
        channel.queue_declare.return_value = SimpleNamespace(
            method=SimpleNamespace(message_count=1)
        )

        with self.assertRaises(DispatcherSmokeAssertionError):
            preflight_demucs_queue_is_empty(channel)

        channel.queue_declare.assert_called_once_with(queue=DEMUCS_REQUEST_QUEUE, passive=True)

    def test_matching_delivery_is_acknowledged_after_full_contract_validation(self) -> None:
        """The controlled message is removed only after body/properties match."""

        channel = MagicMock()
        delivery = SimpleNamespace(delivery_tag=9)
        channel.basic_get.return_value = (delivery, properties(), message_body())

        consume_expected_demucs_request(
            channel,
            expected=expected_event(),
            timeout_seconds=5,
        )

        channel.basic_get.assert_called_once_with(queue=DEMUCS_REQUEST_QUEUE, auto_ack=False)
        channel.basic_ack.assert_called_once_with(9)
        channel.basic_nack.assert_not_called()

    def test_unexpected_delivery_is_requeued_not_acknowledged(self) -> None:
        """A failed smoke test preserves a real worker message for later work."""

        channel = MagicMock()
        delivery = SimpleNamespace(delivery_tag=10)
        bad_properties = properties()
        bad_properties.message_id = "11111111-1111-4111-8111-111111111111"
        channel.basic_get.return_value = (delivery, bad_properties, message_body())

        with self.assertRaises(DispatcherSmokeAssertionError):
            consume_expected_demucs_request(
                channel,
                expected=expected_event(),
                timeout_seconds=5,
            )

        channel.basic_nack.assert_called_once_with(10, requeue=True)
        channel.basic_ack.assert_not_called()


class PublishedOutboxTests(unittest.TestCase):
    """Prove message inspection waits for authoritative durable completion."""

    def test_published_query_accepts_only_the_complete_terminal_state(self) -> None:
        """Timestamp and cleared lease prevent a partial-status false pass."""

        cursor = MagicMock()
        cursor.fetchone.return_value = ("published", True, True, True)

        self.assertTrue(outbox_event_is_published(cursor, expected_event()))
        query, params = cursor.execute.call_args.args
        self.assertIn("publication_status", query)
        self.assertEqual(params, (EVENT_ID, JOB_ID))

    def test_leased_or_partially_cleared_state_is_not_accepted(self) -> None:
        """A message-visible event still needs its authoritative DB completion."""

        cursor = MagicMock()
        cursor.fetchone.return_value = ("leased", False, False, False)

        self.assertFalse(outbox_event_is_published(cursor, expected_event()))

    def test_wait_retries_until_durable_publication_is_visible(self) -> None:
        """AMQP availability can slightly precede the post-confirm SQL update."""

        settings = DispatcherSmokeDatabaseSettings(
            host="postgresql.test",
            port=5432,
            database="clouddsp_job_api",
            username="smoke-reader",
            password="test-only-password",
        )
        calls: list[bool] = []
        clock = iter((0.0, 0.0, 1.0, 1.0))

        def read_published(_settings, _expected) -> bool:
            calls.append(True)
            return len(calls) == 2

        wait_for_outbox_event_published(
            settings,
            expected_event(),
            timeout_seconds=5,
            sleep_function=MagicMock(),
            monotonic=lambda: next(clock),
            read_published=read_published,
        )

        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
