"""Unit tests for ADTOF's pure PostgreSQL first-claim and recovery decisions.

The cursor is scripted in memory. These tests do not connect to PostgreSQL or
RabbitMQ, mount Kubernetes Secrets, perform MinIO I/O, run ADTOF, or create a
Kubernetes resource. They prove durable claim/recovery ordering and idempotency only.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Mapping
from uuid import UUID

from app.adtof_requested_message import ADTOFRequestedMessage
from app.task_claim import (
    DEFAULT_ADTOF_LEASE_SECONDS,
    CLAIM_NEXT_EXPIRED_ADTOF_TASK_SQL,
    FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL,
    INSERT_FIRST_ADTOF_TASK_LEASE_SQL,
    LOCK_EXISTING_ADTOF_TASK_SQL,
    LOCK_JOB_FOR_ADTOF_CLAIM_SQL,
    READ_PUBLISHED_ADTOF_OUTBOX_SQL,
    START_LEASED_ADTOF_TASK_SQL,
    ADTOFStaleRequestReason,
    ADTOFExpiredLeaseTerminalization,
    ADTOFTaskClaimDisposition,
    ADTOFTaskClaimInconsistency,
    ADTOFTaskClaimProtocolError,
    ADTOFTaskLease,
    MAX_ADTOF_TASK_ATTEMPTS,
    claim_adtof_task_for_delivery,
    claim_next_expired_adtof_task,
    finalize_next_expired_exhausted_adtof_task,
    start_leased_adtof_task,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
OTHER_EVENT_ID = "a0adcc3a-2a32-4d77-875c-b9922f3e3ef5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "9381d35a-355f-4fb1-bb39-32ceba7d917f"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
LEASE_EXPIRES_AT = datetime(2026, 9, 13, 12, 15, tzinfo=UTC)
SHA256 = "a" * 64


def message(**overrides: object) -> ADTOFRequestedMessage:
    """Return one parser-approved drums request unless a test changes evidence."""

    values: dict[str, object] = {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stem_name": "drums",
        "stem_bucket": "clouddsp-uploads",
        "stem_object_key": f"stems/{JOB_ID}/drums.wav",
        "stem_content_length": 1234,
        "stem_sha256": SHA256,
    }
    values.update(overrides)
    return ADTOFRequestedMessage(**values)  # type: ignore[arg-type]


def job_row(**overrides: object) -> dict[str, object]:
    """Return only fields supplied by the lock function's reviewed signature."""

    row: dict[str, object] = {
        "job_id": JOB_ID,
        "stem_mode": "4-stems",
        "status": "midi_processing",
        "revision": 7,
        "is_retained": True,
    }
    row.update(overrides)
    return row


def outbox_row(**overrides: object) -> dict[str, object]:
    """Return immutable event evidence matching the parser-approved request."""

    row: dict[str, object] = {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stage": "adtof",
        "stem_name": "drums",
        "event_type": "adtof.requested",
        "publication_status": "published",
        "payload": {
            "schema_version": 1,
            "job_id": JOB_ID,
            "stem_name": "drums",
            "stem": {
                "bucket": "clouddsp-uploads",
                "object_key": f"stems/{JOB_ID}/drums.wav",
                "content_type": "audio/wav",
                "size_bytes": 1234,
                "sha256": SHA256,
            },
        },
    }
    row.update(overrides)
    return row


def leased_task_row(**overrides: object) -> dict[str, object]:
    """Return a valid row from either an existing-task or INSERT RETURNING query."""

    row: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stage": "adtof",
        "stem_name": "drums",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"stems/{JOB_ID}/drums.wav",
        "stem_mode": "4-stems",
        "status": "leased",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": LEASE_EXPIRES_AT,
    }
    row.update(overrides)
    return row


def exhausted_terminalization_row(**overrides: object) -> dict[str, object]:
    """Return the exact safe projection from one exhausted-lease transition."""

    row: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stage": "adtof",
        "stem_name": "drums",
        "status": "failed",
        "attempt_count": 3,
        "completed_at": datetime(2026, 9, 13, 12, 30, tzinfo=UTC),
        "last_error_code": "lease_expired_attempts_exhausted",
    }
    row.update(overrides)
    return row


def lease(**overrides: object) -> ADTOFTaskLease:
    """Return a direct immutable lease for task-start adapter tests."""

    values: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stem_name": "drums",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"stems/{JOB_ID}/drums.wav",
        "stem_mode": "4-stems",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": LEASE_EXPIRES_AT,
    }
    values.update(overrides)
    return ADTOFTaskLease(**values)  # type: ignore[arg-type]


