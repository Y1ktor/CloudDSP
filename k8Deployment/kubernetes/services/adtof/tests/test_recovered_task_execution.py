"""Unit tests for ADTOF's delivery-free recovered-task execution gate.

The success coordinator is mocked at its public boundary. These tests make no
PostgreSQL, MinIO, RabbitMQ, model, image, or Kubernetes call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.messaging.adtof_requested_message import ADTOFRequestedMessage
from app.runtime.claimed_task_success import ADTOFClaimedTaskSuccess, ADTOFClaimedTaskSuccessOutcome
from app.runtime.recovered_task_execution import (
    ADTOFRecoveredTaskExecutionError,
    execute_recovered_adtof_task,
)
from app.db.recovery import ADTOFRecoveredTask
from app.db.task_claim import ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


def recovery_message(**overrides: object) -> ADTOFRequestedMessage:
    """Return strict outbox evidence for a recovered drums task."""

    values: dict[str, object] = {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stem_name": "drums",
        "stem_bucket": "clouddsp-uploads",
        "stem_object_key": f"stems/{JOB_ID}/drums.wav",
        "stem_content_length": 1234,
        "stem_sha256": "a" * 64,
    }
    values.update(overrides)
    return ADTOFRequestedMessage(**values)  # type: ignore[arg-type]


def recovery_lease(**overrides: object) -> ADTOFTaskLease:
    """Return the second-attempt durable ownership from recovery SQL."""

    values: dict[str, object] = {
        "task_id": "9381d35a-355f-4fb1-bb39-32ceba7d917f",
        "job_id": JOB_ID,
        "stem_name": "drums",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"stems/{JOB_ID}/drums.wav",
        "stem_mode": "4-stems",
        "attempt_count": 2,
        "lease_token": "63c9d8d2-11db-41c4-9cc5-79889f912f98",
        "lease_expires_at": datetime(2026, 9, 14, 12, 15, tzinfo=UTC),
    }
    values.update(overrides)
    return ADTOFTaskLease(**values)  # type: ignore[arg-type]


def recovered_task(**overrides: object) -> ADTOFRecoveredTask:
    """Pair matching recovered lease and immutable strict event evidence."""

    values: dict[str, object] = {
        "lease": recovery_lease(),
        "message": recovery_message(),
    }
    values.update(overrides)
    return ADTOFRecoveredTask(**values)  # type: ignore[arg-type]


class ADTOFRecoveredTaskExecutionTests(unittest.TestCase):
    """Prove recovery runs no model work unless both durable halves agree."""

    @patch("app.runtime.recovered_task_execution.execute_claimed_adtof_task_success_path")
    def test_forwards_exact_valid_recovery_pair_to_success_coordinator(self, execute) -> None:
        """Recovered work enters the same preflight/start/finalization path."""

        expected = ADTOFClaimedTaskSuccess(
            outcome=ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST,
        )
        execute.return_value = expected
        database = MagicMock()
        storage_client = MagicMock()
        runner = MagicMock()
        task = recovered_task()

        returned = execute_recovered_adtof_task(
            task,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )

        self.assertIs(returned, expected)
        execute.assert_called_once_with(
            database=database,
            storage_client=storage_client,
            message=recovery_message(),
            lease=recovery_lease(),
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )

    @patch("app.runtime.recovered_task_execution.execute_claimed_adtof_task_success_path")
    def test_cross_wired_or_nonrecovery_pair_stops_before_success_path(self, execute) -> None:
        """Another event, object, or first lease cannot impersonate recovery work."""

        invalid_tasks = (
            recovered_task(lease=recovery_lease(request_event_id="1e5d48bd-6b8f-4e7a-9987-2a19e3e2a6ba")),
            recovered_task(message=recovery_message(stem_sha256="A" * 64)),
            recovered_task(lease=recovery_lease(attempt_count=1)),
            object(),
        )
        for invalid in invalid_tasks:
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaises(ADTOFRecoveredTaskExecutionError) as raised:
                    execute_recovered_adtof_task(
                        invalid,  # type: ignore[arg-type]
                        database=MagicMock(),
                        storage_client=MagicMock(),
                        work_directory=Path("/worker-scratch"),
                    )
                self.assertEqual(str(raised.exception), "ADTOF recovered task evidence is invalid.")

        execute.assert_not_called()

    @patch("app.runtime.recovered_task_execution.execute_claimed_adtof_task_success_path")
    def test_forged_recovery_container_stops_before_success_path(self, execute) -> None:
        """A bypassed frozen dataclass constructor still cannot reach storage/CPU work."""

        forged = object.__new__(ADTOFRecoveredTask)
        object.__setattr__(forged, "lease", object())
        object.__setattr__(forged, "message", object())

        with self.assertRaises(ADTOFRecoveredTaskExecutionError):
            execute_recovered_adtof_task(
                forged,
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )

        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
