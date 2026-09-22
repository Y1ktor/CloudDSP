"""Unit tests for pure Demucs PostgreSQL task-lease SQL decisions.

No test opens PostgreSQL, RabbitMQ, MinIO, Kubernetes, or Demucs. A tiny
dictionary-row cursor records parameterized SQL calls so the tests can prove
duplicate/recovery/lease safety without local cluster state.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from uuid import UUID

from app.demucs_requested_message import DemucsRequestedMessage
from app.task_lease import (
    CLAIM_NEXT_RECOVERABLE_DEMUCS_TASK_SQL,
    COMPLETE_RUNNING_DEMUCS_TASK_SQL,
    DEFAULT_DEMUCS_LEASE_SECONDS,
    DEMUCS_EXHAUSTED_LEASE_ERROR_CODE,
    FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL,
    INSERT_FIRST_DEMUCS_TASK_LEASE_SQL,
    LOCK_EXISTING_DEMUCS_TASK_SQL,
    MAX_DEMUCS_TASK_ATTEMPTS,
    RENEW_DEMUCS_TASK_LEASE_SQL,
    START_LEASED_DEMUCS_TASK_SQL,
    DemucsStaleRequestReason,
    DemucsExpiredLeaseTerminalization,
    DemucsTaskClaimDisposition,
    DemucsTaskClaimInconsistency,
    DemucsTaskLease,
    DemucsTaskLeaseProtocolError,
    claim_demucs_task_for_delivery,
    claim_next_recoverable_demucs_task,
    complete_running_demucs_task,
    finalize_next_expired_exhausted_demucs_task,
    renew_demucs_task_lease,
    start_leased_demucs_task,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
LEASE_EXPIRY = datetime(2026, 9, 8, 12, 15, tzinfo=UTC)
DOWNSTREAM_EVENT_IDS = (
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003",
    "00000000-0000-4000-8000-000000000004",
)


def message() -> DemucsRequestedMessage:
    """Return a parser-validated delivery for claim tests."""

    return DemucsRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        source_bucket="clouddsp-uploads",
        source_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
    )


def job_row(**overrides: object) -> dict[str, object]:
    """Return the authoritative row required for a first valid task claim."""

    row: dict[str, object] = {
        "job_id": JOB_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"uploads/{JOB_ID}/mix.wav",
        "source_uploaded": True,
        "stem_mode": "4-stems",
        "status": "source_uploaded",
        "revision": 7,
        "is_retained": True,
    }
    row.update(overrides)
    return row


def outbox_row(**overrides: object) -> dict[str, object]:
    """Return the published outbox identity the worker must re-check."""

    row: dict[str, object] = {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stage": "demucs",
        "stem_name": "",
        "event_type": "demucs.requested",
        "publication_status": "published",
    }
    row.update(overrides)
    return row


def leased_task_row(**overrides: object) -> dict[str, object]:
    """Return a row shaped like either first-claim or recovery SQL RETURNING."""

    row: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"uploads/{JOB_ID}/mix.wav",
        "stem_mode": "4-stems",
        "status": "leased",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": LEASE_EXPIRY,
    }
    row.update(overrides)
    return row


def lease() -> DemucsTaskLease:
    """Return the already-committed ownership token used by start tests."""

    return DemucsTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=LEASE_EXPIRY,
    )


def expired_terminalization_row(**overrides: object) -> dict[str, object]:
    """Return the compact atomic task/Job result of final-expiry SQL."""

    row: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "attempt_count": 3,
        "completed_at": datetime(2026, 9, 21, 12, 30, tzinfo=UTC),
        "last_error_code": DEMUCS_EXHAUSTED_LEASE_ERROR_CODE,
        "job_revision": 8,
        "job_status": "failed",
    }
    row.update(overrides)
    return row


def downstream_events_document() -> str:
    """Return the exact four-stem downstream fan-out accepted by completion SQL."""

    documents = []
    for event_id, stem_name in zip(DOWNSTREAM_EVENT_IDS, ("drums", "bass", "other", "vocals"), strict=True):
        stage = "adtof" if stem_name == "drums" else "basic-pitch"
        documents.append(
            {
                "event_id": event_id,
                "stage": stage,
                "stem_name": stem_name,
                "event_type": f"{stage}.requested",
                "payload": {
                    "schema_version": 1,
                    "job_id": JOB_ID,
                    "stem_name": stem_name,
                    "stem": {
                        "bucket": "clouddsp-uploads",
                        "object_key": f"stems/{JOB_ID}/{stem_name}.wav",
                        "content_type": "audio/wav",
                        "size_bytes": 100,
                        "sha256": "a" * 64,
                    },
                },
            }
        )
    return json.dumps(documents, sort_keys=True, separators=(",", ":"))


class FakeCursor:
    """Return scripted rows and retain the SQL/parameters supplied by an adapter."""

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
    """Produce predictable application UUIDs while proving they remain UUID objects."""

    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> UUID:
        return UUID(next(self._values))


class FirstClaimTests(unittest.TestCase):
    """Prove an AMQP delivery becomes one durable first task or safe no-op."""

    def test_valid_first_delivery_locks_then_inserts_one_first_lease(self) -> None:
        """The caller can commit this lease before a future AMQP acknowledgement."""

        cursor = FakeCursor([None, job_row(), None, outbox_row(), leased_task_row()])
        factory = FixedUuidFactory(TASK_ID, LEASE_TOKEN)

        result = claim_demucs_task_for_delivery(cursor, message=message(), uuid_factory=factory)

        self.assertEqual(result.disposition, DemucsTaskClaimDisposition.CLAIMED)
        self.assertIsNotNone(result.lease)
        assert result.lease is not None
        self.assertEqual(result.lease.task_id, TASK_ID)
        self.assertEqual(result.lease.lease_token, LEASE_TOKEN)
        self.assertEqual(result.lease.attempt_count, 1)
        self.assertEqual(len(cursor.calls), 5)
        self.assertEqual(cursor.calls[0][0], LOCK_EXISTING_DEMUCS_TASK_SQL)
        self.assertEqual(cursor.calls[2][0], LOCK_EXISTING_DEMUCS_TASK_SQL)
        self.assertEqual(cursor.calls[4][0], INSERT_FIRST_DEMUCS_TASK_LEASE_SQL)
        self.assertEqual(
            cursor.calls[4][1],
            (
                TASK_ID,
                JOB_ID,
                EVENT_ID,
                "clouddsp-uploads",
                f"uploads/{JOB_ID}/mix.wav",
                "4-stems",
                LEASE_TOKEN,
                DEFAULT_DEMUCS_LEASE_SECONDS,
            ),
        )

    def test_existing_task_with_same_event_is_duplicate_and_is_not_mutated(self) -> None:
        """At-least-once broker redelivery cannot create a second logical task."""

        cursor = FakeCursor([leased_task_row(status="running")])

        result = claim_demucs_task_for_delivery(cursor, message=message())

        self.assertEqual(result.disposition, DemucsTaskClaimDisposition.DUPLICATE)
        self.assertEqual(result.duplicate_status, "running")
        self.assertIsNone(result.lease)
        self.assertEqual(len(cursor.calls), 1)

    def test_existing_task_with_different_event_is_not_treated_as_safe_duplicate(self) -> None:
        """Corrupt/mismatched durable identity requires later terminal handling."""

        cursor = FakeCursor([leased_task_row(request_event_id=TASK_ID)])

        with self.assertRaises(DemucsTaskClaimInconsistency):
            claim_demucs_task_for_delivery(cursor, message=message())

    def test_missing_expired_or_terminal_job_is_safe_stale_delivery(self) -> None:
        """Historic broker redelivery does not recreate removed/terminal work."""

        cases = (
            ([None, None], DemucsStaleRequestReason.JOB_MISSING),
            # A present Job is followed by a second no-row task read after its
            # lock. That repeated read is intentional race protection, even
            # when the locked Job is about to classify as stale.
            ([None, job_row(is_retained=False), None], DemucsStaleRequestReason.JOB_EXPIRED),
            ([None, job_row(status="failed"), None], DemucsStaleRequestReason.JOB_TERMINAL),
        )
        for rows, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                cursor = FakeCursor(rows)
                result = claim_demucs_task_for_delivery(cursor, message=message())
                self.assertEqual(result.disposition, DemucsTaskClaimDisposition.STALE)
                self.assertEqual(result.stale_reason, expected_reason)

    def test_job_and_outbox_disagreement_never_becomes_a_safe_claim(self) -> None:
        """The later result adapter must durably resolve these suspicious cases."""

        cases = (
            [None, job_row(input_object_key=f"uploads/{JOB_ID}/other.wav"), None],
            [None, job_row(), None, outbox_row(publication_status="pending")],
        )
        for rows in cases:
            with self.subTest(rows=len(rows)):
                with self.assertRaises(DemucsTaskClaimInconsistency):
                    claim_demucs_task_for_delivery(FakeCursor(rows), message=message())

    def test_invalid_lease_duration_or_returned_row_fails_without_hidden_coercion(self) -> None:
        """A malformed Deployment setting/database return cannot silently own work."""

        with self.assertRaises(DemucsTaskLeaseProtocolError):
            claim_demucs_task_for_delivery(FakeCursor([]), message=message(), lease_seconds=59)

        cursor = FakeCursor([None, job_row(), None, outbox_row(), leased_task_row(lease_token=TASK_ID)])
        with self.assertRaises(DemucsTaskLeaseProtocolError):
            claim_demucs_task_for_delivery(
                cursor,
                message=message(),
                uuid_factory=FixedUuidFactory(TASK_ID, LEASE_TOKEN),
            )


class RecoveryAndRenewalTests(unittest.TestCase):
    """Prove one replica owns due recovery and stale workers cannot renew."""

    def test_recovery_claim_uses_skip_locked_query_and_increments_existing_attempt(self) -> None:
        """A retry/expired lease gets one new token, never a second task row."""

        cursor = FakeCursor([leased_task_row(attempt_count=2)])

        lease = claim_next_recoverable_demucs_task(
            cursor,
            uuid_factory=FixedUuidFactory(LEASE_TOKEN),
        )

        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual(lease.attempt_count, 2)
        self.assertEqual(lease.lease_token, LEASE_TOKEN)
        self.assertEqual(cursor.calls[0][0], CLAIM_NEXT_RECOVERABLE_DEMUCS_TASK_SQL)
        self.assertEqual(cursor.calls[0][1], (LEASE_TOKEN, DEFAULT_DEMUCS_LEASE_SECONDS))
        self.assertIn("FOR UPDATE SKIP LOCKED", CLAIM_NEXT_RECOVERABLE_DEMUCS_TASK_SQL)
        self.assertIn(f"attempt_count < {MAX_DEMUCS_TASK_ATTEMPTS}", CLAIM_NEXT_RECOVERABLE_DEMUCS_TASK_SQL)

    def test_empty_recovery_scan_returns_none_without_inventing_work(self) -> None:
        """An idle worker should sleep later, rather than synthesize an AMQP task."""

        cursor = FakeCursor([None])

        self.assertIsNone(claim_next_recoverable_demucs_task(cursor))
        self.assertEqual(len(cursor.calls), 1)

    def test_renewal_is_guarded_by_current_token_active_state_and_unexpired_lease(self) -> None:
        """A recovered/stale Pod sees None and must stop processing immediately."""

        renewed_expiry = datetime(2026, 9, 8, 12, 30, tzinfo=UTC)
        cursor = FakeCursor([{"lease_expires_at": renewed_expiry}])

        returned = renew_demucs_task_lease(cursor, task_id=TASK_ID, lease_token=LEASE_TOKEN)

        self.assertEqual(returned, renewed_expiry)
        self.assertEqual(cursor.calls[0][0], RENEW_DEMUCS_TASK_LEASE_SQL)
        self.assertEqual(
            cursor.calls[0][1],
            (DEFAULT_DEMUCS_LEASE_SECONDS, TASK_ID, LEASE_TOKEN),
        )
        self.assertIn("lease_token = %s::uuid", RENEW_DEMUCS_TASK_LEASE_SQL)
        self.assertIn("lease_expires_at > CURRENT_TIMESTAMP", RENEW_DEMUCS_TASK_LEASE_SQL)

        stale_cursor = FakeCursor([None])
        self.assertIsNone(renew_demucs_task_lease(stale_cursor, task_id=TASK_ID, lease_token=LEASE_TOKEN))


class ExpiredLeaseTerminalizationTests(unittest.TestCase):
    """Prove a third expired active lease becomes one atomic Demucs/Job failure."""

    def test_terminalizes_one_expired_final_attempt_with_job_state(self) -> None:
        """The locked candidate, task, and Job must report one matching result."""

        cursor = FakeCursor([expired_terminalization_row()])

        terminalization = finalize_next_expired_exhausted_demucs_task(cursor)

        self.assertEqual(
            terminalization,
            DemucsExpiredLeaseTerminalization(
                task_id=TASK_ID,
                job_id=JOB_ID,
                attempt_count=MAX_DEMUCS_TASK_ATTEMPTS,
                completed_at=datetime(2026, 9, 21, 12, 30, tzinfo=UTC),
                job_revision=8,
                error_code=DEMUCS_EXHAUSTED_LEASE_ERROR_CODE,
            ),
        )
        self.assertEqual(cursor.calls, [(FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL, ())])
        self.assertIn("attempt_count = 3", FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL)
        self.assertIn("status IN ('leased', 'running')", FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL)
        self.assertIn("lease_expires_at <= CURRENT_TIMESTAMP", FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL)
        self.assertIn("FOR UPDATE SKIP LOCKED", FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL)
        self.assertIn("UPDATE public.jobs AS job", FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL)
        # The preceding `failed_task` CTE exposes its UUID as canonical text
        # for the Python adapter. PostgreSQL does not implicitly compare that
        # text to the UUID jobs primary key, so this cast is required even for
        # an idle recovery scan. Its regression guard prevents a seemingly
        # harmless formatting edit from blocking all later AMQP polling.
        self.assertIn(
            "AND job.job_id = failed_task.job_id::uuid",
            FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL,
        )
        # The fixed task-side error code is sufficient terminal evidence. The
        # worker must not gain SELECT access to arbitrary Job error history
        # merely to return a redundant literal written by this same statement.
        self.assertNotIn("job.error_message", FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL)
        self.assertIn("status = 'failed'", FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL)
        self.assertIn("lease_token = NULL", FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL)
        self.assertIn("lease_expires_at = NULL", FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL)
        self.assertIn(DEMUCS_EXHAUSTED_LEASE_ERROR_CODE, FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL)

    def test_idle_or_forged_result_cannot_invent_terminal_progress(self) -> None:
        """No candidate is normal idle; every returned proof remains strict."""

        self.assertIsNone(finalize_next_expired_exhausted_demucs_task(FakeCursor([None])))

        for row in (
            expired_terminalization_row(attempt_count=2),
            expired_terminalization_row(last_error_code="demucs_process_failure_retry_exhausted"),
            expired_terminalization_row(job_status="source_uploaded"),
            expired_terminalization_row(completed_at=datetime(2026, 9, 21, 12, 30)),
        ):
            with self.subTest(row=row):
                with self.assertRaises(DemucsTaskLeaseProtocolError):
                    finalize_next_expired_exhausted_demucs_task(FakeCursor([row]))


class TaskStartTests(unittest.TestCase):
    """Prove only the still-current preflight lease may start the model stage."""

    def test_current_leased_token_moves_to_running_after_preflight(self) -> None:
        """A committed start timestamp is required before later model work."""

        started_at = datetime(2026, 9, 8, 12, 20, tzinfo=UTC)
        cursor = FakeCursor([{"started_at": started_at}])

        returned = start_leased_demucs_task(cursor, lease=lease())

        self.assertEqual(returned, started_at)
        self.assertEqual(cursor.calls[0][0], START_LEASED_DEMUCS_TASK_SQL)
        self.assertEqual(cursor.calls[0][1], (TASK_ID, JOB_ID, LEASE_TOKEN))
        self.assertIn("status = 'leased'", START_LEASED_DEMUCS_TASK_SQL)
        self.assertIn("status = 'running'", START_LEASED_DEMUCS_TASK_SQL)
        self.assertIn("lease_token = %s::uuid", START_LEASED_DEMUCS_TASK_SQL)
        self.assertIn("lease_expires_at > CURRENT_TIMESTAMP", START_LEASED_DEMUCS_TASK_SQL)

    def test_lost_or_expired_ownership_returns_none_before_model_work(self) -> None:
        """A recovery replica can revoke the token without a stale overwrite."""

        cursor = FakeCursor([None])

        self.assertIsNone(start_leased_demucs_task(cursor, lease=lease()))
        self.assertEqual(len(cursor.calls), 1)

    def test_invalid_returned_start_timestamp_is_not_treated_as_permission(self) -> None:
        """An uncertain driver row must abort the caller's transaction scope."""

        cursor = FakeCursor([{"started_at": "not-a-timestamp"}])

        with self.assertRaises(DemucsTaskLeaseProtocolError):
            start_leased_demucs_task(cursor, lease=lease())


