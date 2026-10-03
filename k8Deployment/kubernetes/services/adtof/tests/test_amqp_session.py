"""Unit tests for ADTOF's closeable AMQP connection/channel session context.

All broker boundaries are mocked. Tests open no socket, consume/acknowledge no
delivery, declare no topology, and make no database/MinIO/model/Kubernetes call.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.messaging.amqp_channel import ADTOFAMQPChannelUnavailable
from app.messaging.amqp_connection import DEFAULT_ADTOF_AMQP_HOST, ADTOFAMQPConnectionUnavailable, ADTOFAMQPSettings
from app.messaging.amqp_session import opened_adtof_rabbitmq_session


def settings() -> ADTOFAMQPSettings:
    """Return synthetic settings that satisfy the fixed private AMQP contract."""

    return ADTOFAMQPSettings(
        host=DEFAULT_ADTOF_AMQP_HOST,
        port=5672,
        username="clouddsp-adtof",
        password="not-a-real-password",
    )


class RecordingResource:
    """Pika-shaped resource that records nesting-order cleanup without I/O."""

    def __init__(self, name: str, events: list[str], *, close_error: BaseException | None = None) -> None:
        self.name = name
        self.events = events
        self.close_error = close_error
        self.is_open = True

    def close(self) -> None:
        """Record one close and optionally model a private client cleanup failure."""

        self.events.append(f"close-{self.name}")
        self.is_open = False
        if self.close_error is not None:
            raise self.close_error


class ADTOFAMQPSessionTests(unittest.TestCase):
    """Prove prepared channels are yielded and both Pika resources are closed."""

    @patch("app.messaging.amqp_session.configure_adtof_rabbitmq_channel")
    @patch("app.messaging.amqp_session.open_adtof_rabbitmq_connection")
    def test_prepares_channel_then_closes_channel_before_connection(self, open_connection, configure) -> None:
        """The loop sees only a prepared channel, not an unconfigured socket."""

        events: list[str] = []
        channel = RecordingResource("channel", events)
        connection = RecordingResource("connection", events)
        connection.channel = MagicMock(return_value=channel)  # type: ignore[attr-defined]
        open_connection.return_value = connection

        with opened_adtof_rabbitmq_session(settings=settings()) as yielded:
            self.assertIs(yielded, channel)
            self.assertEqual(events, [])

        open_connection.assert_called_once_with(settings())
        configure.assert_called_once_with(channel, settings=settings())
        self.assertEqual(events, ["close-channel", "close-connection"])

    @patch("app.messaging.amqp_session.configure_adtof_rabbitmq_channel")
    @patch("app.messaging.amqp_session.open_adtof_rabbitmq_connection")
    def test_setup_or_body_failure_closes_resources_without_hiding_original_error(self, open_connection, configure) -> None:
        """Topology and worker failures cannot leak a socket or be relabeled by cleanup."""

        for failure_source in ("configure", "body"):
            with self.subTest(failure_source=failure_source):
                events: list[str] = []
                channel = RecordingResource("channel", events)
                connection = RecordingResource("connection", events)
                connection.channel = MagicMock(return_value=channel)  # type: ignore[attr-defined]
                open_connection.return_value = connection
                configure.reset_mock(return_value=True, side_effect=True)
                configure.side_effect = (
                    ADTOFAMQPChannelUnavailable("RabbitMQ ADTOF channel is unavailable.")
                    if failure_source == "configure"
                    else None
                )

                if failure_source == "configure":
                    with self.assertRaises(ADTOFAMQPChannelUnavailable):
                        with opened_adtof_rabbitmq_session(settings=settings()):
                            self.fail("setup failure must not yield a channel")
                else:
                    body_error = RuntimeError("private worker error")
                    with self.assertRaises(RuntimeError) as raised:
                        with opened_adtof_rabbitmq_session(settings=settings()):
                            raise body_error
                    self.assertIs(raised.exception, body_error)

                self.assertEqual(events, ["close-channel", "close-connection"])

    @patch("app.messaging.amqp_session.configure_adtof_rabbitmq_channel")
    @patch("app.messaging.amqp_session.open_adtof_rabbitmq_connection")
    def test_cleanup_failure_after_normal_exit_is_redacted_and_still_closes_connection(self, open_connection, configure) -> None:
        """A broken channel close cannot leak the connection or expose driver text."""

        events: list[str] = []
        channel = RecordingResource("channel", events, close_error=RuntimeError("private close diagnostic"))
        connection = RecordingResource("connection", events)
        connection.channel = MagicMock(return_value=channel)  # type: ignore[attr-defined]
        open_connection.return_value = connection

        with self.assertRaises(ADTOFAMQPChannelUnavailable) as raised:
            with opened_adtof_rabbitmq_session(settings=settings()):
                pass

        self.assertEqual(str(raised.exception), "RabbitMQ ADTOF channel is unavailable.")
        self.assertNotIn("private close diagnostic", str(raised.exception))
        self.assertEqual(events, ["close-channel", "close-connection"])

    @patch("app.messaging.amqp_session.configure_adtof_rabbitmq_channel")
    @patch("app.messaging.amqp_session.open_adtof_rabbitmq_connection")
    def test_connection_failure_does_not_invent_channel_or_cleanup_work(self, open_connection, configure) -> None:
        """The reviewed factory error remains the outer retryable failure category."""

        open_connection.side_effect = ADTOFAMQPConnectionUnavailable(
            "RabbitMQ ADTOF connection is unavailable."
        )

        with self.assertRaises(ADTOFAMQPConnectionUnavailable):
            with opened_adtof_rabbitmq_session(settings=settings()):
                self.fail("connection failure must not yield a channel")

        configure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
