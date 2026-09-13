"""Unit tests for pure Basic Pitch first-claim SQL decisions.

The fake cursor records parameterized calls only. These tests do not connect to
PostgreSQL, RabbitMQ, MinIO, Basic Pitch, Docker, or Kubernetes, which keeps
the lease/idempotency contract reviewable before a worker runtime exists.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from app.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.task_lease import (
    BASIC_PITCH_JOB_CLAIM_LOCK_FUNCTION,
    CLAIM_NEXT_DUE_BASIC_PITCH_RETRY_SQL,
    DEFAULT_BASIC_PITCH_LEASE_SECONDS,
    INSERT_FIRST_BASIC_PITCH_TASK_LEASE_SQL,
    LOCK_EXISTING_BASIC_PITCH_TASK_SQL,
    LOCK_JOB_FOR_BASIC_PITCH_CLAIM_SQL,
    START_LEASED_BASIC_PITCH_TASK_SQL,
    BasicPitchStaleRequestReason,
    BasicPitchTaskClaimDisposition,
    BasicPitchTaskClaimInconsistency,
    BasicPitchTaskLease,
    BasicPitchTaskLeaseProtocolError,
    claim_basic_pitch_task_for_delivery,
    claim_next_due_basic_pitch_retry,
    start_leased_basic_pitch_task,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
LEASE_EXPIRY = datetime(2026, 9, 11, 12, 15, tzinfo=UTC)
STEM_NAME = "vocals"
STEM_KEY = f"stems/{JOB_ID}/{STEM_NAME}.wav"
STEM_SHA256 = "a" * 64


def message() -> BasicPitchRequestedMessage:
    """Return a parser-shaped non-drum request for a four-stem Job."""

    return BasicPitchRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        stem_name=STEM_NAME,
        stem_bucket="clouddsp-uploads",
        stem_object_key=STEM_KEY,
        stem_content_length=101,
        stem_sha256=STEM_SHA256,
    )


def job_row(**overrides: object) -> dict[str, object]:
    """Return the small locked Job projection required by a valid claim."""

    row: dict[str, object] = {
        "job_id": JOB_ID,
        "stem_mode": "4-stems",
        "status": "midi_processing",
        "revision": 8,
        "is_retained": True,
    }
    row.update(overrides)
    return row


def outbox_row(**overrides: object) -> dict[str, object]:
    """Return durable JSONB evidence that exactly matches the AMQP delivery."""

    row: dict[str, object] = {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stage": "basic-pitch",
        "stem_name": STEM_NAME,
        "event_type": "basic-pitch.requested",
        "publication_status": "published",
        "payload": {
            "schema_version": 1,
            "job_id": JOB_ID,
            "stem_name": STEM_NAME,
            "stem": {
                "bucket": "clouddsp-uploads",
                "object_key": STEM_KEY,
                "content_type": "audio/wav",
                "size_bytes": 101,
                "sha256": STEM_SHA256,
            },
        },
    }
    row.update(overrides)
    return row


def leased_task_row(**overrides: object) -> dict[str, object]:
    """Return a row shaped like the task lookup or INSERT RETURNING clause."""

    row: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stage": "basic-pitch",
        "stem_name": STEM_NAME,
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": STEM_KEY,
        "stem_mode": "4-stems",
        "status": "leased",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": LEASE_EXPIRY,
    }
    row.update(overrides)
    return row


class FakeCursor:
    """Script rows and retain every query/parameter tuple the adapter sends."""

    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        if not self._rows:
            raise AssertionError("adapter fetched more rows than the test supplied")
        return self._rows.pop(0)


class FixedUuidFactory:
    """Supply deterministic UUID objects without weakening production UUID use."""

    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> UUID:
        return UUID(next(self._values))


class FirstClaimTests(unittest.TestCase):
    """Prove one delivery becomes one lease, duplicate, stale result, or error."""

    def test_job_claim_lock_uses_only_the_reviewed_security_definer_boundary(self) -> None:
        """The worker must never regain direct `jobs` row-lock authority.

        PostgreSQL treats every `FOR UPDATE` variant as requiring UPDATE
        privilege. This static contract keeps that exceptional capability in
        the database function, whose SQL and grants are reviewed separately in
        the administrator-only bootstrap Job.
        """

        self.assertIn(BASIC_PITCH_JOB_CLAIM_LOCK_FUNCTION, LOCK_JOB_FOR_BASIC_PITCH_CLAIM_SQL)
        self.assertIn("(%s::uuid)", LOCK_JOB_FOR_BASIC_PITCH_CLAIM_SQL)
        self.assertNotIn("public.jobs", LOCK_JOB_FOR_BASIC_PITCH_CLAIM_SQL)
        self.assertNotIn("FOR UPDATE", LOCK_JOB_FOR_BASIC_PITCH_CLAIM_SQL)

    def test_valid_delivery_locks_job_and_task_then_inserts_one_lease(self) -> None:
        """Commit of this result must precede a later AMQP acknowledgement."""

        cursor = FakeCursor([None, job_row(), None, outbox_row(), leased_task_row()])
        result = claim_basic_pitch_task_for_delivery(
            cursor,
            message=message(),
            uuid_factory=FixedUuidFactory(TASK_ID, LEASE_TOKEN),
        )

        self.assertEqual(result.disposition, BasicPitchTaskClaimDisposition.CLAIMED)
        self.assertIsNotNone(result.lease)
        assert result.lease is not None
        self.assertEqual(result.lease.stem_name, STEM_NAME)
        self.assertEqual(len(cursor.calls), 5)
        self.assertEqual(cursor.calls[0][0], LOCK_EXISTING_BASIC_PITCH_TASK_SQL)
        self.assertEqual(cursor.calls[0][1], (JOB_ID, STEM_NAME))
        self.assertEqual(cursor.calls[1][0], LOCK_JOB_FOR_BASIC_PITCH_CLAIM_SQL)
        self.assertEqual(cursor.calls[1][1], (JOB_ID,))
        self.assertEqual(cursor.calls[2][0], LOCK_EXISTING_BASIC_PITCH_TASK_SQL)
        self.assertEqual(cursor.calls[4][0], INSERT_FIRST_BASIC_PITCH_TASK_LEASE_SQL)
        self.assertEqual(
            cursor.calls[4][1],
            (
                TASK_ID,
                JOB_ID,
                STEM_NAME,
                EVENT_ID,
                "clouddsp-uploads",
                STEM_KEY,
                "4-stems",
                LEASE_TOKEN,
                DEFAULT_BASIC_PITCH_LEASE_SECONDS,
            ),
        )

    def test_matching_existing_task_is_duplicate_and_is_not_mutated(self) -> None:
        """At-least-once redelivery cannot create a second per-stem task."""

        cursor = FakeCursor([leased_task_row(status="running")])
        result = claim_basic_pitch_task_for_delivery(cursor, message=message())

        self.assertEqual(result.disposition, BasicPitchTaskClaimDisposition.DUPLICATE)
        self.assertEqual(result.duplicate_status, "running")
        self.assertEqual(len(cursor.calls), 1)

    def test_existing_task_with_other_event_or_input_is_not_safe_duplicate(self) -> None:
        """A corrupt task/event relationship must reach later durable handling."""

        for row in (
            leased_task_row(request_event_id=TASK_ID),
            leased_task_row(input_object_key=f"stems/{JOB_ID}/bass.wav"),
        ):
            with self.subTest(row=row):
                with self.assertRaises(BasicPitchTaskClaimInconsistency):
                    claim_basic_pitch_task_for_delivery(FakeCursor([row]), message=message())

    def test_missing_expired_or_terminal_job_is_safe_stale_history(self) -> None:
        """Old broker delivery does not recreate removed or terminal work."""

        cases = (
            ([None, None], BasicPitchStaleRequestReason.JOB_MISSING),
            ([None, job_row(is_retained=False), None], BasicPitchStaleRequestReason.JOB_EXPIRED),
            ([None, job_row(status="failed"), None], BasicPitchStaleRequestReason.JOB_TERMINAL),
        )
        for rows, expected in cases:
            with self.subTest(expected=expected):
                result = claim_basic_pitch_task_for_delivery(FakeCursor(rows), message=message())
                self.assertEqual(result.disposition, BasicPitchTaskClaimDisposition.STALE)
                self.assertEqual(result.stale_reason, expected)

    def test_wrong_job_state_or_stem_mode_never_claims_work(self) -> None:
        """A valid delivery must still agree with the locked Job lifecycle."""

        invalid_cases = (
            (message(), job_row(status="stem_processing")),
            # `no_vocals` is a valid Basic Pitch request but is produced only
            # by Demucs two-stem mode, never by the four-stem Job below.
            (
                replace(
                    message(),
                    stem_name="no_vocals",
                    stem_object_key=f"stems/{JOB_ID}/no_vocals.wav",
                ),
                job_row(stem_mode="4-stems"),
            ),
        )
        for request, row in invalid_cases:
            with self.subTest(request=request, row=row):
                with self.assertRaises(BasicPitchTaskClaimInconsistency):
                    claim_basic_pitch_task_for_delivery(FakeCursor([None, row, None]), message=request)

    def test_unpublished_or_payload_mismatch_never_claims_work(self) -> None:
        """AMQP content is never trusted more than the durable outbox event."""

        cases = (
            outbox_row(publication_status="pending"),
            outbox_row(payload={"schema_version": 1}),
            outbox_row(payload={**outbox_row()["payload"], "stem_name": "bass"}),
        )
        for event in cases:
            with self.subTest(event=event):
                with self.assertRaises(BasicPitchTaskClaimInconsistency):
                    claim_basic_pitch_task_for_delivery(
                        FakeCursor([None, job_row(), None, event]), message=message()
                    )

    def test_invalid_lease_duration_or_returned_lease_cannot_grant_ownership(self) -> None:
        """Malformed configuration/driver output aborts rather than owning work."""

        with self.assertRaises(BasicPitchTaskLeaseProtocolError):
            claim_basic_pitch_task_for_delivery(FakeCursor([]), message=message(), lease_seconds=59)

        with self.assertRaises(BasicPitchTaskLeaseProtocolError):
            claim_basic_pitch_task_for_delivery(
                FakeCursor([None, job_row(), None, outbox_row(), leased_task_row(lease_token=TASK_ID)]),
                message=message(),
                uuid_factory=FixedUuidFactory(TASK_ID, LEASE_TOKEN),
            )


def lease(**overrides: object) -> BasicPitchTaskLease:
    """Return one committed, still-active Basic Pitch lease for start tests."""

    arguments: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stem_name": STEM_NAME,
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": STEM_KEY,
        "stem_mode": "4-stems",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": LEASE_EXPIRY,
    }
    arguments.update(overrides)
    return BasicPitchTaskLease(**arguments)  # type: ignore[arg-type]


class TaskStartTests(unittest.TestCase):
    """Prove only a current lease token may make Basic Pitch model-eligible."""

    def test_current_leased_token_moves_to_running_after_stem_preflight(self) -> None:
        """The PostgreSQL timestamp is the required model-start permission."""

        started_at = datetime(2026, 9, 11, 12, 20, tzinfo=UTC)
        cursor = FakeCursor([{"started_at": started_at}])

        returned = start_leased_basic_pitch_task(cursor, lease=lease())

        self.assertEqual(returned, started_at)
        self.assertEqual(cursor.calls[0][0], START_LEASED_BASIC_PITCH_TASK_SQL)
        self.assertEqual(cursor.calls[0][1], (TASK_ID, JOB_ID, STEM_NAME, LEASE_TOKEN))
        self.assertIn("status = 'leased'", START_LEASED_BASIC_PITCH_TASK_SQL)
        self.assertIn("status = 'running'", START_LEASED_BASIC_PITCH_TASK_SQL)
        self.assertIn("lease_token = %s::uuid", START_LEASED_BASIC_PITCH_TASK_SQL)
        self.assertIn("lease_expires_at > CURRENT_TIMESTAMP", START_LEASED_BASIC_PITCH_TASK_SQL)

    def test_lost_or_expired_ownership_returns_none_before_model_work(self) -> None:
        """Recovery is a normal stop signal, not permission for a stale worker."""

        cursor = FakeCursor([None])

        self.assertIsNone(start_leased_basic_pitch_task(cursor, lease=lease()))
        self.assertEqual(len(cursor.calls), 1)

    def test_invalid_returned_timestamp_or_hand_built_lease_is_not_permission(self) -> None:
        """Malformed driver rows and forged task evidence abort before model work."""

        with self.assertRaises(BasicPitchTaskLeaseProtocolError):
            start_leased_basic_pitch_task(FakeCursor([{"started_at": "not-a-timestamp"}]), lease=lease())

        cursor = FakeCursor([])
        with self.assertRaises(BasicPitchTaskLeaseProtocolError):
            start_leased_basic_pitch_task(cursor, lease=lease(input_object_key=f"stems/{JOB_ID}/bass.wav"))
        self.assertEqual(cursor.calls, [])


class DueRetryRecoveryTests(unittest.TestCase):
    """Prove one due durable retry can receive exactly one fresh lease."""

    def test_due_retry_is_claimed_with_a_new_token_and_incremented_attempt(self) -> None:
        """The SQL uses an ordered skip-locked candidate, not a broker delivery."""

        recovery_token = "63c9d8d2-11db-41c4-9cc5-79889f912f98"
        cursor = FakeCursor(
            [
                leased_task_row(
                    attempt_count=2,
                    lease_token=recovery_token,
                )
            ]
        )

        recovered = claim_next_due_basic_pitch_retry(
            cursor,
            uuid_factory=FixedUuidFactory(recovery_token),
        )

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered.task_id, TASK_ID)
        self.assertEqual(recovered.stem_name, STEM_NAME)
        self.assertEqual(recovered.attempt_count, 2)
        self.assertEqual(recovered.lease_token, recovery_token)
        self.assertEqual(
            cursor.calls,
            [
                (
                    CLAIM_NEXT_DUE_BASIC_PITCH_RETRY_SQL,
                    (3, recovery_token, DEFAULT_BASIC_PITCH_LEASE_SECONDS),
                )
            ],
        )
        self.assertIn("status = 'retry_scheduled'", CLAIM_NEXT_DUE_BASIC_PITCH_RETRY_SQL)
        self.assertIn("available_at <= CURRENT_TIMESTAMP", CLAIM_NEXT_DUE_BASIC_PITCH_RETRY_SQL)
        self.assertIn("FOR UPDATE SKIP LOCKED", CLAIM_NEXT_DUE_BASIC_PITCH_RETRY_SQL)
        self.assertIn("attempt_count = task.attempt_count + 1", CLAIM_NEXT_DUE_BASIC_PITCH_RETRY_SQL)
        self.assertNotIn("public.outbox_events", CLAIM_NEXT_DUE_BASIC_PITCH_RETRY_SQL)
        self.assertNotIn("UPDATE public.jobs", CLAIM_NEXT_DUE_BASIC_PITCH_RETRY_SQL)

    def test_no_due_retry_returns_none_without_creating_work(self) -> None:
        """An empty indexed scan is normal idle recovery, not a worker error."""

        self.assertIsNone(
            claim_next_due_basic_pitch_retry(
                FakeCursor([None]),
                uuid_factory=FixedUuidFactory("63c9d8d2-11db-41c4-9cc5-79889f912f98"),
            )
        )

    def test_invalid_returned_lease_or_duration_never_grants_recovery_ownership(self) -> None:
        """A malformed driver row cannot authorize unverified recovery work."""

        recovery_token = "63c9d8d2-11db-41c4-9cc5-79889f912f98"
        for row in (
            leased_task_row(attempt_count=1, lease_token=recovery_token),
            leased_task_row(attempt_count=2, lease_token=LEASE_TOKEN),
            leased_task_row(attempt_count=2, lease_token=recovery_token, input_object_key=f"stems/{JOB_ID}/bass.wav"),
        ):
            with self.subTest(row=row):
                with self.assertRaises(BasicPitchTaskLeaseProtocolError):
                    claim_next_due_basic_pitch_retry(
                        FakeCursor([row]),
                        uuid_factory=FixedUuidFactory(recovery_token),
                    )

        with self.assertRaises(BasicPitchTaskLeaseProtocolError):
            claim_next_due_basic_pitch_retry(FakeCursor([]), lease_seconds=59)


if __name__ == "__main__":
    unittest.main()
