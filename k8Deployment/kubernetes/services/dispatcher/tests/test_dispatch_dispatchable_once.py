"""Tests for one generic durable outbox dispatch attempt.

Every database cursor, RabbitMQ connection, selector, and publisher in these
tests is a fake.  They prove the ordered state machine without connecting to
PostgreSQL/RabbitMQ, publishing a message, sleeping, or running Kubernetes.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import unittest
from unittest.mock import MagicMock, patch

from app.amqp_publisher import (
    DispatcherBrokerUnavailable,
    DispatcherPublisherConfirmationUnknown,
    DispatcherPublisherSettings,
)
from app.dispatch_dispatchable_once import dispatch_dispatchable_once
from app.dispatch_once import DEFAULT_DISPATCH_LEASE_SECONDS, DispatchOnceOutcome
from app.dispatchable_outbox_request import DispatchableOutboxRequestContractError
from app.outbox_lease import DispatcherDeadLetterCode, LeasedOutboxEvent


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
LEASE_TOKEN = "0a2e7458-c355-4933-ac0b-5788eecc504d"


def downstream_event() -> LeasedOutboxEvent:
    """Return one already-claimed Basic Pitch event without a database query."""

    return LeasedOutboxEvent(
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
        delivery_attempts=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
    )


def publisher_settings() -> DispatcherPublisherSettings:
    """Return non-secret fake settings for an isolated composition test."""

    return DispatcherPublisherSettings(
        host="rabbitmq.test",
        port=5672,
        username="clouddsp-dispatcher",
        password="not-a-real-password",
    )


class FakeDatabase:
    """Record the short database scopes used by a one-attempt composition."""

    def __init__(self) -> None:
        self.cursors: list[MagicMock] = []

    @contextmanager
    def write_cursor(self):
        """Yield one fresh fake cursor per committed/rolled-back operation."""

        cursor = MagicMock()
        self.cursors.append(cursor)
        yield cursor


class DispatchDispatchableOnceTests(unittest.TestCase):
    """Prove generic claim → route → confirm → guarded-state ordering."""

    def setUp(self) -> None:
        self.database = FakeDatabase()
        self.connection = MagicMock()
        self.channel = MagicMock()
        self.connection.channel.return_value = self.channel

    def _run(self, **kwargs: object):
        """Use a fake broker connection unless a case injects a failure."""

        return dispatch_dispatchable_once(
            database=self.database,
            publisher_settings=publisher_settings(),
            connection_factory=kwargs.pop("connection_factory", MagicMock(return_value=self.connection)),
            **kwargs,
        )

    @patch("app.dispatch_dispatchable_once.claim_due_dispatchable_outbox_event", return_value=None)
    @patch("app.dispatch_dispatchable_once.open_dispatcher_rabbitmq_connection")
    def test_idle_outbox_opens_no_broker_connection(self, _unused_default_connection, claim) -> None:
        """No due row means no route selection, connection, or artificial work."""

        result = self._run()

        self.assertEqual(result.outcome, DispatchOnceOutcome.IDLE)
        self.assertEqual(len(self.database.cursors), 1)
        claim.assert_called_once_with(
            self.database.cursors[0],
            lease_seconds=DEFAULT_DISPATCH_LEASE_SECONDS,
        )
        self.connection.channel.assert_not_called()

    @patch("app.dispatch_dispatchable_once.mark_outbox_event_published", return_value=True)
    @patch("app.dispatch_dispatchable_once.publish_dispatchable_amqp_request")
    @patch("app.dispatch_dispatchable_once.enable_dispatcher_publisher_confirms")
    @patch("app.dispatch_dispatchable_once.build_dispatchable_amqp_request")
    @patch("app.dispatch_dispatchable_once.claim_due_dispatchable_outbox_event", return_value=downstream_event())
    def test_confirmed_downstream_publish_marks_matching_generic_lease(
        self,
        claim,
        select_request,
        enable_confirms,
        publish,
        mark_published,
    ) -> None:
        """The generic path uses no Demucs-specific claim or completion helper."""

        request = MagicMock()
        select_request.return_value = request

        result = self._run()

        self.assertEqual(result.outcome, DispatchOnceOutcome.PUBLISHED)
        self.assertEqual(len(self.database.cursors), 2)
        select_request.assert_called_once_with(downstream_event())
        enable_confirms.assert_called_once_with(self.channel)
        publish.assert_called_once_with(self.channel, request=request)
        mark_published.assert_called_once_with(
            self.database.cursors[1],
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
        )
        claim.assert_called_once()

    @patch("app.dispatch_dispatchable_once.mark_outbox_event_dead_lettered", return_value=True)
    @patch("app.dispatch_dispatchable_once.open_dispatcher_rabbitmq_connection")
    @patch(
        "app.dispatch_dispatchable_once.build_dispatchable_amqp_request",
        side_effect=DispatchableOutboxRequestContractError("safe"),
    )
    @patch("app.dispatch_dispatchable_once.claim_due_dispatchable_outbox_event", return_value=downstream_event())
    def test_invalid_downstream_record_is_terminal_before_a_broker_connection(
        self,
        _claim,
        _select_request,
        broker_connection,
        mark_dead_lettered,
    ) -> None:
        """A bad durable stem payload never reaches a queue or retry loop."""

        result = self._run()

        self.assertEqual(result.outcome, DispatchOnceOutcome.EVENT_DEAD_LETTERED)
        self.assertEqual(len(self.database.cursors), 2)
        broker_connection.assert_not_called()
        mark_dead_lettered.assert_called_once_with(
            self.database.cursors[1],
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
            reason=DispatcherDeadLetterCode.INVALID_EVENT_CONTRACT,
        )

    @patch("app.dispatch_dispatchable_once.schedule_outbox_event_retry", return_value=True)
    @patch("app.dispatch_dispatchable_once.build_dispatchable_amqp_request", return_value=MagicMock())
    @patch("app.dispatch_dispatchable_once.claim_due_dispatchable_outbox_event", return_value=downstream_event())
    def test_known_pre_publish_failure_schedules_generic_retry(self, _claim, _select_request, schedule_retry) -> None:
        """An unavailable broker is known-safe to retry for a downstream row."""

        result = self._run(connection_factory=MagicMock(side_effect=DispatcherBrokerUnavailable()))

        self.assertEqual(result.outcome, DispatchOnceOutcome.RETRY_SCHEDULED)
        self.assertEqual(len(self.database.cursors), 2)
        schedule_retry.assert_called_once_with(
            self.database.cursors[1],
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
            retry_after_seconds=30,
            failure_code=DispatcherBrokerUnavailable().failure_code,
        )

    @patch("app.dispatch_dispatchable_once.schedule_outbox_event_retry")
    @patch("app.dispatch_dispatchable_once.mark_outbox_event_published")
    @patch(
        "app.dispatch_dispatchable_once.publish_dispatchable_amqp_request",
        side_effect=DispatcherPublisherConfirmationUnknown("safe"),
    )
    @patch("app.dispatch_dispatchable_once.enable_dispatcher_publisher_confirms")
    @patch("app.dispatch_dispatchable_once.build_dispatchable_amqp_request", return_value=MagicMock())
    @patch("app.dispatch_dispatchable_once.claim_due_dispatchable_outbox_event", return_value=downstream_event())
    def test_uncertain_confirmation_leaves_downstream_lease_unchanged(
        self,
        _claim,
        _select_request,
        _enable_confirms,
        _publish,
        mark_published,
        schedule_retry,
    ) -> None:
        """The generic path preserves duplicate-safe lease-expiry recovery."""

        result = self._run()

        self.assertEqual(result.outcome, DispatchOnceOutcome.CONFIRMATION_UNKNOWN)
        self.assertEqual(len(self.database.cursors), 1)
        mark_published.assert_not_called()
        schedule_retry.assert_not_called()
        self.connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
