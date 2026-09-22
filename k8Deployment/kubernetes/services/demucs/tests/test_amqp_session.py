"""Unit tests for Demucs's closeable AMQP connection/channel session context.

Broker boundaries are mocked. Tests open no socket, consume/acknowledge no
delivery, declare no topology, and make no database/MinIO/model/Kubernetes call.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.amqp_channel import DemucsAMQPChannelUnavailable
from app.amqp_connection import DEFAULT_DEMUCS_AMQP_HOST, DemucsAMQPConnectionUnavailable, DemucsAMQPSettings
from app.amqp_session import opened_demucs_rabbitmq_session


def settings() -> DemucsAMQPSettings:
    """Return synthetic settings satisfying the fixed private AMQP contract."""

    return DemucsAMQPSettings(
        host=DEFAULT_DEMUCS_AMQP_HOST,
        port=5672,
        username="clouddsp-demucs",
        password="not-a-real-password",
    )


class RecordingResource:
    """Pika-shaped resource recording nesting-order cleanup without I/O."""

    def __init__(self, name: str, events: list[str], *, close_error: BaseException | None = None) -> None:
        self.name = name
        self.events = events
        self.close_error = close_error
        self.is_open = True

    def close(self) -> None:
        """Record one close and optionally model a private client cleanup error."""

        self.events.append(f"close-{self.name}")
        self.is_open = False
        if self.close_error is not None:
            raise self.close_error


class DemucsAMQPSessionTests(unittest.TestCase):
    """Prove prepared channels are yielded and both Pika resources close."""

    @patch("app.amqp_session.configure_demucs_rabbitmq_channel")
    @patch("app.amqp_session.open_demucs_rabbitmq_connection")
    def test_prepares_channel_then_closes_channel_before_connection(self, open_connection, configure) -> None:
        """The loop sees a prepared channel, never an unconfigured socket."""

        events: list[str] = []
        channel = RecordingResource("channel", events)
        connection = RecordingResource("connection", events)
        connection.channel = MagicMock(return_value=channel)  # type: ignore[attr-defined]
        open_connection.return_value = connection

        with opened_demucs_rabbitmq_session(settings=settings()) as yielded:
            self.assertIs(yielded, channel)
            self.assertEqual(events, [])

        open_connection.assert_called_once_with(settings())
        configure.assert_called_once_with(channel, settings=settings())
        self.assertEqual(events, ["close-channel", "close-connection"])

    @patch("app.amqp_session.configure_demucs_rabbitmq_channel")
    @patch("app.amqp_session.open_demucs_rabbitmq_connection")
    def test_setup_or_body_failure_closes_resources_without_hiding_original_error(self, open_connection, configure) -> None:
        """Worker/setup errors cannot leak a socket or be relabeled by cleanup."""

        for failure_source in ("configure", "body"):
            with self.subTest(failure_source=failure_source):
                events: list[str] = []
                channel = RecordingResource("channel", events)
                connection = RecordingResource("connection", events)
                connection.channel = MagicMock(return_value=channel)  # type: ignore[attr-defined]
                open_connection.return_value = connection
                configure.reset_mock(return_value=True, side_effect=True)
                configure.side_effect = (
                    DemucsAMQPChannelUnavailable("RabbitMQ Demucs channel is unavailable.")
                    if failure_source == "configure"
                    else None
                )

                if failure_source == "configure":
                    with self.assertRaises(DemucsAMQPChannelUnavailable):
                        with opened_demucs_rabbitmq_session(settings=settings()):
                            self.fail("setup failure must not yield a channel")
                else:
                    body_error = RuntimeError("test-only worker error")
                    with self.assertRaises(RuntimeError) as raised:
                        with opened_demucs_rabbitmq_session(settings=settings()):
                            raise body_error
                    self.assertIs(raised.exception, body_error)

                self.assertEqual(events, ["close-channel", "close-connection"])

    @patch("app.amqp_session.configure_demucs_rabbitmq_channel")
    @patch("app.amqp_session.open_demucs_rabbitmq_connection")
    def test_normal_cleanup_failure_is_redacted_and_still_closes_connection(self, open_connection, configure) -> None:
        """Broken channel close cannot leak connection or expose driver detail."""

        events: list[str] = []
        channel = RecordingResource("channel", events, close_error=RuntimeError("test-only close diagnostic"))
        connection = RecordingResource("connection", events)
        connection.channel = MagicMock(return_value=channel)  # type: ignore[attr-defined]
        open_connection.return_value = connection

        with self.assertRaises(DemucsAMQPChannelUnavailable) as raised:
            with opened_demucs_rabbitmq_session(settings=settings()):
                pass

        self.assertEqual(str(raised.exception), "RabbitMQ Demucs channel is unavailable.")
        self.assertNotIn("test-only close diagnostic", str(raised.exception))
        self.assertEqual(events, ["close-channel", "close-connection"])

    @patch("app.amqp_session.configure_demucs_rabbitmq_channel")
    @patch("app.amqp_session.open_demucs_rabbitmq_connection")
    def test_connection_failure_does_not_invent_channel_or_cleanup_work(self, open_connection, configure) -> None:
        """Factory's reviewed retryable failure reaches the future supervisor."""

        open_connection.side_effect = DemucsAMQPConnectionUnavailable(
            "RabbitMQ Demucs connection is unavailable."
        )

        with self.assertRaises(DemucsAMQPConnectionUnavailable):
            with opened_demucs_rabbitmq_session(settings=settings()):
                self.fail("connection failure must not yield a channel")

        configure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
