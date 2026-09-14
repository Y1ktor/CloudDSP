"""Unit tests for ADTOF channel flow control and passive queue setup.

The Pika-shaped channel is mocked. Tests make no broker connection or topology
mutation and perform no delivery/acknowledgement/database/storage/model/Docker/
Kubernetes action.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.amqp_channel import ADTOFAMQPChannelUnavailable, configure_adtof_rabbitmq_channel
from app.amqp_connection import (
    ADTOF_REQUEST_QUEUE,
    DEFAULT_ADTOF_AMQP_HOST,
    ADTOFAMQPConfigurationError,
    ADTOFAMQPSettings,
)


def settings() -> ADTOFAMQPSettings:
    """Return valid synthetic connection/topology settings for channel tests."""

    return ADTOFAMQPSettings(
        host=DEFAULT_ADTOF_AMQP_HOST,
        port=5672,
        username="clouddsp-adtof",
        password="not-a-real-password",
    )


class ADTOFAMQPChannelTests(unittest.TestCase):
    """Prove one worker channel remains bounded and topology-read-only."""

    def test_sets_prefetch_one_and_passively_checks_only_the_reviewed_queue(self) -> None:
        """A CPU worker cannot reserve a second drum request while processing one."""

        channel = MagicMock()

        configure_adtof_rabbitmq_channel(channel, settings=settings())

        channel.basic_qos.assert_called_once_with(prefetch_count=1)
        channel.queue_declare.assert_called_once_with(
            queue=ADTOF_REQUEST_QUEUE,
            passive=True,
        )
        channel.exchange_declare.assert_not_called()
        channel.queue_bind.assert_not_called()
        channel.basic_get.assert_not_called()
        channel.basic_ack.assert_not_called()

    def test_qos_failure_becomes_safe_channel_unavailable_without_queue_lookup(self) -> None:
        """The worker cannot continue if prefetch configuration fails."""

        channel = MagicMock()
        channel.basic_qos.side_effect = RuntimeError("private broker diagnostic")

        with self.assertRaises(ADTOFAMQPChannelUnavailable) as raised:
            configure_adtof_rabbitmq_channel(channel, settings=settings())

        self.assertEqual(str(raised.exception), "RabbitMQ ADTOF channel is unavailable.")
        channel.queue_declare.assert_not_called()

    def test_passive_queue_failure_never_falls_back_to_active_declaration(self) -> None:
        """A restricted consumer reports unavailable topology rather than repairing it."""

        channel = MagicMock()
        channel.queue_declare.side_effect = RuntimeError("private broker diagnostic")

        with self.assertRaises(ADTOFAMQPChannelUnavailable):
            configure_adtof_rabbitmq_channel(channel, settings=settings())

        channel.queue_declare.assert_called_once_with(
            queue=ADTOF_REQUEST_QUEUE,
            passive=True,
        )
        channel.exchange_declare.assert_not_called()
        channel.queue_bind.assert_not_called()

    def test_direct_settings_for_another_queue_are_rejected_before_channel_io(self) -> None:
        """A direct dataclass cannot widen this helper into a foreign queue reader."""

        channel = MagicMock()
        widened = ADTOFAMQPSettings(
            host=DEFAULT_ADTOF_AMQP_HOST,
            port=5672,
            username="clouddsp-adtof",
            password="not-a-real-password",
            queue_name="clouddsp.basic-pitch.requests",
        )

        # ADTOF reuses its complete endpoint/topology/identity validator here,
        # so the forged queue is rejected as a configuration-contract error
        # before this helper can issue either channel method.
        with self.assertRaises(ADTOFAMQPConfigurationError):
            configure_adtof_rabbitmq_channel(channel, settings=widened)

        channel.basic_qos.assert_not_called()
        channel.queue_declare.assert_not_called()

    def test_malformed_channel_is_rejected_before_any_broker_method(self) -> None:
        """The future consumer cannot treat an arbitrary object as a Pika channel."""

        with self.assertRaisesRegex(TypeError, "channel must provide basic_qos and queue_declare"):
            configure_adtof_rabbitmq_channel(object(), settings=settings())


if __name__ == "__main__":
    unittest.main()
