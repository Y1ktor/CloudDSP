"""Unit tests for upload-intake's connection-cycle supervisor.

These tests inject fake AMQP connections, sleep functions, and adapters. They
never install Pika/Psycopg/Boto3, contact RabbitMQ/PostgreSQL/MinIO, or process
a Kubernetes workload. Their purpose is to make manual-ack recovery and
graceful connection closing observable before a container exists.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.amqp_consumer import RabbitMQSettings
from app.consumer_runtime import (
    ConsumerRuntimeConfigurationError,
    ConsumerRuntimeSettings,
    build_source_intake_handler,
    consume_connection_cycle,
    run_consumer_forever,
)
from app.message_handler import SourceIntakeEnvelopeOutcome, SourceIntakeMessageResult


def amqp_settings() -> RabbitMQSettings:
    """Create non-secret fake AMQP settings for one isolated runtime test."""

    return RabbitMQSettings(
        host="rabbitmq.test",
        port=5672,
        username="clouddsp-upload-intake",
        password="test-only-password",
    )


def acknowledgement_safe_result() -> SourceIntakeMessageResult:
    """Return the smallest realistic result expected by the AMQP adapter."""

    return SourceIntakeMessageResult(
        envelope_outcome=SourceIntakeEnvelopeOutcome.HANDLED,
        candidate_results=(),
        ignored_records=(),
    )


class ConsumerRuntimeSettingsTests(unittest.TestCase):
    """Prove the Pod timing values are bounded before the loop starts."""

    @patch.dict(
        os.environ,
        {
            "UPLOAD_INTAKE_IDLE_POLL_SECONDS": "2",
            "UPLOAD_INTAKE_RECONNECT_DELAY_SECONDS": "7",
        },
        clear=True,
    )
    def test_loads_explicit_non_secret_timing_values(self) -> None:
        """A reviewed Deployment could set these values through a ConfigMap."""

        loaded = ConsumerRuntimeSettings.from_environment()

        self.assertEqual(loaded.idle_poll_seconds, 2)
        self.assertEqual(loaded.reconnect_delay_seconds, 7)

    @patch.dict(os.environ, {"UPLOAD_INTAKE_IDLE_POLL_SECONDS": "0"}, clear=True)
    def test_rejects_zero_idle_poll_time(self) -> None:
        """Zero would turn an idle, empty queue into a CPU busy loop."""

        with self.assertRaises(ConsumerRuntimeConfigurationError):
            ConsumerRuntimeSettings.from_environment()


class HandlerCompositionTests(unittest.TestCase):
    """Prove dependencies are assembled once without opening a database yet."""

    @patch("app.consumer_runtime.handle_source_intake_message")
    @patch("app.consumer_runtime.create_boto3_head_object_client")
    @patch("app.consumer_runtime.ObjectStorageSettings.from_environment")
    @patch("app.consumer_runtime.PsycopgSourceIntakeDatabase")
    def test_handler_binds_one_database_and_s3_client(
        self,
        database_type,
        load_object_settings,
        create_object_client,
        handle_message,
    ) -> None:
        """The result passes only the expected injected dependencies onward."""

        database = MagicMock()
        object_settings = SimpleNamespace()
        object_client = MagicMock()
        expected = acknowledgement_safe_result()
        database_type.return_value = database
        load_object_settings.return_value = object_settings
        create_object_client.return_value = object_client
        handle_message.return_value = expected

        bound_handler = build_source_intake_handler()
        returned = bound_handler(b"test-message")

        self.assertIs(returned, expected)
        create_object_client.assert_called_once_with(object_settings)
        handle_message.assert_called_once_with(
            b"test-message",
            database=database,
            object_client=object_client,
            object_storage_settings=object_settings,
        )


class ConnectionCycleTests(unittest.TestCase):
    """Prove open/configure/poll/close ordering with no live AMQP connection."""

    def test_successful_delivery_then_stop_closes_connection(self) -> None:
        """A cooperative SIGTERM-equivalent closes only after the current work."""

        channel = MagicMock()
        connection = MagicMock()
        connection.channel.return_value = channel
        stopped = {"value": False}
        configure_channel = MagicMock()
        handler = MagicMock(return_value=acknowledgement_safe_result())

        def consume_one(*_args, **_kwargs) -> bool:
            # The AMQP adapter itself would have acknowledged this successful
            # message.  Set the flag so the runtime exits before a second poll.
            stopped["value"] = True
            return True

        consume_connection_cycle(
            amqp_settings=amqp_settings(),
            handle_message=handler,
            idle_poll_seconds=1,
            stop_requested=lambda: stopped["value"],
            connection_factory=MagicMock(return_value=connection),
            configure_channel=configure_channel,
            consume_one=consume_one,
        )

        configure_channel.assert_called_once_with(channel, settings=amqp_settings())
        connection.close.assert_called_once_with()

    def test_handler_failure_closes_connection_and_escapes_without_extra_ack_logic(self) -> None:
        """The supervisor gets the failure after the connection is released."""

        channel = MagicMock()
        connection = MagicMock()
        connection.channel.return_value = channel
        consume_one = MagicMock(side_effect=RuntimeError("test processing outage"))

        with self.assertRaisesRegex(RuntimeError, "test processing outage"):
            consume_connection_cycle(
                amqp_settings=amqp_settings(),
                handle_message=MagicMock(),
                idle_poll_seconds=1,
                stop_requested=lambda: False,
                connection_factory=MagicMock(return_value=connection),
                configure_channel=MagicMock(),
                consume_one=consume_one,
            )

        # The runtime cannot accidentally ack/nack: it receives only the
        # adapter callable, and its only broker-lifetime action is close().
        connection.close.assert_called_once_with()

    def test_empty_poll_sleeps_once_then_gracefully_closes(self) -> None:
        """An idle queue uses the bounded pause rather than a tight loop."""

        channel = MagicMock()
        connection = MagicMock()
        connection.channel.return_value = channel
        stopped = {"value": False}
        sleep = MagicMock(side_effect=lambda _seconds: stopped.__setitem__("value", True))

        consume_connection_cycle(
            amqp_settings=amqp_settings(),
            handle_message=MagicMock(),
            idle_poll_seconds=2,
            stop_requested=lambda: stopped["value"],
            sleep_function=sleep,
            connection_factory=MagicMock(return_value=connection),
            configure_channel=MagicMock(),
            consume_one=MagicMock(return_value=False),
        )

        sleep.assert_called_once_with(2)
        connection.close.assert_called_once_with()


class ConsumerSupervisorTests(unittest.TestCase):
    """Prove a failed cycle pauses before a reconnect instead of hot-looping."""

    def test_failed_cycle_notifies_waits_and_stops_before_reconnecting(self) -> None:
        """One safe failed delivery yields one bounded supervisor backoff."""

        channel = MagicMock()
        connection = MagicMock()
        connection.channel.return_value = channel
        stopped = {"value": False}
        sleep_seconds: list[float] = []
        failure_notifier = MagicMock()

        def sleep(seconds: float) -> None:
            sleep_seconds.append(seconds)
            # Simulate SIGTERM arriving during reconnect backoff.  The loop
            # must not make a second broker connection after the pause.
            stopped["value"] = True

        run_consumer_forever(
            amqp_settings=amqp_settings(),
            handle_message=MagicMock(),
            runtime_settings=ConsumerRuntimeSettings(
                idle_poll_seconds=1,
                reconnect_delay_seconds=5,
            ),
            stop_requested=lambda: stopped["value"],
            sleep_function=sleep,
            failure_notifier=failure_notifier,
            connection_factory=MagicMock(return_value=connection),
            configure_channel=MagicMock(),
            consume_one=MagicMock(side_effect=RuntimeError("test processing outage")),
        )

        failure_notifier.assert_called_once_with()
        self.assertEqual(sleep_seconds, [5])
        connection.close.assert_called_once_with()
