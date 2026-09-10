"""Unit tests for the dispatcher RabbitMQ publisher boundary.

The tests replace Pika with fakes, so they neither open a broker connection nor
publish/consume a message. They prove the high-value delivery decisions before
the later runtime combines this AMQP adapter with PostgreSQL outbox leases.
"""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.amqp_publisher import (
    DEFAULT_AMQP_HOST,
    DEMUCS_REQUESTED_ROUTING_KEY,
    DemucsAMQPRequest,
    PROCESSING_EXCHANGE,
    DispatcherBrokerUnavailable,
    DispatcherPublisherConfigurationError,
    DispatcherPublisherConfirmationUnknown,
    DispatcherPublisherContractError,
    DispatcherPublisherNack,
    DispatcherPublisherSettings,
    DispatcherQueueRejected,
    enable_dispatcher_publisher_confirms,
    open_dispatcher_rabbitmq_connection,
    publish_dispatchable_amqp_request,
    publish_demucs_requested,
)
from app.downstream_outbox_request import build_downstream_amqp_request
from app.outbox_lease import DispatcherPublishFailureCode


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


def settings() -> DispatcherPublisherSettings:
    """Return non-secret test-only settings for a fake Pika connection."""

    return DispatcherPublisherSettings(
        host="rabbitmq.test",
        port=5672,
        username="clouddsp-dispatcher",
        password="not-a-real-password",
    )


def payload(**overrides: object) -> dict[str, object]:
    """Return one valid durable version-1 Demucs request body."""

    body: dict[str, object] = {
        "schema_version": 1,
        "job_id": JOB_ID,
        "source": {"bucket": "clouddsp-uploads", "object_key": f"uploads/{JOB_ID}/mix.wav"},
        "stem_mode": "4-stems",
    }
    body.update(overrides)
    return body


class FakeNackError(Exception):
    """Stand in for Pika's definite broker negative-confirmation exception."""


class FakeUnroutableError(Exception):
    """Stand in for Pika's mandatory-return exception."""


def fake_pika(*, properties: object | None = None) -> SimpleNamespace:
    """Build a minimal fake Pika module without importing the real dependency."""

    return SimpleNamespace(
        BasicProperties=MagicMock(return_value=properties if properties is not None else MagicMock()),
        PlainCredentials=MagicMock(),
        ConnectionParameters=MagicMock(),
        BlockingConnection=MagicMock(),
        exceptions=SimpleNamespace(
            NackError=FakeNackError,
            UnroutableError=FakeUnroutableError,
        ),
    )


class DispatcherPublisherSettingsTests(unittest.TestCase):
    """Prove a Pod uses fixed private topology and hides its password."""

    @patch.dict(
        os.environ,
        {
            "RABBITMQ_DISPATCHER_USERNAME": "clouddsp-dispatcher",
            "RABBITMQ_DISPATCHER_PASSWORD": "test-only-password",
        },
        clear=True,
    )
    def test_defaults_to_private_service_dns_and_hides_password(self) -> None:
        """The browser-facing local ingress must never become a Pod endpoint."""

        loaded = DispatcherPublisherSettings.from_environment()

        self.assertEqual(loaded.host, DEFAULT_AMQP_HOST)
        self.assertEqual(loaded.exchange_name, PROCESSING_EXCHANGE)
        self.assertNotIn("test-only-password", repr(loaded))

    @patch.dict(
        os.environ,
        {
            "DISPATCHER_AMQP_HOST": "rabbitmq.localhost",
            "RABBITMQ_DISPATCHER_USERNAME": "clouddsp-dispatcher",
            "RABBITMQ_DISPATCHER_PASSWORD": "test-only-password",
        },
        clear=True,
    )
    def test_rejects_a_browser_facing_broker_host(self) -> None:
        """A dispatcher must use the private AMQP ClusterIP Service."""

        with self.assertRaises(DispatcherPublisherConfigurationError):
            DispatcherPublisherSettings.from_environment()

    @patch.dict(
        os.environ,
        {
            "DISPATCHER_AMQP_EXCHANGE": "arbitrary-exchange",
            "RABBITMQ_DISPATCHER_USERNAME": "clouddsp-dispatcher",
            "RABBITMQ_DISPATCHER_PASSWORD": "test-only-password",
        },
        clear=True,
    )
    def test_rejects_a_configuration_that_widens_the_reviewed_route(self) -> None:
        """Changing a Deployment variable cannot turn this into a general publisher."""

        with self.assertRaises(DispatcherPublisherConfigurationError):
            DispatcherPublisherSettings.from_environment()


