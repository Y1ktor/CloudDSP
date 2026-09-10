"""Unit tests for manual-ack source-intake RabbitMQ adapter behaviour.

The tests use a fake Pika module and mock AMQP channel. They open no broker
connection and do not call the message handler's MinIO/PostgreSQL dependencies.
They prove the vital ordering rule: `basic_ack` happens only after a durable-
safe handler result, while handler errors remain unacknowledged.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.amqp_consumer import (
    DEFAULT_AMQP_HOST,
    RabbitMQConfigurationError,
    RabbitMQSettings,
    RabbitMQUnavailable,
    configure_source_intake_channel,
    consume_one_source_intake_delivery,
    open_rabbitmq_connection,
)
from app.message_handler import (
    SourceIntakeEnvelopeOutcome,
    SourceIntakeMessageResult,
)


def settings() -> RabbitMQSettings:
    """Return non-secret test-only configuration for a fake Pika connection."""

    return RabbitMQSettings(
        host="rabbitmq.test",
        port=5672,
        username="clouddsp-upload-intake",
        password="not-a-real-password",
    )


def acknowledgement_safe_result() -> SourceIntakeMessageResult:
    """Return a realistic handler result that permits a manual acknowledgement."""

    return SourceIntakeMessageResult(
        envelope_outcome=SourceIntakeEnvelopeOutcome.HANDLED,
        candidate_results=(),
        ignored_records=(),
    )


class RabbitMQSettingsTests(unittest.TestCase):
    """Prove defaults use private broker DNS and hide the application password."""

    @patch.dict(
        os.environ,
        {
            "RABBITMQ_UPLOAD_INTAKE_USERNAME": "clouddsp-upload-intake",
            "RABBITMQ_UPLOAD_INTAKE_PASSWORD": "test-only-password",
        },
        clear=True,
    )
    def test_settings_default_to_private_service_dns_and_hide_password_repr(self) -> None:
        """A Pod must not connect through the Mac/browser network path."""

        loaded = RabbitMQSettings.from_environment()

        self.assertEqual(loaded.host, DEFAULT_AMQP_HOST)
        self.assertEqual(loaded.queue_name, "clouddsp.source-intake")
        self.assertNotIn("test-only-password", repr(loaded))

    @patch.dict(
        os.environ,
        {
            "UPLOAD_INTAKE_AMQP_HOST": "rabbitmq.localhost",
            "RABBITMQ_UPLOAD_INTAKE_USERNAME": "clouddsp-upload-intake",
            "RABBITMQ_UPLOAD_INTAKE_PASSWORD": "test-only-password",
        },
        clear=True,
    )
    def test_rejects_browser_facing_broker_host(self) -> None:
        """RabbitMQ must never be exposed/routed through a browser endpoint."""

        with self.assertRaises(RabbitMQConfigurationError):
            RabbitMQSettings.from_environment()


class RabbitMQConnectionTests(unittest.TestCase):
    """Prove the adapter builds Pika parameters without a real AMQP socket."""

    @patch("app.amqp_consumer._load_pika")
    def test_open_connection_uses_restricted_vhost_and_bounded_parameters(self, load_pika) -> None:
        """The settings are passed to Pika, but no message is consumed here."""

        pika = MagicMock()
        load_pika.return_value = pika
        connection = MagicMock()
        pika.BlockingConnection.return_value = connection

        returned_connection = open_rabbitmq_connection(settings())

        self.assertIs(returned_connection, connection)
        pika.PlainCredentials.assert_called_once_with("clouddsp-upload-intake", "not-a-real-password")
        pika.ConnectionParameters.assert_called_once()
        parameters_kwargs = pika.ConnectionParameters.call_args.kwargs
        self.assertEqual(parameters_kwargs["host"], "rabbitmq.test")
        self.assertEqual(parameters_kwargs["virtual_host"], "/clouddsp")
        self.assertEqual(parameters_kwargs["connection_attempts"], 3)
        self.assertEqual(parameters_kwargs["heartbeat"], 30)

    @patch("app.amqp_consumer._load_pika")
    def test_connection_error_becomes_safe_retryable_category(self, load_pika) -> None:
        """A Pika diagnostic cannot leak through the consumer supervisor."""

        pika = MagicMock()
        pika.BlockingConnection.side_effect = RuntimeError("private broker diagnostic")
        load_pika.return_value = pika

        with self.assertRaises(RabbitMQUnavailable) as raised:
            open_rabbitmq_connection(settings())

        self.assertEqual(str(raised.exception), "RabbitMQ upload-intake connection is unavailable.")


class ManualAcknowledgementTests(unittest.TestCase):
    """Prove the adapter's receive/handle/acknowledge state machine."""

    def test_channel_configuration_sets_prefetch_one_and_checks_queue_passively(self) -> None:
        """One consumer Pod receives at most one unacknowledged delivery at once."""

        channel = MagicMock()

        configure_source_intake_channel(channel, settings=settings())

        channel.basic_qos.assert_called_once_with(prefetch_count=1)
        channel.queue_declare.assert_called_once_with(
            queue="clouddsp.source-intake",
            passive=True,
        )

    def test_empty_queue_returns_false_without_handler_or_acknowledgement(self) -> None:
        """Polling an empty queue must not create an artificial acknowledgement."""

        channel = MagicMock()
        channel.basic_get.return_value = (None, None, None)
        handler = MagicMock()

        consumed = consume_one_source_intake_delivery(
            channel,
            settings=settings(),
            handle_message=handler,
        )

        self.assertFalse(consumed)
        channel.basic_get.assert_called_once_with(queue="clouddsp.source-intake", auto_ack=False)
        handler.assert_not_called()
        channel.basic_ack.assert_not_called()

    def test_durable_safe_handler_result_is_acknowledged_with_its_delivery_tag(self) -> None:
        """The broker removes a delivery only after PostgreSQL work completed."""

        channel = MagicMock()
        method = SimpleNamespace(delivery_tag=42)
        channel.basic_get.return_value = (method, None, b'{"Records": []}')
        handler = MagicMock(return_value=acknowledgement_safe_result())

        consumed = consume_one_source_intake_delivery(
            channel,
            settings=settings(),
            handle_message=handler,
        )

        self.assertTrue(consumed)
        handler.assert_called_once_with(b'{"Records": []}')
        channel.basic_ack.assert_called_once_with(42)
        channel.basic_nack.assert_not_called()

    def test_handler_failure_propagates_without_ack_or_immediate_requeue(self) -> None:
        """An outage retains evidence until the later retry/DLQ task exists."""

        channel = MagicMock()
        method = SimpleNamespace(delivery_tag=42)
        channel.basic_get.return_value = (method, None, b'{"Records": []}')
        handler = MagicMock(side_effect=RuntimeError("private handler diagnostic"))

        with self.assertRaisesRegex(RuntimeError, "private handler diagnostic"):
            consume_one_source_intake_delivery(
                channel,
                settings=settings(),
                handle_message=handler,
            )

        channel.basic_ack.assert_not_called()
        channel.basic_nack.assert_not_called()

    def test_invalid_delivery_body_is_not_acknowledged(self) -> None:
        """A broken AMQP client contract cannot silently remove a message."""

        channel = MagicMock()
        method = SimpleNamespace(delivery_tag=42)
        channel.basic_get.return_value = (method, None, "not-bytes")

        with self.assertRaises(RabbitMQUnavailable):
            consume_one_source_intake_delivery(
                channel,
                settings=settings(),
                handle_message=MagicMock(),
            )

        channel.basic_ack.assert_not_called()
