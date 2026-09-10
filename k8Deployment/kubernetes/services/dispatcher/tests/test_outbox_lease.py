"""Focused tests for the dispatcher lease SQL boundary.

These tests use dictionary-shaped mock cursor rows rather than PostgreSQL. They
prove parameter order, lease ownership checks, and safe no-row outcomes before
a later task introduces a real database connection or RabbitMQ publisher.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from app.outbox_lease import (
    CLAIM_DUE_DISPATCHABLE_OUTBOX_EVENT_SQL,
    CLAIM_DUE_DEMUCS_OUTBOX_EVENT_SQL,
    MARK_OUTBOX_EVENT_DEAD_LETTERED_SQL,
    MARK_OUTBOX_EVENT_PUBLISHED_SQL,
    MARK_DEMUCS_OUTBOX_EVENT_DEAD_LETTERED_SQL,
    MARK_DEMUCS_OUTBOX_EVENT_PUBLISHED_SQL,
    SCHEDULE_OUTBOX_EVENT_RETRY_SQL,
    SCHEDULE_DEMUCS_OUTBOX_EVENT_RETRY_SQL,
    DispatcherDeadLetterCode,
    DispatcherOutboxProtocolError,
    DispatcherPublishFailureCode,
    claim_due_dispatchable_outbox_event,
    claim_due_demucs_outbox_event,
    mark_demucs_outbox_event_dead_lettered,
    mark_demucs_outbox_event_published,
    mark_outbox_event_dead_lettered,
    mark_outbox_event_published,
    schedule_outbox_event_retry,
    schedule_demucs_outbox_event_retry,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
LEASE_TOKEN = "0a2e7458-c355-4933-ac0b-5788eecc504d"
LEASE_EXPIRY = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def leased_row(**overrides: object) -> dict[str, object]:
    """Build the dictionary row Psycopg's later ``dict_row`` will supply."""

    row: dict[str, object] = {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stage": "demucs",
        "stem_name": "",
        "event_type": "demucs.requested",
        "payload": {
            "schema_version": 1,
            "job_id": JOB_ID,
            "source": {"bucket": "clouddsp-uploads", "object_key": f"uploads/{JOB_ID}/mix.wav"},
            "stem_mode": "4-stems",
        },
        "publication_status": "leased",
        "delivery_attempts": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": LEASE_EXPIRY,
    }
    row.update(overrides)
    return row


def downstream_leased_row(
    *,
    stage: str = "basic-pitch",
    stem_name: str = "bass",
    event_type: str = "basic-pitch.requested",
    **overrides: object,
) -> dict[str, object]:
    """Build one v004 downstream claim row without a database or broker.

    The generic lease layer needs only a matching Job ID; the next composition
    task will use the stricter request builder to validate the private WAV
    evidence inside ``payload`` before RabbitMQ publication.
    """

    row = leased_row(
        stage=stage,
        stem_name=stem_name,
        event_type=event_type,
        payload={
            "schema_version": 1,
            "job_id": JOB_ID,
            "stem_name": stem_name,
            "stem": {
                "bucket": "clouddsp-uploads",
                "object_key": f"stems/{JOB_ID}/{stem_name}.wav",
                "content_type": "audio/wav",
                "size_bytes": 1234,
                "sha256": "a" * 64,
            },
        },
    )
    row.update(overrides)
    return row


