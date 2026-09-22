"""Unit tests for Demucs RabbitMQ settings and bounded Pika connection setup.

The Pika module is mocked, and every environment credential is synthetic. The
tests open no broker socket, consume/acknowledge no message, touch no database
or storage object, run no model, and create no Kubernetes resource.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.amqp_connection import (
    DEFAULT_DEMUCS_AMQP_HOST,
    DEMUCS_AMQP_VHOST,
    DemucsAMQPConfigurationError,
    DemucsAMQPConnectionUnavailable,
    DemucsAMQPSettings,
    open_demucs_rabbitmq_connection,
    validate_demucs_amqp_settings,
)
from app.amqp_manual_ack import DEMUCS_REQUEST_QUEUE


VALID_ENVIRONMENT = {
    "RABBITMQ_DEMUCS_USERNAME": "clouddsp-demucs",
    # Test-only text; it is not copied from the ignored local Secret.
    "RABBITMQ_DEMUCS_PASSWORD": "unit-test-demucs-rabbitmq-password",
}


def settings() -> DemucsAMQPSettings:
    """Return explicit non-secret settings for mocked Pika assertions."""

    return DemucsAMQPSettings(
        host=DEFAULT_DEMUCS_AMQP_HOST,
        port=5672,
        username="clouddsp-demucs",
        password="not-a-real-password",
    )


def fake_pika() -> SimpleNamespace:
    """Build the minimal connection-only Pika surface required by this module."""

    return SimpleNamespace(
        PlainCredentials=MagicMock(),
        ConnectionParameters=MagicMock(),
        BlockingConnection=MagicMock(),
    )


class DemucsAMQPSettingsTests(unittest.TestCase):
    """Prove a future Pod cannot widen its no-tag consumer connection target."""

    def settings_from(self, overrides: dict[str, str] | None = None) -> DemucsAMQPSettings:
        """Load an isolated synthetic environment for one settings assertion."""

        environment = dict(VALID_ENVIRONMENT)
        if overrides:
            environment.update(overrides)
        with patch.dict(os.environ, environment, clear=True):
            return DemucsAMQPSettings.from_environment()

    def test_defaults_to_private_service_and_hides_rabbitmq_password(self) -> None:
        """No browser/Mac endpoint or mounted password enters normal diagnostics."""

        loaded = self.settings_from()

        self.assertEqual(loaded.host, DEFAULT_DEMUCS_AMQP_HOST)
        self.assertEqual(loaded.virtual_host, DEMUCS_AMQP_VHOST)
        self.assertEqual(loaded.queue_name, DEMUCS_REQUEST_QUEUE)
        self.assertNotIn(VALID_ENVIRONMENT["RABBITMQ_DEMUCS_PASSWORD"], repr(loaded))

    def test_missing_password_fails_without_disclosing_or_loading_a_client(self) -> None:
        """A malformed Secret is configuration failure before Pika is needed."""

        environment = dict(VALID_ENVIRONMENT)
        environment.pop("RABBITMQ_DEMUCS_PASSWORD")
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(DemucsAMQPConfigurationError) as raised:
                DemucsAMQPSettings.from_environment()

        self.assertEqual(
            str(raised.exception),
            "Required environment variable RABBITMQ_DEMUCS_PASSWORD is absent.",
        )

    def test_rejects_host_port_topology_or_identity_that_widens_the_worker(self) -> None:
        """Deployment variables cannot select another broker resource or account."""

        for overrides in (
            {"DEMUCS_AMQP_HOST": "rabbitmq.localhost"},
            {"DEMUCS_AMQP_PORT": "15672"},
            {"DEMUCS_AMQP_VHOST": "/"},
            {"DEMUCS_AMQP_QUEUE": "clouddsp.source-intake"},
            {"RABBITMQ_DEMUCS_USERNAME": "clouddsp-admin"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(DemucsAMQPConfigurationError):
                    self.settings_from(overrides)

    def test_rejects_invalid_bounded_connection_timings(self) -> None:
        """A malformed environment cannot turn one connection attempt into a hang."""

        for overrides in (
            {"DEMUCS_AMQP_CONNECT_TIMEOUT_SECONDS": "0"},
            {"DEMUCS_AMQP_CONNECT_TIMEOUT_SECONDS": "not-a-number"},
            {"DEMUCS_AMQP_HEARTBEAT_SECONDS": "301"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(DemucsAMQPConfigurationError):
                    self.settings_from(overrides)

    def test_direct_settings_construction_cannot_widen_restricted_authority(self) -> None:
        """The factory rejects a hand-built foreign host/queue/account before I/O."""

        for widened in (
            DemucsAMQPSettings(
                host="rabbitmq.localhost",
                port=5672,
                username="clouddsp-demucs",
                password="not-a-real-password",
            ),
            DemucsAMQPSettings(
                host=DEFAULT_DEMUCS_AMQP_HOST,
                port=5672,
                username="clouddsp-demucs",
                password="not-a-real-password",
                queue_name="clouddsp.source-intake",
            ),
        ):
            with self.subTest(widened=widened.host, queue=widened.queue_name):
                with self.assertRaises(DemucsAMQPConfigurationError):
                    validate_demucs_amqp_settings(widened)


class DemucsAMQPConnectionTests(unittest.TestCase):
    """Prove Pika receives only bounded private connection parameters."""

    @patch("app.amqp_connection._load_pika")
    def test_open_connection_uses_restricted_identity_and_private_endpoint(self, load_pika) -> None:
        """Opening an AMQP socket does not consume or configure broker state."""

        pika = fake_pika()
        connection = MagicMock()
        pika.BlockingConnection.return_value = connection
        load_pika.return_value = pika

        returned = open_demucs_rabbitmq_connection(settings())

        self.assertIs(returned, connection)
        pika.PlainCredentials.assert_called_once_with("clouddsp-demucs", "not-a-real-password")
        parameters = pika.ConnectionParameters.call_args.kwargs
        self.assertEqual(parameters["host"], DEFAULT_DEMUCS_AMQP_HOST)
        self.assertEqual(parameters["port"], 5672)
        self.assertEqual(parameters["virtual_host"], DEMUCS_AMQP_VHOST)
        self.assertEqual(parameters["connection_attempts"], 3)
        self.assertEqual(parameters["heartbeat"], 30)

    @patch("app.amqp_connection._load_pika")
    def test_connection_failure_becomes_a_safe_retryable_category(self, load_pika) -> None:
        """Broker diagnostics cannot escape a later runtime's normal log message."""

        pika = fake_pika()
        pika.BlockingConnection.side_effect = RuntimeError("private broker diagnostic")
        load_pika.return_value = pika

        with self.assertRaises(DemucsAMQPConnectionUnavailable) as raised:
            open_demucs_rabbitmq_connection(settings())

        self.assertEqual(str(raised.exception), "RabbitMQ Demucs connection is unavailable.")
        self.assertNotIn("private broker diagnostic", str(raised.exception))

    @patch("app.amqp_connection._load_pika")
    def test_direct_widened_settings_are_rejected_before_loading_pika(self, load_pika) -> None:
        """A test/entrypoint cannot send restricted credentials to another queue host."""

        with self.assertRaises(DemucsAMQPConfigurationError):
            open_demucs_rabbitmq_connection(
                DemucsAMQPSettings(
                    host="rabbitmq.localhost",
                    port=5672,
                    username="clouddsp-demucs",
                    password="not-a-real-password",
                )
            )
        load_pika.assert_not_called()


if __name__ == "__main__":
    unittest.main()