class ScriptedCursor:
    """Record parameterized SQL and return one prearranged row per fetchone."""

    def __init__(self, rows: list[Mapping[str, object] | None]) -> None:
        self._rows = iter(rows)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        """Record the query shape/parameters without interpolating test input."""

        self.calls.append((query, params))

    def fetchone(self) -> Mapping[str, object] | None:
        """Return the next scripted driver result."""

        return next(self._rows)


class FixedUuidFactory:
    """Return deterministic UUID objects while production uses ``uuid4``."""

    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> UUID:
        """Return one canonical UUID object for the next inserted identity."""

        return UUID(next(self._values))


class ADTOFTaskClaimTests(unittest.TestCase):
    """Prove first claim, duplicate, stale, and conflict behavior without I/O."""

    def test_valid_first_claim_rechecks_durable_authority_then_inserts_one_lease(self) -> None:
        """Every input source agrees before PostgreSQL creates the task row."""

        cursor = ScriptedCursor([None, job_row(), None, outbox_row(), leased_task_row()])

        result = claim_adtof_task_for_delivery(
            cursor,
            message=message(),
            uuid_factory=FixedUuidFactory(TASK_ID, LEASE_TOKEN),
        )

        self.assertEqual(result.disposition, ADTOFTaskClaimDisposition.CLAIMED)
        assert result.lease is not None
        self.assertEqual(result.lease.task_id, TASK_ID)
        self.assertEqual(result.lease.lease_token, LEASE_TOKEN)
        self.assertEqual(len(cursor.calls), 5)
        self.assertIs(cursor.calls[0][0], LOCK_EXISTING_ADTOF_TASK_SQL)
        self.assertEqual(cursor.calls[0][1], (JOB_ID,))
        self.assertIs(cursor.calls[1][0], LOCK_JOB_FOR_ADTOF_CLAIM_SQL)
        self.assertIs(cursor.calls[2][0], LOCK_EXISTING_ADTOF_TASK_SQL)
        self.assertIs(cursor.calls[3][0], READ_PUBLISHED_ADTOF_OUTBOX_SQL)
        self.assertIs(cursor.calls[4][0], INSERT_FIRST_ADTOF_TASK_LEASE_SQL)
        self.assertEqual(
            cursor.calls[4][1],
            (
                TASK_ID,
                JOB_ID,
                EVENT_ID,
                "clouddsp-uploads",
                f"stems/{JOB_ID}/drums.wav",
                "4-stems",
                LEASE_TOKEN,
                DEFAULT_ADTOF_LEASE_SECONDS,
            ),
        )

    def test_existing_matching_task_is_a_read_only_duplicate(self) -> None:
        """Redelivery never reads Job/outbox or inserts another drums task."""

        cursor = ScriptedCursor([leased_task_row(status="running")])

        result = claim_adtof_task_for_delivery(cursor, message=message())

        self.assertEqual(result.disposition, ADTOFTaskClaimDisposition.DUPLICATE)
        self.assertEqual(result.duplicate_status, "running")
        self.assertEqual(len(cursor.calls), 1)
        self.assertIs(cursor.calls[0][0], LOCK_EXISTING_ADTOF_TASK_SQL)

    def test_missing_expired_and_terminal_jobs_become_safe_no_mutation_results(self) -> None:
        """Historic delivery cannot recreate, revive, or process a finished Job."""

        cases = (
            ([None, None], ADTOFStaleRequestReason.JOB_MISSING),
            ([None, job_row(is_retained=False), None], ADTOFStaleRequestReason.JOB_EXPIRED),
            ([None, job_row(status="completed"), None], ADTOFStaleRequestReason.JOB_TERMINAL),
        )
        for rows, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                cursor = ScriptedCursor(rows)
                result = claim_adtof_task_for_delivery(cursor, message=message())
                self.assertEqual(result.disposition, ADTOFTaskClaimDisposition.STALE)
                self.assertEqual(result.stale_reason, expected_reason)
                self.assertNotIn(INSERT_FIRST_ADTOF_TASK_LEASE_SQL, [call[0] for call in cursor.calls])

    def test_second_lookup_after_job_lock_converges_a_concurrent_first_claim(self) -> None:
        """A task created while this transaction waited is still a duplicate."""

        cursor = ScriptedCursor([None, job_row(), leased_task_row(status="leased")])

        result = claim_adtof_task_for_delivery(cursor, message=message())

        self.assertEqual(result.disposition, ADTOFTaskClaimDisposition.DUPLICATE)
        self.assertEqual(len(cursor.calls), 3)

    def test_durable_outbox_or_existing_task_mismatch_raises_without_insert(self) -> None:
        """Transport evidence must never override PostgreSQL's immutable record."""

        inconsistent_rows = (
            [leased_task_row(request_event_id=OTHER_EVENT_ID)],
            [None, job_row(), None, outbox_row(publication_status="pending")],
            [None, job_row(), None, outbox_row(payload={"unexpected": "shape"})],
        )
        for rows in inconsistent_rows:
            with self.subTest(rows=rows):
                cursor = ScriptedCursor(rows)
                with self.assertRaises(ADTOFTaskClaimInconsistency):
                    claim_adtof_task_for_delivery(cursor, message=message())
                self.assertNotIn(INSERT_FIRST_ADTOF_TASK_LEASE_SQL, [call[0] for call in cursor.calls])

    def test_hand_built_message_or_invalid_lease_is_rejected_before_sql(self) -> None:
        """The claim adapter cannot become a bypass around the strict parser."""

        for invalid_message in (
            message(stem_name="vocals"),
            message(stem_object_key=f"stems/{JOB_ID}/vocals.wav"),
            message(stem_content_length=0),
            message(stem_sha256="not-a-sha256"),
        ):
            with self.subTest(invalid_message=invalid_message):
                cursor = ScriptedCursor([])
                with self.assertRaises(ADTOFTaskClaimProtocolError):
                    claim_adtof_task_for_delivery(cursor, message=invalid_message)
                self.assertEqual(cursor.calls, [])

        cursor = ScriptedCursor([])
        with self.assertRaises(ADTOFTaskClaimProtocolError):
            claim_adtof_task_for_delivery(cursor, message=message(), lease_seconds=True)
        self.assertEqual(cursor.calls, [])


