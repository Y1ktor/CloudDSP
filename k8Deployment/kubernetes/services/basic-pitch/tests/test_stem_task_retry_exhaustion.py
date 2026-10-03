"""Unit tests for the pure Basic Pitch final-attempt retry-exhaustion statement.

The fake cursor records parameterized SQL only. These tests create no
PostgreSQL/RabbitMQ/MinIO connection, worker loop, model process, container,
or Kubernetes resource.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.db.stem_task_retry_exhaustion import (
    FAIL_FINAL_ATTEMPT_LEASED_BASIC_PITCH_STEM_SQL,
    BasicPitchStemRetryExhaustionCode,
    BasicPitchStemRetryExhaustionProtocolError,
    fail_final_attempt_leased_basic_pitch_stem,
)
from app.db.task_lease import MAX_BASIC_PITCH_TASK_ATTEMPTS, BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
COMPLETED_AT = datetime(2026, 9, 11, 13, 5, tzinfo=UTC)


def lease(**overrides: object) -> BasicPitchTaskLease:
    """Return one final-attempt non-drum lease suitable for exhaustion handling."""

    arguments: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stem_name": "vocals",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"stems/{JOB_ID}/vocals.wav",
        "stem_mode": "4-stems",
        "attempt_count": MAX_BASIC_PITCH_TASK_ATTEMPTS,
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


class BasicPitchStemRetryExhaustionTests(unittest.TestCase):
    """Prove only a current third storage attempt can become this terminal result."""

    def test_current_final_attempt_becomes_terminal_with_exact_exhaustion_code(self) -> None:
        """The task is final, but the overall Job remains an aggregate concern."""

        cursor = FakeCursor(
            [
                {
                    "task_id": TASK_ID,
                    "job_id": JOB_ID,
                    "attempt_count": MAX_BASIC_PITCH_TASK_ATTEMPTS,
                    "completed_at": COMPLETED_AT,
                    "last_error_code": BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE.value,
                }
            ]
        )

        result = fail_final_attempt_leased_basic_pitch_stem(
            cursor,
            lease=lease(),
            failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.task_id, TASK_ID)
        self.assertEqual(result.job_id, JOB_ID)
        self.assertEqual(result.completed_at, COMPLETED_AT)
        self.assertEqual(result.failure_code, BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE)
        self.assertEqual(cursor.calls, [
            (
                FAIL_FINAL_ATTEMPT_LEASED_BASIC_PITCH_STEM_SQL,
                (
                    BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE.value,
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
        self.assertIn("status = 'failed'", FAIL_FINAL_ATTEMPT_LEASED_BASIC_PITCH_STEM_SQL)
        self.assertIn("task.attempt_count = %s::integer", FAIL_FINAL_ATTEMPT_LEASED_BASIC_PITCH_STEM_SQL)
        self.assertIn("completed_at = CURRENT_TIMESTAMP", FAIL_FINAL_ATTEMPT_LEASED_BASIC_PITCH_STEM_SQL)
        self.assertNotIn("UPDATE public.jobs", FAIL_FINAL_ATTEMPT_LEASED_BASIC_PITCH_STEM_SQL)

    def test_stale_or_competing_final_attempt_returns_none_without_a_second_outcome(self) -> None:
        """A retry-exhausted worker cannot overwrite recovery or another owner."""

        cursor = FakeCursor([None])

        result = fail_final_attempt_leased_basic_pitch_stem(
            cursor,
            lease=lease(),
            failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
        )

        self.assertIsNone(result)
        self.assertEqual(len(cursor.calls), 1)

    def test_non_final_attempt_or_unreviewed_error_text_never_reaches_sql(self) -> None:
        """Early attempts must use retry scheduling rather than terminal exhaustion."""

        cursor = FakeCursor([])
        with self.assertRaises(BasicPitchStemRetryExhaustionProtocolError):
            fail_final_attempt_leased_basic_pitch_stem(
                cursor,
                lease=lease(attempt_count=MAX_BASIC_PITCH_TASK_ATTEMPTS - 1),
                failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
            )
        self.assertEqual(cursor.calls, [])

        with self.assertRaises(BasicPitchStemRetryExhaustionProtocolError):
            fail_final_attempt_leased_basic_pitch_stem(
                cursor,
                lease=lease(),
                failure_code="private raw MinIO error",  # type: ignore[arg-type]
            )
        self.assertEqual(cursor.calls, [])

    def test_invalid_returned_terminal_proof_is_not_reported_as_durable(self) -> None:
        """A mismatched attempt/code cannot look like an accepted terminal result."""

        cursor = FakeCursor(
            [
                {
                    "task_id": TASK_ID,
                    "job_id": JOB_ID,
                    "attempt_count": MAX_BASIC_PITCH_TASK_ATTEMPTS - 1,
                    "completed_at": COMPLETED_AT,
                    "last_error_code": BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE.value,
                }
            ]
        )

        with self.assertRaises(BasicPitchStemRetryExhaustionProtocolError):
            fail_final_attempt_leased_basic_pitch_stem(
                cursor,
                lease=lease(),
                failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
            )


if __name__ == "__main__":
    unittest.main()
