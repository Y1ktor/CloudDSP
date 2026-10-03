"""Unit tests for Basic Pitch channel flow-control and passive queue setup.

The Pika-shaped channel is a mock. No broker connection, topology mutation,
delivery, acknowledgement, database/storage access, model run, Docker action,
or Kubernetes resource is involved in these tests.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.messaging.amqp_channel import BasicPitchAMQPChannelUnavailable, configure_basic_pitch_rabbitmq_channel
from app.messaging.amqp_connection import (
    BASIC_PITCH_REQUEST_QUEUE,
    DEFAULT_BASIC_PITCH_AMQP_HOST,
    BasicPitchAMQPSettings,
)


def settings() -> BasicPitchAMQPSettings:
    """Return valid test-only connection/topology settings for mocked channel use."""

    return BasicPitchAMQPSettings(
        host=DEFAULT_BASIC_PITCH_AMQP_HOST,
        port=5672,
        username="clouddsp-basic-pitch",
        password="not-a-real-password",
    )


class BasicPitchAMQPChannelTests(unittest.TestCase):
    """Prove one worker channel remains bounded and topology-read-only."""

    def test_sets_prefetch_one_and_passively_checks_only_the_reviewed_queue(self) -> None:
        """A CPU worker receives no second unacknowledged stem while processing one."""

        channel = MagicMock()

        configure_basic_pitch_rabbitmq_channel(channel, settings=settings())

        channel.basic_qos.assert_called_once_with(prefetch_count=1)
        channel.queue_declare.assert_called_once_with(
            queue=BASIC_PITCH_REQUEST_QUEUE,
            passive=True,
        )
        channel.exchange_declare.assert_not_called()
        channel.queue_bind.assert_not_called()
        channel.basic_get.assert_not_called()
        channel.basic_ack.assert_not_called()

    def test_qos_failure_becomes_safe_channel_unavailable_without_queue_lookup(self) -> None:
        """The worker cannot continue after flow-control configuration fails."""

        channel = MagicMock()
        channel.basic_qos.side_effect = RuntimeError("private broker diagnostic")

        with self.assertRaises(BasicPitchAMQPChannelUnavailable) as raised:
            configure_basic_pitch_rabbitmq_channel(channel, settings=settings())

        self.assertEqual(str(raised.exception), "RabbitMQ Basic Pitch channel is unavailable.")
        channel.queue_declare.assert_not_called()

    def test_passive_queue_failure_never_falls_back_to_active_declaration(self) -> None:
        """A restricted consumer reports unavailable topology rather than repairing it."""

        channel = MagicMock()
        channel.queue_declare.side_effect = RuntimeError("private broker diagnostic")

        with self.assertRaises(BasicPitchAMQPChannelUnavailable):
            configure_basic_pitch_rabbitmq_channel(channel, settings=settings())

        channel.queue_declare.assert_called_once_with(
            queue=BASIC_PITCH_REQUEST_QUEUE,
            passive=True,
        )
        channel.exchange_declare.assert_not_called()
        channel.queue_bind.assert_not_called()

    def test_direct_settings_for_another_queue_are_rejected_before_channel_io(self) -> None:
        """A direct dataclass cannot widen this helper into a foreign queue reader."""

        channel = MagicMock()
        widened_settings = BasicPitchAMQPSettings(
            host=DEFAULT_BASIC_PITCH_AMQP_HOST,
            port=5672,
            username="clouddsp-basic-pitch",
            password="not-a-real-password",
            queue_name="clouddsp.source-intake",
        )

        with self.assertRaises(ValueError):
            configure_basic_pitch_rabbitmq_channel(channel, settings=widened_settings)

        channel.basic_qos.assert_not_called()
        channel.queue_declare.assert_not_called()

    def test_malformed_channel_is_rejected_before_any_broker_method(self) -> None:
        """The later consumer cannot treat an arbitrary object as a Pika channel."""

        with self.assertRaisesRegex(TypeError, "channel must provide basic_qos and queue_declare"):
            configure_basic_pitch_rabbitmq_channel(object(), settings=settings())


if __name__ == "__main__":
    unittest.main()