class ADTOFTaskStartTests(unittest.TestCase):
    """Prove only one current verified ADTOF lease can become model-eligible."""

    def test_current_lease_transitions_to_running_with_postgresql_clock_evidence(self) -> None:
        """A returned timestamp is the only successful execution-admission fact."""

        started_at = datetime(2026, 9, 13, 12, 20, tzinfo=UTC)
        cursor = ScriptedCursor([{"started_at": started_at}])

        result = start_leased_adtof_task(cursor, lease=lease())

        self.assertEqual(result, started_at)
        self.assertEqual(cursor.calls, [(START_LEASED_ADTOF_TASK_SQL, (TASK_ID, JOB_ID, LEASE_TOKEN))])
        query = cursor.calls[0][0]
        self.assertIn("status = 'leased'", query)
        self.assertIn("lease_expires_at > CURRENT_TIMESTAMP", query)
        self.assertIn("status = 'running'", query)

    def test_expired_recovered_or_replaced_lease_returns_no_execution_authority(self) -> None:
        """No returned row means the model must not use the downloaded drums WAV."""

        cursor = ScriptedCursor([None])

        result = start_leased_adtof_task(cursor, lease=lease())

        self.assertIsNone(result)
        self.assertEqual(len(cursor.calls), 1)
        self.assertIs(cursor.calls[0][0], START_LEASED_ADTOF_TASK_SQL)

    def test_invalid_direct_lease_is_rejected_before_sql(self) -> None:
        """A constructed lease cannot widen task identity or skip ownership checks."""

        for invalid_lease in (
            lease(stem_name="vocals"),
            lease(input_object_key=f"stems/{JOB_ID}/vocals.wav"),
            lease(stem_mode="2-stems"),
            lease(attempt_count=4),
            lease(lease_token="not-a-uuid"),
        ):
            with self.subTest(lease=invalid_lease):
                cursor = ScriptedCursor([])
                with self.assertRaises(ADTOFTaskClaimProtocolError):
                    start_leased_adtof_task(cursor, lease=invalid_lease)
                self.assertEqual(cursor.calls, [])

    def test_malformed_returned_timestamp_is_not_model_execution_authority(self) -> None:
        """A driver row missing timezone-aware start evidence cannot be accepted."""

        cursor = ScriptedCursor([{"started_at": datetime(2026, 9, 13, 12, 20)}])

        with self.assertRaises(ADTOFTaskClaimProtocolError):
            start_leased_adtof_task(cursor, lease=lease())

        self.assertEqual(len(cursor.calls), 1)