class DispatcherPublisherConnectionTests(unittest.TestCase):
    """Prove the adapter builds bounded Pika connection/confirm settings."""

    @patch("app.amqp_publisher._load_pika")
    def test_opens_private_connection_with_restricted_credentials(self, load_pika) -> None:
        """Opening a connection does not publish or configure RabbitMQ topology."""

        pika = fake_pika()
        connection = MagicMock()
        pika.BlockingConnection.return_value = connection
        load_pika.return_value = pika

        returned = open_dispatcher_rabbitmq_connection(settings())

        self.assertIs(returned, connection)
        pika.PlainCredentials.assert_called_once_with("clouddsp-dispatcher", "not-a-real-password")
        parameters = pika.ConnectionParameters.call_args.kwargs
        self.assertEqual(parameters["host"], "rabbitmq.test")
        self.assertEqual(parameters["virtual_host"], "/clouddsp")
        self.assertEqual(parameters["connection_attempts"], 3)
        self.assertEqual(parameters["heartbeat"], 30)

    @patch("app.amqp_publisher._load_pika")
    def test_connection_failure_is_a_known_broker_unavailable_outcome(self, load_pika) -> None:
        """No body was sent when TCP/AMQP connection creation itself fails."""

        pika = fake_pika()
        pika.BlockingConnection.side_effect = RuntimeError("private broker diagnostic")
        load_pika.return_value = pika

        with self.assertRaises(DispatcherBrokerUnavailable) as raised:
            open_dispatcher_rabbitmq_connection(settings())

        self.assertEqual(raised.exception.failure_code, DispatcherPublishFailureCode.BROKER_UNAVAILABLE)
        self.assertNotIn("private broker diagnostic", str(raised.exception))

    def test_enable_confirms_uses_channel_state_without_a_topology_declare(self) -> None:
        """Publisher confirmation is required before outbox state can advance."""

        channel = MagicMock()

        enable_dispatcher_publisher_confirms(channel)

        channel.confirm_delivery.assert_called_once_with()
        channel.exchange_declare.assert_not_called()
        channel.queue_declare.assert_not_called()