class ClaimDueDispatchableOutboxEventTests(unittest.TestCase):
    """Prove the future generic dispatcher can lease only reviewed v004 rows."""

    def setUp(self) -> None:
        self.cursor = MagicMock()

    def test_claims_basic_pitch_event_and_retains_the_routing_triple(self) -> None:
        """The composition layer receives the exact triple needed for routing."""

        self.cursor.fetchone.return_value = downstream_leased_row()

        event = claim_due_dispatchable_outbox_event(
            self.cursor,
            lease_seconds=30,
            lease_token=LEASE_TOKEN,
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.stage, "basic-pitch")
        self.assertEqual(event.stem_name, "bass")
        self.assertEqual(event.event_type, "basic-pitch.requested")
        self.assertEqual(event.payload["job_id"], JOB_ID)
        self.cursor.execute.assert_called_once_with(
            CLAIM_DUE_DISPATCHABLE_OUTBOX_EVENT_SQL,
            (LEASE_TOKEN, 30),
        )

    def test_claims_the_drums_only_adtof_route(self) -> None:
        """ADTOF retains its distinct stage and cannot be inferred from a name."""

        self.cursor.fetchone.return_value = downstream_leased_row(
            stage="adtof",
            stem_name="drums",
            event_type="adtof.requested",
        )

        event = claim_due_dispatchable_outbox_event(
            self.cursor,
            lease_seconds=30,
            lease_token=LEASE_TOKEN,
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual((event.stage, event.stem_name, event.event_type), ("adtof", "drums", "adtof.requested"))

    def test_rejects_a_row_outside_the_database_and_python_allowlist(self) -> None:
        """A driver/schema mismatch cannot widen the later AMQP publisher."""

        self.cursor.fetchone.return_value = downstream_leased_row(
            stem_name="drums",
        )

        with self.assertRaises(DispatcherOutboxProtocolError):
            claim_due_dispatchable_outbox_event(
                self.cursor,
                lease_seconds=30,
                lease_token=LEASE_TOKEN,
            )

    def test_generic_claim_sql_is_finite_and_keeps_existing_lease_recovery(self) -> None:
        """Only the three reviewed stage families become claimable work."""

        self.assertIn("stage = 'demucs'", CLAIM_DUE_DISPATCHABLE_OUTBOX_EVENT_SQL)
        self.assertIn("stage = 'basic-pitch'", CLAIM_DUE_DISPATCHABLE_OUTBOX_EVENT_SQL)
        self.assertIn("stage = 'adtof'", CLAIM_DUE_DISPATCHABLE_OUTBOX_EVENT_SQL)
        self.assertIn("FOR UPDATE SKIP LOCKED", CLAIM_DUE_DISPATCHABLE_OUTBOX_EVENT_SQL)
        self.assertIn("lease_expires_at <= CURRENT_TIMESTAMP", CLAIM_DUE_DISPATCHABLE_OUTBOX_EVENT_SQL)


class ClaimDueDemucsOutboxEventTests(unittest.TestCase):
    """Prove claim/recovery behavior without a database or broker."""

    def setUp(self) -> None:
        self.cursor = MagicMock()

    def test_claims_one_valid_due_event_with_the_supplied_lease_token(self) -> None:
        self.cursor.fetchone.return_value = leased_row()

        event = claim_due_demucs_outbox_event(
            self.cursor,
            lease_seconds=30,
            lease_token=LEASE_TOKEN,
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_id, EVENT_ID)
        self.assertEqual(event.job_id, JOB_ID)
        self.assertEqual(event.lease_token, LEASE_TOKEN)
        self.assertEqual(event.delivery_attempts, 1)
        self.assertEqual(event.payload["job_id"], JOB_ID)
        self.cursor.execute.assert_called_once_with(
            CLAIM_DUE_DEMUCS_OUTBOX_EVENT_SQL,
            (LEASE_TOKEN, 30),
        )

    def test_returns_none_when_no_pending_or_expired_lease_is_due(self) -> None:
        self.cursor.fetchone.return_value = None

        result = claim_due_demucs_outbox_event(
            self.cursor,
            lease_seconds=30,
            lease_token=LEASE_TOKEN,
        )

        self.assertIsNone(result)

    def test_rejects_invalid_claim_row_before_a_publisher_can_use_it(self) -> None:
        self.cursor.fetchone.return_value = leased_row(lease_token=EVENT_ID)

        with self.assertRaises(DispatcherOutboxProtocolError):
            claim_due_demucs_outbox_event(
                self.cursor,
                lease_seconds=30,
                lease_token=LEASE_TOKEN,
            )

    def test_rejects_an_unbounded_lease_duration(self) -> None:
        with self.assertRaisesRegex(ValueError, "lease_seconds"):
            claim_due_demucs_outbox_event(
                self.cursor,
                lease_seconds=301,
                lease_token=LEASE_TOKEN,
            )
        self.cursor.execute.assert_not_called()

    def test_claim_sql_covers_due_rows_recovery_and_nonblocking_selection(self) -> None:
        self.assertIn("publication_status = 'pending'", CLAIM_DUE_DEMUCS_OUTBOX_EVENT_SQL)
        self.assertIn("publication_status = 'leased'", CLAIM_DUE_DEMUCS_OUTBOX_EVENT_SQL)
        self.assertIn("lease_expires_at <= CURRENT_TIMESTAMP", CLAIM_DUE_DEMUCS_OUTBOX_EVENT_SQL)
        self.assertIn("FOR UPDATE SKIP LOCKED", CLAIM_DUE_DEMUCS_OUTBOX_EVENT_SQL)
        self.assertIn("delivery_attempts = outbox.delivery_attempts + 1", CLAIM_DUE_DEMUCS_OUTBOX_EVENT_SQL)


class CompleteLeaseTests(unittest.TestCase):
    """Prove that only the current lease owner can complete or retry work."""

    def setUp(self) -> None:
        self.cursor = MagicMock()

    def test_marks_confirmed_publish_only_for_the_matching_lease(self) -> None:
        self.cursor.fetchone.return_value = {
            "event_id": EVENT_ID,
            "publication_status": "published",
            "published_at": LEASE_EXPIRY,
        }

        applied = mark_demucs_outbox_event_published(
            self.cursor,
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
        )

        self.assertTrue(applied)
        self.cursor.execute.assert_called_once_with(
            MARK_DEMUCS_OUTBOX_EVENT_PUBLISHED_SQL,
            (EVENT_ID, LEASE_TOKEN),
        )

    def test_returns_false_when_a_stale_owner_can_no_longer_mark_published(self) -> None:
        self.cursor.fetchone.return_value = None

        self.assertFalse(
            mark_demucs_outbox_event_published(
                self.cursor,
                event_id=EVENT_ID,
                lease_token=LEASE_TOKEN,
            )
        )

    def test_schedules_retry_only_for_a_known_broker_failure(self) -> None:
        self.cursor.fetchone.return_value = {
            "event_id": EVENT_ID,
            "publication_status": "pending",
            "available_at": LEASE_EXPIRY + timedelta(seconds=30),
            "last_error_code": "publisher_nack",
        }

        applied = schedule_demucs_outbox_event_retry(
            self.cursor,
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
            retry_after_seconds=30,
            failure_code=DispatcherPublishFailureCode.PUBLISHER_NACK,
        )

        self.assertTrue(applied)
        self.cursor.execute.assert_called_once_with(
            SCHEDULE_DEMUCS_OUTBOX_EVENT_RETRY_SQL,
            (30, "publisher_nack", EVENT_ID, LEASE_TOKEN),
        )

    def test_rejects_an_unreviewed_failure_string_without_executing_sql(self) -> None:
        with self.assertRaises(TypeError):
            schedule_demucs_outbox_event_retry(
                self.cursor,
                event_id=EVENT_ID,
                lease_token=LEASE_TOKEN,
                retry_after_seconds=30,
                failure_code="raw broker error",  # type: ignore[arg-type]
            )
        self.cursor.execute.assert_not_called()

    def test_retry_sql_releases_the_matching_lease_without_changing_attempt_count(self) -> None:
        self.assertIn("publication_status = 'pending'", SCHEDULE_DEMUCS_OUTBOX_EVENT_RETRY_SQL)
        self.assertIn("lease_token = NULL", SCHEDULE_DEMUCS_OUTBOX_EVENT_RETRY_SQL)
        self.assertIn("lease_token = %s::uuid", SCHEDULE_DEMUCS_OUTBOX_EVENT_RETRY_SQL)
        self.assertNotIn("delivery_attempts =", SCHEDULE_DEMUCS_OUTBOX_EVENT_RETRY_SQL)


class GenericCompletionLeaseTests(unittest.TestCase):
    """Prove stage-neutral completions preserve the same lease ownership guard."""

    def setUp(self) -> None:
        self.cursor = MagicMock()

    def test_generic_publish_completion_can_finish_a_downstream_event_lease(self) -> None:
        """The SQL key is event/lease ownership, never the worker stage name."""

        self.cursor.fetchone.return_value = {
            "event_id": EVENT_ID,
            "publication_status": "published",
            "published_at": LEASE_EXPIRY,
        }

        applied = mark_outbox_event_published(
            self.cursor,
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
        )

        self.assertTrue(applied)
        self.cursor.execute.assert_called_once_with(
            MARK_OUTBOX_EVENT_PUBLISHED_SQL,
            (EVENT_ID, LEASE_TOKEN),
        )

    def test_generic_retry_keeps_the_bounded_known_failure_rule(self) -> None:
        """Basic Pitch/ADTOF will not receive an eager retry after uncertainty."""

        self.cursor.fetchone.return_value = {
            "event_id": EVENT_ID,
            "publication_status": "pending",
            "available_at": LEASE_EXPIRY + timedelta(seconds=30),
            "last_error_code": "queue_rejected",
        }

        applied = schedule_outbox_event_retry(
            self.cursor,
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
            retry_after_seconds=30,
            failure_code=DispatcherPublishFailureCode.QUEUE_REJECTED,
        )

        self.assertTrue(applied)
        self.cursor.execute.assert_called_once_with(
            SCHEDULE_OUTBOX_EVENT_RETRY_SQL,
            (30, "queue_rejected", EVENT_ID, LEASE_TOKEN),
        )

    def test_generic_dead_letter_retains_only_a_fixed_safe_category(self) -> None:
        """An invalid downstream request is retained without private payload text."""

        self.cursor.fetchone.return_value = {
            "event_id": EVENT_ID,
            "publication_status": "dead_lettered",
            "last_error_code": "invalid_event_contract",
        }

        applied = mark_outbox_event_dead_lettered(
            self.cursor,
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
            reason=DispatcherDeadLetterCode.INVALID_EVENT_CONTRACT,
        )

        self.assertTrue(applied)
        self.cursor.execute.assert_called_once_with(
            MARK_OUTBOX_EVENT_DEAD_LETTERED_SQL,
            ("invalid_event_contract", EVENT_ID, LEASE_TOKEN),
        )


class DeadLetterLeaseTests(unittest.TestCase):
    """Prove malformed durable events become terminal before broker publication."""

    def setUp(self) -> None:
        self.cursor = MagicMock()

    def test_marks_only_matching_lease_dead_lettered_with_a_fixed_reason(self) -> None:
        """The database stores a safe category, never a raw payload/exception."""

        self.cursor.fetchone.return_value = {
            "event_id": EVENT_ID,
            "publication_status": "dead_lettered",
            "last_error_code": "invalid_event_contract",
        }

        applied = mark_demucs_outbox_event_dead_lettered(
            self.cursor,
            event_id=EVENT_ID,
            lease_token=LEASE_TOKEN,
            reason=DispatcherDeadLetterCode.INVALID_EVENT_CONTRACT,
        )

        self.assertTrue(applied)
        self.cursor.execute.assert_called_once_with(
            MARK_DEMUCS_OUTBOX_EVENT_DEAD_LETTERED_SQL,
            ("invalid_event_contract", EVENT_ID, LEASE_TOKEN),
        )

    def test_returns_false_when_another_dispatcher_recovered_the_lease(self) -> None:
        """A stale attempt must not write terminal state over newer ownership."""

        self.cursor.fetchone.return_value = None

        self.assertFalse(
            mark_demucs_outbox_event_dead_lettered(
                self.cursor,
                event_id=EVENT_ID,
                lease_token=LEASE_TOKEN,
                reason=DispatcherDeadLetterCode.INVALID_EVENT_CONTRACT,
            )
        )

    def test_rejects_an_unreviewed_terminal_reason_without_executing_sql(self) -> None:
        """No arbitrary diagnostic string may enter the durable outbox table."""

        with self.assertRaises(TypeError):
            mark_demucs_outbox_event_dead_lettered(
                self.cursor,
                event_id=EVENT_ID,
                lease_token=LEASE_TOKEN,
                reason="raw payload detail",  # type: ignore[arg-type]
            )

        self.cursor.execute.assert_not_called()

    def test_dead_letter_sql_clears_lease_without_recording_a_publish(self) -> None:
        """The terminal row proves this event was never publisher-confirmed."""

        self.assertIn("publication_status = 'dead_lettered'", MARK_DEMUCS_OUTBOX_EVENT_DEAD_LETTERED_SQL)
        self.assertIn("lease_token = NULL", MARK_DEMUCS_OUTBOX_EVENT_DEAD_LETTERED_SQL)
        self.assertIn("lease_token = %s::uuid", MARK_DEMUCS_OUTBOX_EVENT_DEAD_LETTERED_SQL)
        self.assertNotIn("published_at =", MARK_DEMUCS_OUTBOX_EVENT_DEAD_LETTERED_SQL)


if __name__ == "__main__":
    unittest.main()