class ADTOFExpiredLeaseRecoveryCandidateTests(unittest.TestCase):
    """Prove recovery gives one expired active task a fresh token, never a task two."""

    def test_expired_candidate_claim_uses_skip_locked_and_returns_a_second_attempt(self) -> None:
        """Two replicas cannot reclaim one task concurrently through this query."""

        cursor = ScriptedCursor([leased_task_row(attempt_count=2)])

        recovered = claim_next_expired_adtof_task(
            cursor,
            uuid_factory=FixedUuidFactory(LEASE_TOKEN),
        )

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered.attempt_count, 2)
        self.assertEqual(recovered.lease_token, LEASE_TOKEN)
        self.assertEqual(
            cursor.calls,
            [(CLAIM_NEXT_EXPIRED_ADTOF_TASK_SQL, (LEASE_TOKEN, DEFAULT_ADTOF_LEASE_SECONDS))],
        )
        self.assertIn("FOR UPDATE SKIP LOCKED", CLAIM_NEXT_EXPIRED_ADTOF_TASK_SQL)
        self.assertIn("status IN ('leased', 'running')", CLAIM_NEXT_EXPIRED_ADTOF_TASK_SQL)
        self.assertIn(f"attempt_count < {MAX_ADTOF_TASK_ATTEMPTS}", CLAIM_NEXT_EXPIRED_ADTOF_TASK_SQL)

    def test_empty_expired_scan_returns_none_without_inventing_work(self) -> None:
        """A later supervisor can treat no recovery candidate as normal idle."""

        cursor = ScriptedCursor([None])

        self.assertIsNone(claim_next_expired_adtof_task(cursor))
        self.assertEqual(len(cursor.calls), 1)

    def test_forged_first_or_exhausted_recovery_return_is_rejected(self) -> None:
        """The pure adapter cannot convert unexpected driver rows into authority."""

        for row in (
            leased_task_row(attempt_count=1),
            leased_task_row(attempt_count=4),
            leased_task_row(lease_token=TASK_ID, attempt_count=2),
        ):
            with self.subTest(row=row):
                cursor = ScriptedCursor([row])
                with self.assertRaises(ADTOFTaskClaimProtocolError):
                    claim_next_expired_adtof_task(
                        cursor,
                        uuid_factory=FixedUuidFactory(LEASE_TOKEN),
                    )


class ADTOFExpiredLeaseTerminalizationTests(unittest.TestCase):
    """Prove a third expired active lease becomes one durable task failure."""

    def test_terminalizes_only_one_expired_active_third_attempt(self) -> None:
        """The SQL lock, final state, and returned evidence are all explicit."""

        cursor = ScriptedCursor([exhausted_terminalization_row()])

        terminalization = finalize_next_expired_exhausted_adtof_task(cursor)

        self.assertEqual(
            terminalization,
            ADTOFExpiredLeaseTerminalization(
                task_id=TASK_ID,
                job_id=JOB_ID,
                stem_name="drums",
                attempt_count=3,
                completed_at=datetime(2026, 9, 13, 12, 30, tzinfo=UTC),
                error_code="lease_expired_attempts_exhausted",
            ),
        )
        self.assertEqual(cursor.calls, [(FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL, ())])
        self.assertIn("attempt_count = 3", FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL)
        self.assertIn("status IN ('leased', 'running')", FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL)
        self.assertIn("lease_expires_at <= CURRENT_TIMESTAMP", FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL)
        self.assertIn("FOR UPDATE SKIP LOCKED", FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL)
        self.assertIn("status = 'failed'", FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL)
        self.assertIn("lease_token = NULL", FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL)
        self.assertIn("lease_expires_at = NULL", FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL)
        self.assertIn("completed_at = CURRENT_TIMESTAMP", FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL)
        self.assertIn("lease_expired_attempts_exhausted", FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL)
        self.assertNotIn("UPDATE public.jobs", FINALIZE_NEXT_EXPIRED_EXHAUSTED_ADTOF_TASK_SQL)

    def test_idle_or_forged_terminalization_row_never_invents_safe_progress(self) -> None:
        """No candidate is idle; a changed query/driver projection is rejected."""

        self.assertIsNone(finalize_next_expired_exhausted_adtof_task(ScriptedCursor([None])))

        for forged_row in (
            exhausted_terminalization_row(attempt_count=2),
            exhausted_terminalization_row(status="running"),
            exhausted_terminalization_row(last_error_code="model_timeout"),
            exhausted_terminalization_row(completed_at=datetime(2026, 9, 13, 12, 30)),
        ):
            with self.subTest(forged_row=forged_row):
                with self.assertRaises(ADTOFTaskClaimProtocolError):
                    finalize_next_expired_exhausted_adtof_task(ScriptedCursor([forged_row]))


if __name__ == "__main__":
    unittest.main()
