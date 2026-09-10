"""Unit tests for Demucs Pika-channel flow-control and passive-queue setup.

The Pika-shaped channel is a mock. These tests connect to no broker, declare no
RabbitMQ topology, receive/acknowledge no delivery, and do not touch database,
storage, media, Docker, or Kubernetes state.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.amqp_channel import DemucsAMQPChannelUnavailable, configure_demucs_rabbitmq_channel
from app.amqp_connection import DEFAULT_DEMUCS_AMQP_HOST, DemucsAMQPSettings
from app.amqp_manual_ack import DEMUCS_REQUEST_QUEUE


def settings() -> DemucsAMQPSettings:
    """Return an explicit valid test-only connection/topology configuration."""

    return DemucsAMQPSettings(
        host=DEFAULT_DEMUCS_AMQP_HOST,
        port=5672,
        username="clouddsp-demucs",
        password="not-a-real-password",
    )


class DemucsAMQPChannelTests(unittest.TestCase):
    """Prove the worker channel is bounded and cannot create request topology."""

    def test_sets_prefetch_one_and_passively_checks_only_the_reviewed_queue(self) -> None:
        """One CPU/GPU worker should hold only one unacknowledged delivery."""

        channel = MagicMock()

        configure_demucs_rabbitmq_channel(channel, settings=settings())

        channel.basic_qos.assert_called_once_with(prefetch_count=1)
        channel.queue_declare.assert_called_once_with(
            queue=DEMUCS_REQUEST_QUEUE,
            passive=True,
        )
        channel.exchange_declare.assert_not_called()
        channel.queue_bind.assert_not_called()
        channel.basic_get.assert_not_called()
        channel.basic_ack.assert_not_called()

    def test_qos_failure_becomes_a_safe_channel_unavailable_category(self) -> None:
        """No passive queue lookup follows a failed flow-control setup."""

        channel = MagicMock()
        channel.basic_qos.side_effect = RuntimeError("private broker diagnostic")

        with self.assertRaises(DemucsAMQPChannelUnavailable) as raised:
            configure_demucs_rabbitmq_channel(channel, settings=settings())

        self.assertEqual(str(raised.exception), "RabbitMQ Demucs channel is unavailable.")
        channel.queue_declare.assert_not_called()

    def test_passive_queue_failure_is_not_replaced_with_an_active_declare(self) -> None:
        """A restricted consumer must report missing/inaccessible topology, not repair it."""

        channel = MagicMock()
        channel.queue_declare.side_effect = RuntimeError("private broker diagnostic")

        with self.assertRaises(DemucsAMQPChannelUnavailable):
            configure_demucs_rabbitmq_channel(channel, settings=settings())

        channel.queue_declare.assert_called_once_with(
            queue=DEMUCS_REQUEST_QUEUE,
            passive=True,
        )
        channel.queue_bind.assert_not_called()

    def test_rejects_directly_constructed_settings_for_another_queue_before_channel_io(self) -> None:
        """The channel layer repeats the fixed-queue guard for defence in depth."""

        channel = MagicMock()
        widened_settings = DemucsAMQPSettings(
            host=DEFAULT_DEMUCS_AMQP_HOST,
            port=5672,
            username="clouddsp-demucs",
            password="not-a-real-password",
            queue_name="clouddsp.source-intake",
        )

        with self.assertRaises(ValueError):
            configure_demucs_rabbitmq_channel(channel, settings=widened_settings)

        channel.basic_qos.assert_not_called()
        channel.queue_declare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
