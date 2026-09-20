"""Tests for guarded post-``running`` Demucs retry/terminal SQL decisions.

The recording cursor makes no PostgreSQL connection. The tests create no
MinIO/RabbitMQ/model/worker/Kubernetes action; they prove only the finite
cursor parameters, final-attempt behavior, and no-row safety of this adapter.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.running_failure_classification import (
    DemucsRunningFailureClassification,
    DemucsRunningFailureDisposition,
    DemucsRunningRetryCode,
    DemucsRunningRetryExhaustionCode,
)
from app.running_failure_transition import (
    DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
    FAIL_FINAL_ATTEMPT_RUNNING_DEMUCS_TASK_AND_JOB_SQL,
    SCHEDULE_RUNNING_DEMUCS_TASK_RETRY_SQL,
    DemucsRunningFailureTransitionDisposition,
    transition_running_demucs_failure,
)
from app.task_lease import DemucsTaskLease


TASK_ID = "00000000-0000-4000-8000-000000000001"
JOB_ID = "00000000-0000-4000-8000-000000000002"
EVENT_ID = "00000000-0000-4000-8000-000000000003"
LEASE_TOKEN = "00000000-0000-4000-8000-000000000004"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class RecordingCursor:
    """Minimal dictionary-row cursor that records the one SQL decision."""

    def __init__(self, row: dict[str, object] | None) -> None:
        """Store one predeclared PostgreSQL result for adapter validation."""

        self.row = row
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        """Record parameterized SQL rather than contacting PostgreSQL."""

        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        """Return the configured one-row transition result."""

        return self.row


def lease(*, attempt_count: int) -> DemucsTaskLease:
    """Build one current post-model lease with the reviewed source coordinate."""

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


def retry_classification() -> DemucsRunningFailureClassification:
    """Return a reviewed transient post-model MinIO failure category."""

    return DemucsRunningFailureClassification(
        disposition=DemucsRunningFailureDisposition.RETRY_SCHEDULED,
        retry_code=DemucsRunningRetryCode.ARTIFACT_STORAGE_UNAVAILABLE,
    )


class DemucsRunningFailureTransitionTests(unittest.TestCase):
    """Prove retry, final exhaustion, race, and unclassified guards."""

    def test_non_final_running_failure_schedules_postgresql_retry(self) -> None:
        """The model-started task becomes recoverable without changing its Job."""

        cursor = RecordingCursor(
            {
                "task_id": TASK_ID,
                "job_id": JOB_ID,
                "attempt_count": 2,
                "available_at": NOW + timedelta(seconds=30),
                "last_error_code": DemucsRunningRetryCode.ARTIFACT_STORAGE_UNAVAILABLE.value,
            }
        )

        result = transition_running_demucs_failure(
            cursor,  # type: ignore[arg-type]
            lease=lease(attempt_count=2),
            classification=retry_classification(),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.disposition, DemucsRunningFailureTransitionDisposition.RETRY_SCHEDULED)
        self.assertEqual(result.retry_schedule.attempt_count, 2)  # type: ignore[union-attr]
        self.assertEqual(len(cursor.calls), 1)
        query, params = cursor.calls[0]
        self.assertEqual(query, SCHEDULE_RUNNING_DEMUCS_TASK_RETRY_SQL)
        self.assertEqual(
            params,
            (
                JOB_ID,
                DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
                DemucsRunningRetryCode.ARTIFACT_STORAGE_UNAVAILABLE.value,
                TASK_ID,
                "clouddsp-uploads",
                f"uploads/{JOB_ID}/mix.wav",
                "4-stems",
                2,
                3,
                LEASE_TOKEN,
            ),
        )
        self.assertIn("task.status = 'running'", query)

    def test_final_running_failure_fails_task_and_job_with_paired_code(self) -> None:
        """Attempt three is terminal and cannot create a fourth Demucs lease."""

        cursor = RecordingCursor(
            {
                "task_id": TASK_ID,
                "job_id": JOB_ID,
                "attempt_count": 3,
                "completed_at": NOW,
                "last_error_code": DemucsRunningRetryExhaustionCode.ARTIFACT_STORAGE_UNAVAILABLE.value,
                "job_revision": 8,
                "job_status": "failed",
                "error_message": DemucsRunningRetryExhaustionCode.ARTIFACT_STORAGE_UNAVAILABLE.value,
            }
        )

        result = transition_running_demucs_failure(
            cursor,  # type: ignore[arg-type]
            lease=lease(attempt_count=3),
            classification=retry_classification(),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.disposition, DemucsRunningFailureTransitionDisposition.TERMINAL_FAILURE)
        self.assertEqual(
            result.retry_exhaustion.failure_code,  # type: ignore[union-attr]
            DemucsRunningRetryExhaustionCode.ARTIFACT_STORAGE_UNAVAILABLE,
        )
        query, params = cursor.calls[0]
        self.assertEqual(query, FAIL_FINAL_ATTEMPT_RUNNING_DEMUCS_TASK_AND_JOB_SQL)
        self.assertIn("UPDATE public.processing_tasks", query)
        self.assertIn("UPDATE public.jobs", query)
        self.assertIn(DemucsRunningRetryExhaustionCode.ARTIFACT_STORAGE_UNAVAILABLE.value, params)

    def test_no_row_is_normal_running_lease_or_job_state_loss(self) -> None:
        """A stale worker returns no evidence and cannot overwrite recovery."""

        cursor = RecordingCursor(None)

        self.assertIsNone(
            transition_running_demucs_failure(
                cursor,  # type: ignore[arg-type]
                lease=lease(attempt_count=1),
                classification=retry_classification(),
            )
        )
        self.assertEqual(len(cursor.calls), 1)

    def test_unclassified_error_issues_no_sql(self) -> None:
        """Image/database/contract faults must preserve their separate policy."""

        cursor = RecordingCursor(None)
        unclassified = DemucsRunningFailureClassification(
            disposition=DemucsRunningFailureDisposition.UNCLASSIFIED,
        )

        with self.assertRaisesRegex(RuntimeError, "running-failure transition is invalid"):
            transition_running_demucs_failure(
                cursor,  # type: ignore[arg-type]
                lease=lease(attempt_count=1),
                classification=unclassified,
            )

        self.assertEqual(cursor.calls, [])


if __name__ == "__main__":
    unittest.main()
