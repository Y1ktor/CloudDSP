"""Unit tests for one guarded Demucs source-failure PostgreSQL decision.

No test opens a database connection, catches a runtime exception, contacts
MinIO/RabbitMQ, starts Demucs, sleeps, creates a container, or changes a
Kubernetes resource. A recording cursor proves only the SQL boundary's safe
parameter choices and returned-evidence validation.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.db.pre_model_failure_transition import (
    DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
    FAIL_LEASED_DEMUCS_PRE_MODEL_TASK_AND_JOB_SQL,
    SCHEDULE_LEASED_DEMUCS_PRE_MODEL_RETRY_SQL,
    DemucsPreModelFailureTransitionDisposition,
    DemucsPreModelRetryExhaustionCode,
    transition_leased_demucs_pre_model_failure,
)
from app.runtime.source_failure_classification import (
    DemucsPreModelFailureClassification,
    DemucsPreModelFailureDisposition,
    DemucsPreModelRetryCode,
    DemucsPreModelTerminalFailureCode,
)
from app.db.task_lease import DemucsTaskLease


TASK_ID = "00000000-0000-4000-8000-000000000001"
JOB_ID = "00000000-0000-4000-8000-000000000002"
EVENT_ID = "00000000-0000-4000-8000-000000000003"
LEASE_TOKEN = "00000000-0000-4000-8000-000000000004"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class RecordingCursor:
    """Minimal dictionary-row cursor that records one pure SQL operation."""

    def __init__(self, row: dict[str, object] | None) -> None:
        """Store the exact database row the test wants the adapter to inspect."""

        self.row = row
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        """Record parameters rather than contacting a PostgreSQL server."""

        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        """Return the configured fake result after the one SQL statement."""

        return self.row


def lease(*, attempt_count: int) -> DemucsTaskLease:
    """Build one complete current source lease with canonical private coordinates."""

    return DemucsTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
        attempt_count=attempt_count,
        lease_token=LEASE_TOKEN,
        lease_expires_at=NOW + timedelta(minutes=15),
    )


def retry_classification() -> DemucsPreModelFailureClassification:
    """Return the only approved temporary source-storage classification."""

    return DemucsPreModelFailureClassification(
        disposition=DemucsPreModelFailureDisposition.RETRY_SCHEDULED,
        retry_code=DemucsPreModelRetryCode.STORAGE_UNAVAILABLE,
    )


def terminal_classification() -> DemucsPreModelFailureClassification:
    """Return one reviewed immutable source-rejection classification."""

    return DemucsPreModelFailureClassification(
        disposition=DemucsPreModelFailureDisposition.TERMINAL_FAILURE,
        terminal_failure_code=DemucsPreModelTerminalFailureCode.METADATA_MISMATCH,
    )


class DemucsPreModelFailureTransitionTests(unittest.TestCase):
    """Prove retry, terminal, exhaustion, race, and invalid-decision behavior."""

    def test_first_or_second_storage_outage_schedules_one_due_retry(self) -> None:
        """The task stays non-terminal and PostgreSQL provides its retry time."""

        available_at = NOW + timedelta(seconds=DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS)
        cursor = RecordingCursor(
            {
                "task_id": TASK_ID,
                "job_id": JOB_ID,
                "attempt_count": 2,
                "available_at": available_at,
                "last_error_code": DemucsPreModelRetryCode.STORAGE_UNAVAILABLE.value,
            }
        )

        result = transition_leased_demucs_pre_model_failure(
            cursor,  # type: ignore[arg-type]
            lease=lease(attempt_count=2),
            classification=retry_classification(),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.disposition, DemucsPreModelFailureTransitionDisposition.RETRY_SCHEDULED)
        self.assertEqual(result.retry_schedule.attempt_count, 2)  # type: ignore[union-attr]
        self.assertIsNone(result.terminal_failure)
        self.assertEqual(len(cursor.calls), 1)
        query, params = cursor.calls[0]
        self.assertEqual(query, SCHEDULE_LEASED_DEMUCS_PRE_MODEL_RETRY_SQL)
        self.assertEqual(
            params,
            (
                JOB_ID,
                DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
                DemucsPreModelRetryCode.STORAGE_UNAVAILABLE.value,
                TASK_ID,
                "clouddsp-uploads",
                f"uploads/{JOB_ID}/mix.wav",
                "4-stems",
                2,
                3,
                LEASE_TOKEN,
            ),
        )

    def test_immutable_source_rejection_fails_task_and_job_atomically(self) -> None:
        """A proven mismatch is terminal on the first attempt and bumps Job revision."""

        cursor = RecordingCursor(
            {
                "task_id": TASK_ID,
                "job_id": JOB_ID,
                "attempt_count": 1,
                "completed_at": NOW,
                "last_error_code": DemucsPreModelTerminalFailureCode.METADATA_MISMATCH.value,
                "job_revision": 8,
                "job_status": "failed",
                "error_message": DemucsPreModelTerminalFailureCode.METADATA_MISMATCH.value,
            }
        )

        result = transition_leased_demucs_pre_model_failure(
            cursor,  # type: ignore[arg-type]
            lease=lease(attempt_count=1),
            classification=terminal_classification(),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.disposition, DemucsPreModelFailureTransitionDisposition.TERMINAL_FAILURE)
        self.assertEqual(
            result.terminal_failure.failure_code,  # type: ignore[union-attr]
            DemucsPreModelTerminalFailureCode.METADATA_MISMATCH,
        )
        self.assertIsNone(result.retry_schedule)
        self.assertEqual(len(cursor.calls), 1)
        query, params = cursor.calls[0]
        self.assertEqual(query, FAIL_LEASED_DEMUCS_PRE_MODEL_TASK_AND_JOB_SQL)
        self.assertEqual(
            params,
            (
                JOB_ID,
                DemucsPreModelTerminalFailureCode.METADATA_MISMATCH.value,
                TASK_ID,
                "clouddsp-uploads",
                f"uploads/{JOB_ID}/mix.wav",
                "4-stems",
                1,
                LEASE_TOKEN,
                DemucsPreModelTerminalFailureCode.METADATA_MISMATCH.value,
            ),
        )
        self.assertIn("UPDATE public.processing_tasks", query)
        self.assertIn("UPDATE public.jobs", query)

    def test_third_storage_outage_is_terminal_retry_exhaustion(self) -> None:
        """The attempt cap never permits a fourth Demucs source-storage lease."""

        cursor = RecordingCursor(
            {
                "task_id": TASK_ID,
                "job_id": JOB_ID,
                "attempt_count": 3,
                "completed_at": NOW,
                "last_error_code": DemucsPreModelRetryExhaustionCode.STORAGE_UNAVAILABLE.value,
                "job_revision": 9,
                "job_status": "failed",
                "error_message": DemucsPreModelRetryExhaustionCode.STORAGE_UNAVAILABLE.value,
            }
        )

        result = transition_leased_demucs_pre_model_failure(
            cursor,  # type: ignore[arg-type]
            lease=lease(attempt_count=3),
            classification=retry_classification(),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.disposition, DemucsPreModelFailureTransitionDisposition.TERMINAL_FAILURE)
        self.assertEqual(
            result.terminal_failure.failure_code,  # type: ignore[union-attr]
            DemucsPreModelRetryExhaustionCode.STORAGE_UNAVAILABLE,
        )
        self.assertEqual(cursor.calls[0][0], FAIL_LEASED_DEMUCS_PRE_MODEL_TASK_AND_JOB_SQL)
        self.assertIn(DemucsPreModelRetryExhaustionCode.STORAGE_UNAVAILABLE.value, cursor.calls[0][1])

    def test_no_row_is_normal_ownership_or_job_state_loss(self) -> None:
        """A stale worker stops without inventing retry or terminal evidence."""

        cursor = RecordingCursor(None)

        self.assertIsNone(
            transition_leased_demucs_pre_model_failure(
                cursor,  # type: ignore[arg-type]
                lease=lease(attempt_count=1),
                classification=terminal_classification(),
            )
        )
        self.assertEqual(len(cursor.calls), 1)

    def test_unclassified_error_cannot_issue_sql(self) -> None:
        """An unrelated process/protocol exception must retain its own policy."""

        cursor = RecordingCursor(None)
        unclassified = DemucsPreModelFailureClassification(
            disposition=DemucsPreModelFailureDisposition.UNCLASSIFIED,
        )

        with self.assertRaisesRegex(RuntimeError, "pre-model failure transition is invalid"):
            transition_leased_demucs_pre_model_failure(
                cursor,  # type: ignore[arg-type]
                lease=lease(attempt_count=1),
                classification=unclassified,
            )

        self.assertEqual(cursor.calls, [])


if __name__ == "__main__":
    unittest.main()