class TaskCompletionTests(unittest.TestCase):
    """Prove only one current running lease may atomically publish stem state."""

    def test_current_running_lease_updates_task_and_job_with_one_guarded_statement(self) -> None:
        """A complete receipt map is durable only after the task/job CTE succeeds."""

        completed_at = datetime(2026, 9, 8, 12, 35, tzinfo=UTC)
        cursor = FakeCursor(
            [
                {
                    "task_id": TASK_ID,
                    "job_id": JOB_ID,
                    "completed_at": completed_at,
                    "job_revision": 8,
                    "outbox_event_count": 4,
                }
            ]
        )
        stems_document = '{"drums":{"s3_key":"stems/example/drums.wav","status":"ready"}}'
        events_document = downstream_events_document()

        completion = complete_running_demucs_task(
            cursor,
            lease=lease(),
            stems_document=stems_document,
            downstream_events_document=events_document,
        )

        self.assertEqual(
            completion,
            type(completion)(
                task_id=TASK_ID,
                job_id=JOB_ID,
                completed_at=completed_at,
                job_revision=8,
                outbox_event_count=4,
            ),
        )
        self.assertEqual(cursor.calls[0][0], COMPLETE_RUNNING_DEMUCS_TASK_SQL)
        self.assertEqual(
            cursor.calls[0][1],
            (
                JOB_ID,
                "4-stems",
                TASK_ID,
                JOB_ID,
                LEASE_TOKEN,
                stems_document,
                events_document,
            ),
        )
        self.assertIn("status = 'running'", COMPLETE_RUNNING_DEMUCS_TASK_SQL)
        self.assertIn("lease_token = %s::uuid", COMPLETE_RUNNING_DEMUCS_TASK_SQL)
        self.assertIn("lease_expires_at > CURRENT_TIMESTAMP", COMPLETE_RUNNING_DEMUCS_TASK_SQL)
        self.assertIn("status = 'midi_processing'", COMPLETE_RUNNING_DEMUCS_TASK_SQL)
        # The task CTE intentionally returns canonical text for the Python
        # adapter. PostgreSQL still needs an explicit conversion when the
        # next CTE compares that value with the native UUID `jobs.job_id`.
        self.assertIn("job.job_id = completed_task.job_id::uuid", COMPLETE_RUNNING_DEMUCS_TASK_SQL)
        self.assertIn("INSERT INTO public.outbox_events", COMPLETE_RUNNING_DEMUCS_TASK_SQL)

    def test_ownership_loss_returns_none_without_inventing_success(self) -> None:
        """An expired/recovered/deleted Job leaves both durable rows untouched."""

        cursor = FakeCursor([None])

        self.assertIsNone(
            complete_running_demucs_task(
                cursor,
                lease=lease(),
                stems_document="{}",
                downstream_events_document=downstream_events_document(),
            )
        )
        self.assertEqual(len(cursor.calls), 1)

    def test_invalid_document_or_returned_row_cannot_become_task_completion(self) -> None:
        """The SQL boundary rejects unsafe caller input and driver result shapes."""

        with self.assertRaises(DemucsTaskLeaseProtocolError):
            complete_running_demucs_task(
                FakeCursor([]),
                lease=lease(),
                stems_document="",
                downstream_events_document=downstream_events_document(),
            )

        cursor = FakeCursor(
            [
                {
                    "task_id": TASK_ID,
                    "job_id": JOB_ID,
                    "completed_at": "not-a-timestamp",
                    "job_revision": 8,
                    "outbox_event_count": 4,
                }
            ]
        )
        with self.assertRaises(DemucsTaskLeaseProtocolError):
            complete_running_demucs_task(
                cursor,
                lease=lease(),
                stems_document="{}",
                downstream_events_document=downstream_events_document(),
            )

        # A complete Job/stem update is never permitted to omit a routed next
        # stage. This rejection happens before the SQL boundary can execute.
        with self.assertRaises(DemucsTaskLeaseProtocolError):
            complete_running_demucs_task(
                FakeCursor([]),
                lease=lease(),
                stems_document="{}",
                downstream_events_document="[]",
            )

        mismatched_count_cursor = FakeCursor(
            [
                {
                    "task_id": TASK_ID,
                    "job_id": JOB_ID,
                    "completed_at": LEASE_EXPIRY,
                    "job_revision": 8,
                    "outbox_event_count": 3,
                }
            ]
        )
        with self.assertRaises(DemucsTaskLeaseProtocolError):
            complete_running_demucs_task(
                mismatched_count_cursor,
                lease=lease(),
                stems_document="{}",
                downstream_events_document=downstream_events_document(),
            )


if __name__ == "__main__":
    unittest.main()
