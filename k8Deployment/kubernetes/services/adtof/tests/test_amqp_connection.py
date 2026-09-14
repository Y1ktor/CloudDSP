"""Unit tests for ADTOF's private RabbitMQ connection-settings contract.

Each credential here is synthetic. Tests import no Pika and open no socket,
channel, queue, database, MinIO client, ADTOF model, or Kubernetes resource.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.amqp_connection import (
    ADTOF_AMQP_VHOST,
    ADTOF_PREFETCH_COUNT,
    ADTOF_REQUEST_QUEUE,
    DEFAULT_ADTOF_AMQP_HOST,
    ADTOFAMQPConfigurationError,
    ADTOFAMQPConnectionUnavailable,
    ADTOFAMQPSettings,
    open_adtof_rabbitmq_connection,
    validate_adtof_amqp_settings,
)


VALID_ENVIRONMENT = {
    "RABBITMQ_ADTOF_USERNAME": "clouddsp-adtof",
    # Test-only text, never copied from the ignored Kubernetes Secret.
    "RABBITMQ_ADTOF_PASSWORD": "unit-test-adtof-rabbitmq-password",
}


def settings() -> ADTOFAMQPSettings:
    """Return synthetic direct settings for validation-focused test cases."""

    return ADTOFAMQPSettings(
        host=DEFAULT_ADTOF_AMQP_HOST,
        port=5672,
        username="clouddsp-adtof",
        password="not-a-real-password",
    )


def fake_pika() -> SimpleNamespace:
    """Return only the connection-only Pika surface this module requires."""

    return SimpleNamespace(
        PlainCredentials=MagicMock(),
        ConnectionParameters=MagicMock(),
        BlockingConnection=MagicMock(),
    )


class ADTOFAMQPSettingsTests(unittest.TestCase):
    """Prove a future ADTOF Pod cannot widen its private broker authority."""

    def settings_from(self, overrides: dict[str, str] | None = None) -> ADTOFAMQPSettings:
        """Load one isolated synthetic environment for a focused assertion."""

        environment = dict(VALID_ENVIRONMENT)
        if overrides:
            environment.update(overrides)
        with patch.dict(os.environ, environment, clear=True):
            return ADTOFAMQPSettings.from_environment()

    def test_defaults_to_private_plain_amqp_queue_prefetch_one_and_hidden_password(self) -> None:
        """The future worker gets no browser, TLS, host-port, or admin route."""

        loaded = self.settings_from()

        self.assertEqual(loaded.host, DEFAULT_ADTOF_AMQP_HOST)
        self.assertEqual(loaded.virtual_host, ADTOF_AMQP_VHOST)
        self.assertEqual(loaded.queue_name, ADTOF_REQUEST_QUEUE)
        self.assertEqual(loaded.prefetch_count, ADTOF_PREFETCH_COUNT)
        self.assertNotIn(VALID_ENVIRONMENT["RABBITMQ_ADTOF_PASSWORD"], repr(loaded))

    def test_missing_password_fails_without_an_amqp_client_operation(self) -> None:
        """Malformed Secret input cannot proceed to a future socket factory."""

        environment = dict(VALID_ENVIRONMENT)
        environment.pop("RABBITMQ_ADTOF_PASSWORD")
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ADTOFAMQPConfigurationError) as raised:
                ADTOFAMQPSettings.from_environment()

        self.assertEqual(
            str(raised.exception),
            "Required environment variable RABBITMQ_ADTOF_PASSWORD is absent.",
        )

    def test_rejects_endpoint_topology_identity_or_prefetch_widening(self) -> None:
        """Environment settings cannot redirect one restricted consumer elsewhere."""

        for overrides in (
            {"ADTOF_AMQP_HOST": "rabbitmq.localhost"},
            {"ADTOF_AMQP_PORT": "15672"},
            {"ADTOF_AMQP_VHOST": "/"},
            {"ADTOF_AMQP_QUEUE": "clouddsp.basic-pitch.requests"},
            {"ADTOF_AMQP_PREFETCH_COUNT": "2"},
            {"RABBITMQ_ADTOF_USERNAME": "clouddsp-admin"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ADTOFAMQPConfigurationError):
                    self.settings_from(overrides)

    def test_rejects_invalid_bounded_connection_timings(self) -> None:
        """A future factory cannot inherit an unbounded timing configuration."""

        for overrides in (
            {"ADTOF_AMQP_CONNECT_TIMEOUT_SECONDS": "0"},
            {"ADTOF_AMQP_CONNECT_TIMEOUT_SECONDS": "not-a-number"},
            {"ADTOF_AMQP_HEARTBEAT_SECONDS": "301"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ADTOFAMQPConfigurationError):
                    self.settings_from(overrides)

    def test_direct_construction_cannot_bypass_fixed_contract(self) -> None:
        """Later adapters must validate hand-built dataclasses before broker I/O."""

        for widened in (
            ADTOFAMQPSettings(
                host=DEFAULT_ADTOF_AMQP_HOST,
                port=15672,
                username="clouddsp-adtof",
                password="not-a-real-password",
            ),
            ADTOFAMQPSettings(
                host=DEFAULT_ADTOF_AMQP_HOST,
                port=5672,
                username="clouddsp-adtof",
                password="not-a-real-password",
                prefetch_count=2,
            ),
        ):
            with self.subTest(widened=widened):
                with self.assertRaises(ADTOFAMQPConfigurationError):
                    validate_adtof_amqp_settings(widened)


class ADTOFAMQPConnectionTests(unittest.TestCase):
    """Prove Pika receives one bounded private connection configuration only."""

    @patch("app.amqp_connection._load_pika")
    def test_open_connection_uses_restricted_identity_without_tls_options(self, load_pika) -> None:
        """Socket creation alone cannot consume, configure, or acknowledge work."""

        pika = fake_pika()
        connection = MagicMock()
        pika.BlockingConnection.return_value = connection
        load_pika.return_value = pika

        returned = open_adtof_rabbitmq_connection(settings())

        self.assertIs(returned, connection)
        pika.PlainCredentials.assert_called_once_with("clouddsp-adtof", "not-a-real-password")
        parameters = pika.ConnectionParameters.call_args.kwargs
        self.assertEqual(parameters["host"], DEFAULT_ADTOF_AMQP_HOST)
        self.assertEqual(parameters["port"], 5672)
        self.assertEqual(parameters["virtual_host"], ADTOF_AMQP_VHOST)
        self.assertEqual(parameters["connection_attempts"], 3)
        self.assertEqual(parameters["heartbeat"], 30)
        self.assertNotIn("ssl_options", parameters)

    @patch("app.amqp_connection._load_pika")
    def test_direct_settings_cannot_widen_before_pika_import(self, load_pika) -> None:
        """A hand-built dataclass cannot redirect the credential to management."""

        widened = ADTOFAMQPSettings(
            host=DEFAULT_ADTOF_AMQP_HOST,
            port=15672,
            username="clouddsp-adtof",
            password="not-a-real-password",
        )

        with self.assertRaises(ADTOFAMQPConfigurationError):
            open_adtof_rabbitmq_connection(widened)

        load_pika.assert_not_called()

    @patch("app.amqp_connection._load_pika")
    def test_connection_failure_becomes_safe_retryable_category(self, load_pika) -> None:
        """Raw Pika/broker diagnostics cannot enter future worker logs."""

        pika = fake_pika()
        pika.BlockingConnection.side_effect = RuntimeError("private broker diagnostic")
        load_pika.return_value = pika

        with self.assertRaises(ADTOFAMQPConnectionUnavailable) as raised:
            open_adtof_rabbitmq_connection(settings())

        self.assertEqual(str(raised.exception), "RabbitMQ ADTOF connection is unavailable.")
        self.assertNotIn("private broker diagnostic", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
