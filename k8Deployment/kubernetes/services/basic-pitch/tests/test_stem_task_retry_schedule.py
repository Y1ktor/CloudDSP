"""Unit tests for the pure Basic Pitch pre-model retry-scheduling statement.

The fake cursor records parameterized SQL only.  These tests create no
PostgreSQL/RabbitMQ/MinIO connection, retry loop, model process, container, or
Kubernetes resource.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.stem_task_retry_schedule import (
    DEFAULT_BASIC_PITCH_RETRY_AFTER_SECONDS,
    SCHEDULE_LEASED_BASIC_PITCH_STEM_RETRY_SQL,
    BasicPitchStemRetryScheduleCode,
    BasicPitchStemRetryScheduleProtocolError,
    schedule_leased_basic_pitch_stem_retry,
)
from app.task_lease import MAX_BASIC_PITCH_TASK_ATTEMPTS, BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
AVAILABLE_AT = datetime(2026, 9, 11, 13, 6, tzinfo=UTC)


def lease(**overrides: object) -> BasicPitchTaskLease:
    """Return one current non-drum lease suitable for pre-model retry handling."""

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


class FakeCursor:
    """Return scripted rows and retain every executed parameterized statement."""

    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self.rows = list(rows)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        if not self.rows:
            raise AssertionError("adapter fetched more rows than this test provided")
        return self.rows.pop(0)


class BasicPitchStemRetryScheduleTests(unittest.TestCase):
    """Prove a transient pre-model error releases only a current retryable lease."""

    def test_current_retryable_lease_receives_a_durable_storage_retry_time(self) -> None:
        """The task is deferred without completing it, starting the model, or changing the Job."""

        cursor = FakeCursor(
            [
                {
                    "task_id": TASK_ID,
                    "job_id": JOB_ID,
                    "attempt_count": 1,
                    "available_at": AVAILABLE_AT,
                    "last_error_code": BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE.value,
                }
            ]
        )

        result = schedule_leased_basic_pitch_stem_retry(
            cursor,
            lease=lease(),
            failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.task_id, TASK_ID)
        self.assertEqual(result.job_id, JOB_ID)
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(result.available_at, AVAILABLE_AT)
        self.assertEqual(result.failure_code, BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE)
        self.assertEqual(cursor.calls, [
            (
                SCHEDULE_LEASED_BASIC_PITCH_STEM_RETRY_SQL,
                (
                    DEFAULT_BASIC_PITCH_RETRY_AFTER_SECONDS,
                    BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE.value,
                    TASK_ID,
                    JOB_ID,
                    "vocals",
                    "clouddsp-uploads",
                    f"stems/{JOB_ID}/vocals.wav",
                    "4-stems",
                    MAX_BASIC_PITCH_TASK_ATTEMPTS,
                    LEASE_TOKEN,
                ),
            )
        ])
        self.assertIn("status = 'retry_scheduled'", SCHEDULE_LEASED_BASIC_PITCH_STEM_RETRY_SQL)
        self.assertIn("task.attempt_count < %s::integer", SCHEDULE_LEASED_BASIC_PITCH_STEM_RETRY_SQL)
        self.assertIn("lease_token = NULL", SCHEDULE_LEASED_BASIC_PITCH_STEM_RETRY_SQL)
        self.assertNotIn("completed_at =", SCHEDULE_LEASED_BASIC_PITCH_STEM_RETRY_SQL)
        self.assertNotIn("UPDATE public.jobs", SCHEDULE_LEASED_BASIC_PITCH_STEM_RETRY_SQL)

    def test_no_row_leaves_concurrent_or_exhausted_state_to_its_own_owner(self) -> None:
        """A stale/expired/final-attempt lease cannot manufacture a second retry outcome."""

        cursor = FakeCursor([None])

        result = schedule_leased_basic_pitch_stem_retry(
            cursor,
            lease=lease(attempt_count=MAX_BASIC_PITCH_TASK_ATTEMPTS),
            failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
        )

        self.assertIsNone(result)
        self.assertEqual(len(cursor.calls), 1)

    def test_invalid_lease_delay_or_unreviewed_error_text_never_reaches_sql(self) -> None:
        """A worker cannot persist raw errors or create an unbounded retry loop."""

        cursor = FakeCursor([])
        with self.assertRaises(BasicPitchStemRetryScheduleProtocolError):
            schedule_leased_basic_pitch_stem_retry(
                cursor,
                lease=lease(input_object_key=f"stems/{JOB_ID}/bass.wav"),
                failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
            )
        self.assertEqual(cursor.calls, [])

        with self.assertRaises(BasicPitchStemRetryScheduleProtocolError):
            schedule_leased_basic_pitch_stem_retry(
                cursor,
                lease=lease(),
                retry_after_seconds=0,
                failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
            )
        self.assertEqual(cursor.calls, [])

        with self.assertRaises(BasicPitchStemRetryScheduleProtocolError):
            schedule_leased_basic_pitch_stem_retry(
                cursor,
                lease=lease(),
                failure_code="private raw MinIO error",  # type: ignore[arg-type]
            )
        self.assertEqual(cursor.calls, [])

    def test_mismatched_returned_retry_evidence_is_not_reported_as_durable(self) -> None:
        """Malformed database evidence aborts the outer transaction instead of hiding it."""

        cursor = FakeCursor(
            [
                {
                    "task_id": TASK_ID,
                    "job_id": JOB_ID,
                    "attempt_count": 1,
                    "available_at": AVAILABLE_AT,
                    "last_error_code": "private raw MinIO error",
                }
            ]
        )

        with self.assertRaises(BasicPitchStemRetryScheduleProtocolError):
            schedule_leased_basic_pitch_stem_retry(
                cursor,
                lease=lease(),
                failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
            )


if __name__ == "__main__":
    unittest.main()
