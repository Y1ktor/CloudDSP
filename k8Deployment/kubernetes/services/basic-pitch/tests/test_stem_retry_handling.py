"""Unit tests for the Basic Pitch pre-model retry classifier-to-commit bridge.

No database connection, MinIO request, RabbitMQ action, model process, worker
loop, container, or Kubernetes resource is created. The transaction wrapper is
patched at its public seam so these tests prove only finite control flow.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from app.artifacts.stem_download import BasicPitchStemDownloadUnavailable
from app.artifacts.stem_object import BasicPitchStemStorageUnavailable
from app.runtime.stem_retry_handling import (
    BasicPitchPreModelRetryHandling,
    BasicPitchPreModelRetryHandlingDisposition,
    handle_basic_pitch_pre_model_storage_retry,
)
from app.db.stem_task_retry_schedule import BasicPitchStemRetrySchedule, BasicPitchStemRetryScheduleCode
from app.db.stem_task_retry_exhaustion import (
    BasicPitchStemRetryExhaustion,
    BasicPitchStemRetryExhaustionCode,
)
from app.db.task_lease import MAX_BASIC_PITCH_TASK_ATTEMPTS, BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def lease(**overrides: object) -> BasicPitchTaskLease:
    """Return one valid current pre-model task lease."""

    arguments: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stem_name": "vocals",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"stems/{JOB_ID}/vocals.wav",
        "stem_mode": "4-stems",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": datetime(2026, 9, 11, 13, 15, tzinfo=UTC),
    }
    arguments.update(overrides)
    return BasicPitchTaskLease(**arguments)  # type: ignore[arg-type]


class BasicPitchPreModelRetryHandlingTests(unittest.TestCase):
    """Prove classification and attempt count select exactly one durable branch."""

    @patch("app.runtime.stem_retry_handling.commit_final_attempt_basic_pitch_stem_retry_exhaustion")
    @patch("app.runtime.stem_retry_handling.commit_basic_pitch_stem_retry_schedule")
    def test_reviewed_storage_outage_commits_its_finite_retry_schedule(self, schedule, exhaust) -> None:
        """A first/second outage crosses only the retry-scheduling SQL boundary."""

        database = object()
        expected = BasicPitchStemRetrySchedule(
            task_id=TASK_ID,
            job_id=JOB_ID,
            attempt_count=1,
            failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
            available_at=datetime(2026, 9, 11, 13, 6, tzinfo=UTC),
        )
        schedule.return_value = expected

        result = handle_basic_pitch_pre_model_storage_retry(
            BasicPitchStemDownloadUnavailable("private outage detail"),
            database=database,  # type: ignore[arg-type]
            lease=lease(),
            retry_after_seconds=45,
        )

        self.assertEqual(result.disposition, BasicPitchPreModelRetryHandlingDisposition.RETRY_SCHEDULED)
        self.assertIs(result.retry_schedule, expected)
        self.assertIsNone(result.retry_exhaustion)
        schedule.assert_called_once_with(
            database=database,
            lease=lease(),
            retry_after_seconds=45,
            failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
        )
        exhaust.assert_not_called()

    @patch("app.runtime.stem_retry_handling.commit_final_attempt_basic_pitch_stem_retry_exhaustion")
    @patch("app.runtime.stem_retry_handling.commit_basic_pitch_stem_retry_schedule")
    def test_final_attempt_storage_outage_commits_terminal_exhaustion(self, schedule, exhaust) -> None:
        """The third transient outage cannot silently schedule a fourth attempt."""

        database = object()
        expected = BasicPitchStemRetryExhaustion(
            task_id=TASK_ID,
            job_id=JOB_ID,
            failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
            completed_at=datetime(2026, 9, 11, 13, 5, tzinfo=UTC),
        )
        exhaust.return_value = expected

        result = handle_basic_pitch_pre_model_storage_retry(
            BasicPitchStemStorageUnavailable("private outage detail"),
            database=database,  # type: ignore[arg-type]
            lease=lease(attempt_count=MAX_BASIC_PITCH_TASK_ATTEMPTS),
        )

        self.assertEqual(result.disposition, BasicPitchPreModelRetryHandlingDisposition.RETRY_EXHAUSTED)
        self.assertIsNone(result.retry_schedule)
        self.assertIs(result.retry_exhaustion, expected)
        exhaust.assert_called_once_with(
            database=database,
            lease=lease(attempt_count=MAX_BASIC_PITCH_TASK_ATTEMPTS),
            failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
        )
        schedule.assert_not_called()

    @patch("app.runtime.stem_retry_handling.commit_final_attempt_basic_pitch_stem_retry_exhaustion")
    @patch("app.runtime.stem_retry_handling.commit_basic_pitch_stem_retry_schedule")
    def test_unclassified_error_does_not_open_a_retry_transaction(self, schedule, exhaust) -> None:
        """A later gate can re-raise a model/protocol/database error unchanged."""

        result = handle_basic_pitch_pre_model_storage_retry(
            RuntimeError("private unexpected detail"),
            database=object(),  # type: ignore[arg-type]
            lease=lease(),
        )

        self.assertEqual(result.disposition, BasicPitchPreModelRetryHandlingDisposition.UNCLASSIFIED)
        self.assertIsNone(result.retry_schedule)
        self.assertIsNone(result.retry_exhaustion)
        schedule.assert_not_called()
        exhaust.assert_not_called()

    @patch("app.runtime.stem_retry_handling.commit_final_attempt_basic_pitch_stem_retry_exhaustion")
    @patch("app.runtime.stem_retry_handling.commit_basic_pitch_stem_retry_schedule")
    def test_no_row_result_remains_distinct_from_an_unclassified_error(self, schedule, exhaust) -> None:
        """A guarded miss has no evidence, whether it follows retry or exhaustion selection."""

        schedule.return_value = None

        result = handle_basic_pitch_pre_model_storage_retry(
            BasicPitchStemStorageUnavailable("private outage detail"),
            database=object(),  # type: ignore[arg-type]
            lease=lease(),
        )

        self.assertEqual(result.disposition, BasicPitchPreModelRetryHandlingDisposition.NO_DURABLE_RESULT)
        self.assertIsNone(result.retry_schedule)
        self.assertIsNone(result.retry_exhaustion)
        exhaust.assert_not_called()

        exhaust.return_value = None
        final_result = handle_basic_pitch_pre_model_storage_retry(
            BasicPitchStemStorageUnavailable("private outage detail"),
            database=object(),  # type: ignore[arg-type]
            lease=lease(attempt_count=MAX_BASIC_PITCH_TASK_ATTEMPTS),
        )
        self.assertEqual(final_result.disposition, BasicPitchPreModelRetryHandlingDisposition.NO_DURABLE_RESULT)
        self.assertIsNone(final_result.retry_schedule)
        self.assertIsNone(final_result.retry_exhaustion)

    def test_result_requires_committed_evidence_only_for_scheduled_disposition(self) -> None:
        """Direct construction cannot falsely report either durable outcome."""

        with self.assertRaises(TypeError):
            BasicPitchPreModelRetryHandling(
                disposition=BasicPitchPreModelRetryHandlingDisposition.RETRY_SCHEDULED,
            )
        with self.assertRaises(TypeError):
            BasicPitchPreModelRetryHandling(
                disposition=BasicPitchPreModelRetryHandlingDisposition.UNCLASSIFIED,
                retry_schedule=BasicPitchStemRetrySchedule(
                    task_id=TASK_ID,
                    job_id=JOB_ID,
                    attempt_count=1,
                    failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
                    available_at=datetime(2026, 9, 11, 13, 6, tzinfo=UTC),
                ),
            )
        with self.assertRaises(TypeError):
            BasicPitchPreModelRetryHandling(
                disposition=BasicPitchPreModelRetryHandlingDisposition.RETRY_EXHAUSTED,
            )


if __name__ == "__main__":
    unittest.main()
