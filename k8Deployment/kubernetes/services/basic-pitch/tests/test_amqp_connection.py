"""Unit tests for Basic Pitch's private RabbitMQ connection configuration.

Pika is mocked and every credential is synthetic. These tests open no broker
socket, channel, queue, database, object-storage connection, model process, or
Kubernetes resource; they verify only the connection contract.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.amqp_connection import (
    BASIC_PITCH_AMQP_VHOST,
    BASIC_PITCH_REQUEST_QUEUE,
    DEFAULT_BASIC_PITCH_AMQP_HOST,
    BasicPitchAMQPConfigurationError,
    BasicPitchAMQPConnectionUnavailable,
    BasicPitchAMQPSettings,
    open_basic_pitch_rabbitmq_connection,
)


VALID_ENVIRONMENT = {
    "RABBITMQ_BASIC_PITCH_USERNAME": "clouddsp-basic-pitch",
    # Test-only text, never copied from the ignored local Kubernetes Secret.
    "RABBITMQ_BASIC_PITCH_PASSWORD": "unit-test-basic-pitch-rabbitmq-password",
}


def settings() -> BasicPitchAMQPSettings:
    """Return a direct, synthetic configuration for mocked Pika assertions."""

    return BasicPitchAMQPSettings(
        host=DEFAULT_BASIC_PITCH_AMQP_HOST,
        port=5672,
        username="clouddsp-basic-pitch",
        password="not-a-real-password",
    )


def fake_pika() -> SimpleNamespace:
    """Return the minimal Pika connection-only surface used by this boundary."""

    return SimpleNamespace(
        PlainCredentials=MagicMock(),
        ConnectionParameters=MagicMock(),
        BlockingConnection=MagicMock(),
    )


class BasicPitchAMQPSettingsTests(unittest.TestCase):
    """Prove a future Pod cannot widen its private no-tag broker target."""

    def settings_from(self, overrides: dict[str, str] | None = None) -> BasicPitchAMQPSettings:
        """Load one isolated synthetic environment for a settings assertion."""

        environment = dict(VALID_ENVIRONMENT)
        if overrides:
            environment.update(overrides)
        with patch.dict(os.environ, environment, clear=True):
            return BasicPitchAMQPSettings.from_environment()

    def test_defaults_to_private_plain_amqp_and_hides_password(self) -> None:
        """The Pod gets a ClusterIP endpoint, not a browser/TLS/host route."""

        loaded = self.settings_from()

        self.assertEqual(loaded.host, DEFAULT_BASIC_PITCH_AMQP_HOST)
        self.assertEqual(loaded.virtual_host, BASIC_PITCH_AMQP_VHOST)
        self.assertEqual(loaded.queue_name, BASIC_PITCH_REQUEST_QUEUE)
        self.assertNotIn(VALID_ENVIRONMENT["RABBITMQ_BASIC_PITCH_PASSWORD"], repr(loaded))

    def test_missing_password_fails_before_pika_is_imported(self) -> None:
        """A malformed Secret cannot trigger a broker connection attempt."""

        environment = dict(VALID_ENVIRONMENT)
        environment.pop("RABBITMQ_BASIC_PITCH_PASSWORD")
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(BasicPitchAMQPConfigurationError) as raised:
                BasicPitchAMQPSettings.from_environment()

        self.assertEqual(
            str(raised.exception),
            "Required environment variable RABBITMQ_BASIC_PITCH_PASSWORD is absent.",
        )

    def test_rejects_host_port_topology_or_identity_widening(self) -> None:
        """Environment settings cannot redirect this credential to another broker surface."""

        for overrides in (
            {"BASIC_PITCH_AMQP_HOST": "rabbitmq.localhost"},
            {"BASIC_PITCH_AMQP_PORT": "15672"},
            {"BASIC_PITCH_AMQP_VHOST": "/"},
            {"BASIC_PITCH_AMQP_QUEUE": "clouddsp.source-intake"},
            {"RABBITMQ_BASIC_PITCH_USERNAME": "clouddsp-admin"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(BasicPitchAMQPConfigurationError):
                    self.settings_from(overrides)

    def test_rejects_invalid_bounded_connection_timings(self) -> None:
        """A bad environment cannot turn one future connection attempt into a hang."""

        for overrides in (
            {"BASIC_PITCH_AMQP_CONNECT_TIMEOUT_SECONDS": "0"},
            {"BASIC_PITCH_AMQP_CONNECT_TIMEOUT_SECONDS": "not-a-number"},
            {"BASIC_PITCH_AMQP_HEARTBEAT_SECONDS": "301"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(BasicPitchAMQPConfigurationError):
                    self.settings_from(overrides)


class BasicPitchAMQPConnectionTests(unittest.TestCase):
    """Prove Pika receives one bounded private AMQP connection configuration."""

    @patch("app.amqp_connection._load_pika")
    def test_open_connection_uses_restricted_identity_without_tls_options(self, load_pika) -> None:
        """Socket creation alone cannot consume, configure, or acknowledge broker state."""

        pika = fake_pika()
        connection = MagicMock()
        pika.BlockingConnection.return_value = connection
        load_pika.return_value = pika

        returned = open_basic_pitch_rabbitmq_connection(settings())

        self.assertIs(returned, connection)
        pika.PlainCredentials.assert_called_once_with("clouddsp-basic-pitch", "not-a-real-password")
        parameters = pika.ConnectionParameters.call_args.kwargs
        self.assertEqual(parameters["host"], DEFAULT_BASIC_PITCH_AMQP_HOST)
        self.assertEqual(parameters["port"], 5672)
        self.assertEqual(parameters["virtual_host"], BASIC_PITCH_AMQP_VHOST)
        self.assertEqual(parameters["connection_attempts"], 3)
        self.assertEqual(parameters["heartbeat"], 30)
        self.assertNotIn("ssl_options", parameters)

    @patch("app.amqp_connection._load_pika")
    def test_direct_construction_cannot_widen_before_pika_import(self, load_pika) -> None:
        """A frozen dataclass must not bypass the environment validation contract."""

        widened = BasicPitchAMQPSettings(
            host=DEFAULT_BASIC_PITCH_AMQP_HOST,
            port=15672,
            username="clouddsp-basic-pitch",
            password="not-a-real-password",
        )

        with self.assertRaises(BasicPitchAMQPConfigurationError):
            open_basic_pitch_rabbitmq_connection(widened)

        load_pika.assert_not_called()

    @patch("app.amqp_connection._load_pika")
    def test_connection_failure_becomes_safe_retryable_category(self, load_pika) -> None:
        """Raw Pika/broker diagnostics must not enter future normal worker logs."""

        pika = fake_pika()
        pika.BlockingConnection.side_effect = RuntimeError("private broker diagnostic")
        load_pika.return_value = pika

        with self.assertRaises(BasicPitchAMQPConnectionUnavailable) as raised:
            open_basic_pitch_rabbitmq_connection(settings())

        self.assertEqual(str(raised.exception), "RabbitMQ Basic Pitch connection is unavailable.")
        self.assertNotIn("private broker diagnostic", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
