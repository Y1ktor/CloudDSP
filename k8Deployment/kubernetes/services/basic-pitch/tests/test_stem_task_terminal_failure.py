"""Unit tests for the pure Basic Pitch pre-model terminal-failure statement.

The fake cursor records parameterized SQL only.  These tests create no
PostgreSQL/RabbitMQ/MinIO connection, worker loop, model process, container,
or Kubernetes resource.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.db.stem_task_terminal_failure import (
    FAIL_LEASED_BASIC_PITCH_STEM_TASK_SQL,
    BasicPitchStemTerminalFailureCode,
    BasicPitchStemTerminalFailureProtocolError,
    fail_leased_basic_pitch_stem_task,
)
from app.db.task_lease import BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
COMPLETED_AT = datetime(2026, 9, 11, 13, 5, tzinfo=UTC)


def lease(**overrides: object) -> BasicPitchTaskLease:
    """Return one current non-drum lease suitable for pre-model validation."""

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


class BasicPitchStemTerminalFailureTests(unittest.TestCase):
    """Prove only a current pre-model lease can record a bounded failure code."""

    def test_current_leased_task_becomes_terminal_with_exact_safe_code(self) -> None:
        """The statement clears the lease but intentionally leaves the Job unchanged."""

        cursor = FakeCursor(
            [
                {
                    "task_id": TASK_ID,
                    "job_id": JOB_ID,
                    "completed_at": COMPLETED_AT,
                    "last_error_code": BasicPitchStemTerminalFailureCode.METADATA_MISMATCH.value,
                }
            ]
        )

        result = fail_leased_basic_pitch_stem_task(
            cursor,
            lease=lease(),
            failure_code=BasicPitchStemTerminalFailureCode.METADATA_MISMATCH,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.task_id, TASK_ID)
        self.assertEqual(result.job_id, JOB_ID)
        self.assertEqual(result.completed_at, COMPLETED_AT)
        self.assertEqual(result.failure_code, BasicPitchStemTerminalFailureCode.METADATA_MISMATCH)
        self.assertEqual(cursor.calls, [
            (
                FAIL_LEASED_BASIC_PITCH_STEM_TASK_SQL,
                (
                    BasicPitchStemTerminalFailureCode.METADATA_MISMATCH.value,
                    TASK_ID,
                    JOB_ID,
                    "vocals",
                    "clouddsp-uploads",
                    f"stems/{JOB_ID}/vocals.wav",
                    "4-stems",
                    LEASE_TOKEN,
                ),
            )
        ])
        self.assertIn("status = 'failed'", FAIL_LEASED_BASIC_PITCH_STEM_TASK_SQL)
        self.assertIn("status = 'leased'", FAIL_LEASED_BASIC_PITCH_STEM_TASK_SQL)
        self.assertIn("lease_token = NULL", FAIL_LEASED_BASIC_PITCH_STEM_TASK_SQL)
        self.assertIn("lease_expires_at > CURRENT_TIMESTAMP", FAIL_LEASED_BASIC_PITCH_STEM_TASK_SQL)
        self.assertNotIn("UPDATE public.jobs", FAIL_LEASED_BASIC_PITCH_STEM_TASK_SQL)

    def test_ownership_loss_or_prior_state_change_returns_none_without_a_second_result(self) -> None:
        """A stale Pod cannot overwrite recovery or another task outcome."""

        cursor = FakeCursor([None])

        result = fail_leased_basic_pitch_stem_task(
            cursor,
            lease=lease(),
            failure_code=BasicPitchStemTerminalFailureCode.DOWNLOAD_CHECKSUM_MISMATCH,
        )

        self.assertIsNone(result)
        self.assertEqual(len(cursor.calls), 1)

    def test_invalid_lease_or_unreviewed_error_text_never_reaches_sql(self) -> None:
        """A worker cannot persist raw MinIO diagnostics in ``last_error_code``."""

        cursor = FakeCursor([])
        with self.assertRaises(BasicPitchStemTerminalFailureProtocolError):
            fail_leased_basic_pitch_stem_task(
                cursor,
                lease=lease(input_object_key=f"stems/{JOB_ID}/bass.wav"),
                failure_code=BasicPitchStemTerminalFailureCode.SIZE_MISMATCH,
            )
        self.assertEqual(cursor.calls, [])

        with self.assertRaises(BasicPitchStemTerminalFailureProtocolError):
            fail_leased_basic_pitch_stem_task(
                cursor,
                lease=lease(),
                failure_code="private raw MinIO error",  # type: ignore[arg-type]
            )
        self.assertEqual(cursor.calls, [])

    def test_invalid_returned_failure_proof_is_not_reported_as_durable(self) -> None:
        """A malformed or mismatched database row aborts the outer transaction."""

        cursor = FakeCursor(
            [
                {
                    "task_id": TASK_ID,
                    "job_id": JOB_ID,
                    "completed_at": COMPLETED_AT,
                    "last_error_code": BasicPitchStemTerminalFailureCode.SIZE_MISMATCH.value,
                }
            ]
        )

        with self.assertRaises(BasicPitchStemTerminalFailureProtocolError):
            fail_leased_basic_pitch_stem_task(
                cursor,
                lease=lease(),
                failure_code=BasicPitchStemTerminalFailureCode.METADATA_MISMATCH,
            )


if __name__ == "__main__":
    unittest.main()