class DispatcherPublishTests(unittest.TestCase):
    """Prove durable properties and known-versus-uncertain outcomes."""

    @patch("app.amqp_publisher._load_pika")
    def test_publishes_exact_durable_message_and_required_properties(self, load_pika) -> None:
        """A confirmed publish carries event/job correlation outside the JSON body."""

        properties = MagicMock()
        pika = fake_pika(properties=properties)
        load_pika.return_value = pika
        channel = MagicMock()
        # Pika's confirmation-enabled blocking API may return None on a
        # successful confirmed publish, so only an explicit False is a nack.
        channel.basic_publish.return_value = None

        publish_demucs_requested(channel, event_id=EVENT_ID, job_id=JOB_ID, payload=payload())

        pika.BasicProperties.assert_called_once_with(
            content_type="application/json",
            content_encoding="utf-8",
            delivery_mode=2,
            type="demucs.requested",
            message_id=EVENT_ID,
            correlation_id=JOB_ID,
        )
        channel.basic_publish.assert_called_once()
        published = channel.basic_publish.call_args.kwargs
        self.assertEqual(published["exchange"], PROCESSING_EXCHANGE)
        self.assertEqual(published["routing_key"], DEMUCS_REQUESTED_ROUTING_KEY)
        self.assertIs(published["properties"], properties)
        self.assertTrue(published["mandatory"])
        self.assertEqual(json.loads(published["body"].decode("utf-8")), payload())

    @patch("app.amqp_publisher._load_pika")
    def test_generic_publisher_accepts_a_validated_basic_pitch_request(self, load_pika) -> None:
        """The later generic dispatcher can use the live downstream route safely."""

        pika = fake_pika()
        load_pika.return_value = pika
        channel = MagicMock()
        channel.basic_publish.return_value = None
        request = build_downstream_amqp_request(
            event_id=EVENT_ID,
            job_id=JOB_ID,
            stage="basic-pitch",
            stem_name="bass",
            event_type="basic-pitch.requested",
            payload={
                "schema_version": 1,
                "job_id": JOB_ID,
                "stem_name": "bass",
                "stem": {
                    "bucket": "clouddsp-uploads",
                    "object_key": f"stems/{JOB_ID}/bass.wav",
                    "content_type": "audio/wav",
                    "size_bytes": 1234,
                    "sha256": "a" * 64,
                },
            },
        )

        publish_dispatchable_amqp_request(channel, request=request)

        pika.BasicProperties.assert_called_once_with(
            content_type="application/json",
            content_encoding="utf-8",
            delivery_mode=2,
            type="basic-pitch.requested",
            message_id=EVENT_ID,
            correlation_id=JOB_ID,
        )
        published = channel.basic_publish.call_args.kwargs
        self.assertEqual(published["exchange"], PROCESSING_EXCHANGE)
        self.assertEqual(published["routing_key"], "basic-pitch.requested")
        self.assertTrue(published["mandatory"])

    @patch("app.amqp_publisher._load_pika")
    def test_generic_publisher_rejects_a_forged_request_before_loading_pika(self, load_pika) -> None:
        """The restricted AMQP identity cannot be widened by a caller record."""

        forged_request = DemucsAMQPRequest(
            exchange="unreviewed-exchange",
            routing_key="unreviewed.route",
            body=b"{}",
            content_type="application/json",
            content_encoding="utf-8",
            delivery_mode=2,
            message_type="unreviewed.type",
            message_id=EVENT_ID,
            correlation_id=JOB_ID,
        )

        with self.assertRaises(DispatcherPublisherContractError):
            publish_dispatchable_amqp_request(MagicMock(), request=forged_request)

        load_pika.assert_not_called()

    @patch("app.amqp_publisher._load_pika")
    def test_rejects_invalid_payload_before_constructing_properties_or_publishing(self, load_pika) -> None:
        """An unexpected durable body cannot be sent to a worker queue."""

        channel = MagicMock()

        with self.assertRaises(DispatcherPublisherContractError):
            publish_demucs_requested(
                channel,
                event_id=EVENT_ID,
                job_id=JOB_ID,
                payload=payload(owner_sub="never-publish-this"),
            )

        load_pika.assert_not_called()
        channel.basic_publish.assert_not_called()

    @patch("app.amqp_publisher._load_pika")
    def test_false_confirmation_is_a_known_publisher_nack(self, load_pika) -> None:
        """An explicit false cannot be treated as successful publication."""

        load_pika.return_value = fake_pika()
        channel = MagicMock()
        channel.basic_publish.return_value = False

        with self.assertRaises(DispatcherPublisherNack) as raised:
            publish_demucs_requested(channel, event_id=EVENT_ID, job_id=JOB_ID, payload=payload())

        self.assertEqual(raised.exception.failure_code, DispatcherPublishFailureCode.PUBLISHER_NACK)

    @patch("app.amqp_publisher._load_pika")
    def test_unroutable_mandatory_publish_is_a_known_queue_rejection(self, load_pika) -> None:
        """A broker-returned body is not an uncertain network outcome."""

        load_pika.return_value = fake_pika()
        channel = MagicMock()
        channel.basic_publish.side_effect = FakeUnroutableError("message body omitted")

        with self.assertRaises(DispatcherQueueRejected) as raised:
            publish_demucs_requested(channel, event_id=EVENT_ID, job_id=JOB_ID, payload=payload())

        self.assertEqual(raised.exception.failure_code, DispatcherPublishFailureCode.QUEUE_REJECTED)

    @patch("app.amqp_publisher._load_pika")
    def test_connection_loss_during_publish_is_explicitly_uncertain(self, load_pika) -> None:
        """A runtime must let the DB lease expire instead of eager retrying it."""

        load_pika.return_value = fake_pika()
        channel = MagicMock()
        channel.basic_publish.side_effect = OSError("connection reset after send")

        with self.assertRaises(DispatcherPublisherConfirmationUnknown) as raised:
            publish_demucs_requested(channel, event_id=EVENT_ID, job_id=JOB_ID, payload=payload())

        self.assertNotIn("connection reset", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
