"""Unit tests for one dispatcher PostgreSQL-to-RabbitMQ composition attempt.

All database cursors, broker connections, and publisher functions are fakes.
The tests prove ordering and outcome handling without opening PostgreSQL or
RabbitMQ, sending a message, sleeping, or running a Kubernetes Pod.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import unittest
from unittest.mock import MagicMock, patch

from app.amqp_publisher import (
    DispatcherBrokerUnavailable,
    DispatcherPublisherConfirmationUnknown,
    DispatcherPublisherContractError,
    DispatcherPublisherSettings,
)
from app.dispatch_once import (
    DEFAULT_DISPATCH_LEASE_SECONDS,
    DEFAULT_RETRY_AFTER_SECONDS,
    DispatchOnceOutcome,
    dispatch_once,
)
from app.outbox_lease import DispatcherDeadLetterCode, LeasedDemucsOutboxEvent


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
LEASE_TOKEN = "0a2e7458-c355-4933-ac0b-5788eecc504d"


def leased_event() -> LeasedDemucsOutboxEvent:
    """Return one realistic, already-claimed durable outbox event."""

    return LeasedDemucsOutboxEvent(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        payload={
            "schema_version": 1,
            "job_id": JOB_ID,
            "source": {"bucket": "clouddsp-uploads", "object_key": f"uploads/{JOB_ID}/mix.wav"},
            "stem_mode": "4-stems",
        },
        delivery_attempts=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
    )


def publisher_settings() -> DispatcherPublisherSettings:
    """Return non-secret test-only AMQP configuration for fake connections."""

    return DispatcherPublisherSettings(
        host="rabbitmq.test",
        port=5672,
        username="clouddsp-dispatcher",
        password="not-a-real-password",
    )


class FakeDatabase:
    """Record the short PostgreSQL scopes used by one composition attempt."""

    def __init__(self) -> None:
        self.cursors: list[MagicMock] = []

    @contextmanager
    def write_cursor(self):
        """Yield a fresh fake cursor and record its bounded transaction scope."""

        cursor = MagicMock()
        self.cursors.append(cursor)
        yield cursor


class DispatchOnceTests(unittest.TestCase):
    """Prove the commit → confirm → completion/recovery state machine."""

    def setUp(self) -> None:
        self.database = FakeDatabase()
        self.connection = MagicMock()
        self.channel = MagicMock()
        self.connection.channel.return_value = self.channel

    def _run(self, **kwargs: object):
        """Call one attempt with the fake connection unless a test replaces it."""

        return dispatch_once(
            database=self.database,
            publisher_settings=publisher_settings(),
            connection_factory=kwargs.pop("connection_factory", MagicMock(return_value=self.connection)),
            **kwargs,
        )

    @patch("app.dispatch_once.claim_due_demucs_outbox_event", return_value=None)
    @patch("app.dispatch_once.open_dispatcher_rabbitmq_connection")
    def test_idle_outbox_opens_no_broker_connection(self, _unused_default_connection, claim) -> None:
        """An idle poll touches only PostgreSQL and produces no artificial work."""

        result = self._run()

        self.assertEqual(result.outcome, DispatchOnceOutcome.IDLE)
        self.assertEqual(len(self.database.cursors), 1)
        claim.assert_called_once_with(
            self.database.cursors[0],
            lease_seconds=DEFAULT_DISPATCH_LEASE_SECONDS,
        )
        self.connection.channel.assert_not_called()

    @patch("app.dispatch_once.mark_demucs_outbox_event_published", return_value=True)
    @patch("app.dispatch_once.publish_demucs_requested")
    @patch("app.dispatch_once.enable_dispatcher_publisher_confirms")
    @patch("app.dispatch_once.claim_due_demucs_outbox_event", return_value=leased_event())
    def test_confirmed_publish_marks_same_lease_in_a_second_database_scope(
        self,
        claim,
        enable_confirms,
        publish,
        mark_published,
    ) -> None:
        """The claim scope ends before broker I/O, then completion is guarded."""

        connection_factory = MagicMock(return_value=self.connection)

        result = self._run(connection_factory=connection_factory)

        self.assertEqual(result.outcome, DispatchOnceOutcome.PUBLISHED)
        self.assertEqual(len(self.database.cursors), 2)
        connection_factory.assert_called_once_with(publisher_settings())
        enable_confirms.assert_called_once_with(self.channel)
        publish.assert_called_once_with(
            self.channel,
            event_id=EVENT_ID,
            job_id=JOB_ID,
            payload=leased_event().payload,
        )
        mark_published.assert_called_once_with(
            self.database.cursors[1],
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
        )
        self.connection.close.assert_called_once_with()
        claim.assert_called_once_with(
            self.database.cursors[0],
            lease_seconds=DEFAULT_DISPATCH_LEASE_SECONDS,
        )

    @patch("app.dispatch_once.schedule_demucs_outbox_event_retry", return_value=True)
    @patch("app.dispatch_once.claim_due_demucs_outbox_event", return_value=leased_event())
    def test_known_pre_publish_broker_failure_schedules_bounded_retry(self, claim, schedule_retry) -> None:
        """A failed connection is known not to have handed a body to RabbitMQ."""

        connection_factory = MagicMock(side_effect=DispatcherBrokerUnavailable())

        result = self._run(connection_factory=connection_factory)

        self.assertEqual(result.outcome, DispatchOnceOutcome.RETRY_SCHEDULED)
        self.assertEqual(len(self.database.cursors), 2)
        schedule_retry.assert_called_once_with(
            self.database.cursors[1],
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
            retry_after_seconds=DEFAULT_RETRY_AFTER_SECONDS,
            failure_code=DispatcherBrokerUnavailable().failure_code,
        )
        claim.assert_called_once()

    @patch("app.dispatch_once.schedule_demucs_outbox_event_retry", return_value=False)
    @patch("app.dispatch_once.claim_due_demucs_outbox_event", return_value=leased_event())
    def test_known_failure_does_not_overwrite_a_recovered_lease(self, _claim, _schedule_retry) -> None:
        """A stale publisher cannot release a lease another attempt now owns."""

        result = self._run(connection_factory=MagicMock(side_effect=DispatcherBrokerUnavailable()))

        self.assertEqual(result.outcome, DispatchOnceOutcome.LEASE_NO_LONGER_OWNED)

    @patch("app.dispatch_once.schedule_demucs_outbox_event_retry")
    @patch("app.dispatch_once.mark_demucs_outbox_event_published")
    @patch("app.dispatch_once.publish_demucs_requested", side_effect=DispatcherPublisherConfirmationUnknown("safe"))
    @patch("app.dispatch_once.enable_dispatcher_publisher_confirms")
    @patch("app.dispatch_once.claim_due_demucs_outbox_event", return_value=leased_event())
    def test_uncertain_confirmation_leaves_lease_unchanged(
        self,
        _claim,
        _enable_confirms,
        _publish,
        mark_published,
        schedule_retry,
    ) -> None:
        """An eager retry here could create a duplicate after a lost confirm."""

        result = self._run()

        self.assertEqual(result.outcome, DispatchOnceOutcome.CONFIRMATION_UNKNOWN)
        self.assertEqual(len(self.database.cursors), 1)
        mark_published.assert_not_called()
        schedule_retry.assert_not_called()
        self.connection.close.assert_called_once_with()

    @patch("app.dispatch_once.mark_demucs_outbox_event_dead_lettered", return_value=True)
    @patch("app.dispatch_once.schedule_demucs_outbox_event_retry")
    @patch("app.dispatch_once.publish_demucs_requested", side_effect=DispatcherPublisherContractError("safe"))
    @patch("app.dispatch_once.enable_dispatcher_publisher_confirms")
    @patch("app.dispatch_once.claim_due_demucs_outbox_event", return_value=leased_event())
    def test_invalid_durable_body_is_never_published_or_silently_retried(
        self,
        _claim,
        _enable_confirms,
        _publish,
        schedule_retry,
        mark_dead_lettered,
    ) -> None:
        """A malformed body becomes terminal before any Demucs worker sees it."""

        result = self._run()

        self.assertEqual(result.outcome, DispatchOnceOutcome.EVENT_DEAD_LETTERED)
        self.assertEqual(len(self.database.cursors), 2)
        schedule_retry.assert_not_called()
        mark_dead_lettered.assert_called_once_with(
            self.database.cursors[1],
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
            reason=DispatcherDeadLetterCode.INVALID_EVENT_CONTRACT,
        )

    @patch("app.dispatch_once.mark_demucs_outbox_event_published", return_value=False)
    @patch("app.dispatch_once.publish_demucs_requested")
    @patch("app.dispatch_once.enable_dispatcher_publisher_confirms")
    @patch("app.dispatch_once.claim_due_demucs_outbox_event", return_value=leased_event())
    def test_confirmed_publish_does_not_overwrite_a_recovered_lease(
        self,
        _claim,
        _enable_confirms,
        _publish,
        mark_published,
    ) -> None:
        """A stale lease result is not permission to write a second completion."""

        result = self._run()

        self.assertEqual(result.outcome, DispatchOnceOutcome.LEASE_NO_LONGER_OWNED)
        self.assertEqual(len(self.database.cursors), 2)
        mark_published.assert_called_once()

    @patch("app.dispatch_once.schedule_demucs_outbox_event_retry", return_value=True)
    @patch("app.dispatch_once.claim_due_demucs_outbox_event", return_value=leased_event())
    def test_channel_creation_failure_is_known_safe_to_retry(self, _claim, schedule_retry) -> None:
        """No message body exists at the broker before `connection.channel()` succeeds."""

        self.connection.channel.side_effect = RuntimeError("private channel diagnostic")

        result = self._run()

        self.assertEqual(result.outcome, DispatchOnceOutcome.RETRY_SCHEDULED)
        schedule_retry.assert_called_once_with(
            self.database.cursors[1],
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
            retry_after_seconds=DEFAULT_RETRY_AFTER_SECONDS,
            failure_code=DispatcherBrokerUnavailable().failure_code,
        )


if __name__ == "__main__":
    unittest.main()
