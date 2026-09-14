"""Unit tests for ADTOF's source-only post-claim success-path coordinator.

Every external boundary is patched or simulated with an in-memory context. The
tests do not connect to MinIO/PostgreSQL/RabbitMQ, run ADTOF, or use Kubernetes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.claimed_task_success import (
    ADTOFClaimedTaskSuccessOutcome,
    execute_claimed_adtof_task_success_path,
)
from app.stem_task_start import RunningADTOFStem
from app.task_completion import ADTOFTaskCompletion
from test_first_claim import message
from test_stem_task_start import downloaded, lease, source


@contextmanager
def running_context(running: RunningADTOFStem | None, events: list[str]) -> Iterator[RunningADTOFStem | None]:
    """Expose exactly when the temporary running-stem context remains alive."""

    events.append("running-context-open")
    try:
        yield running
    finally:
        events.append("running-context-cleanup")


class ADTOFClaimedTaskSuccessTests(unittest.TestCase):
    """Prove the coordinator keeps the model/finalization inside scratch scope."""

    @patch("app.claimed_task_success.finalize_running_adtof_task")
    @patch("app.claimed_task_success.execute_running_adtof_local_task")
    @patch("app.claimed_task_success.started_verified_adtof_stem")
    @patch("app.claimed_task_success.verify_claimed_adtof_stem_head_object")
    def test_runs_preflight_start_local_output_and_finalization_in_order(
        self,
        preflight,
        start_context,
        local_execution,
        finalize,
    ) -> None:
        """Only committed completion escapes after the scratch context closes."""

        events: list[str] = []
        claimed_lease = lease()
        running = RunningADTOFStem(
            lease=claimed_lease,
            stem=downloaded(),
            started_at=datetime(2026, 9, 13, 12, 20, tzinfo=UTC),
        )
        verified_stem = source()
        local_outputs = object()
        completion = ADTOFTaskCompletion(
            task_id=claimed_lease.task_id,
            job_id=claimed_lease.job_id,
            completed_at=datetime(2026, 9, 13, 12, 30, tzinfo=UTC),
            resulting_revision=8,
        )
        database = MagicMock()
        storage_client = MagicMock()
        preflight.side_effect = lambda *_args, **_kwargs: (events.append("preflight"), verified_stem)[1]
        start_context.side_effect = lambda **_kwargs: running_context(running, events)
        local_execution.side_effect = lambda **_kwargs: (events.append("local-output"), local_outputs)[1]
        finalize.side_effect = lambda **_kwargs: (events.append("finalization"), completion)[1]

        result = execute_claimed_adtof_task_success_path(
            database=database,
            storage_client=storage_client,
            message=message(),
            lease=claimed_lease,
            work_directory=Path("/pod-scratch"),
            process_timeout_seconds=60,
        )

        self.assertEqual(result.outcome, ADTOFClaimedTaskSuccessOutcome.SUCCEEDED)
        self.assertIs(result.completion, completion)
        self.assertEqual(
            events,
            ["preflight", "running-context-open", "local-output", "finalization", "running-context-cleanup"],
        )
        preflight.assert_called_once_with(storage_client, lease=claimed_lease, message=message())
        start_context.assert_called_once_with(
            database=database,
            client=storage_client,
            lease=claimed_lease,
            source=verified_stem,
            work_directory=Path("/pod-scratch"),
        )
        local_execution.assert_called_once_with(
            running=running,
            work_directory=Path("/pod-scratch"),
            process_timeout_seconds=60,
            process_runner=None,
        )
        finalize.assert_called_once_with(
            database=database,
            storage_client=storage_client,
            local_outputs=local_outputs,
        )

    @patch("app.claimed_task_success.finalize_running_adtof_task")
    @patch("app.claimed_task_success.execute_running_adtof_local_task")
    @patch("app.claimed_task_success.started_verified_adtof_stem")
    @patch("app.claimed_task_success.verify_claimed_adtof_stem_head_object")
    def test_start_ownership_loss_stops_before_cpu_or_finalization(
        self,
        preflight,
        start_context,
        local_execution,
        finalize,
    ) -> None:
        """No temporary path or later side effect reaches a stale owner."""

        events: list[str] = []
        preflight.return_value = source()
        start_context.return_value = running_context(None, events)

        result = execute_claimed_adtof_task_success_path(
            database=MagicMock(),
            storage_client=MagicMock(),
            message=message(),
            lease=lease(),
            work_directory=Path("/pod-scratch"),
        )

        self.assertEqual(result.outcome, ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST)
        self.assertIsNone(result.completion)
        self.assertEqual(events, ["running-context-open", "running-context-cleanup"])
        local_execution.assert_not_called()
        finalize.assert_not_called()

    @patch("app.claimed_task_success.finalize_running_adtof_task")
    @patch("app.claimed_task_success.execute_running_adtof_local_task")
    @patch("app.claimed_task_success.started_verified_adtof_stem")
    @patch("app.claimed_task_success.verify_claimed_adtof_stem_head_object")
    def test_final_ownership_loss_is_not_reported_as_success(
        self,
        preflight,
        start_context,
        local_execution,
        finalize,
    ) -> None:
        """A recovery that wins during final commit leaves no completion result."""

        events: list[str] = []
        claimed_lease = lease()
        running = RunningADTOFStem(
            lease=claimed_lease,
            stem=downloaded(),
            started_at=datetime(2026, 9, 13, 12, 20, tzinfo=UTC),
        )
        preflight.return_value = source()
        start_context.return_value = running_context(running, events)
        local_execution.return_value = object()
        finalize.return_value = None

        result = execute_claimed_adtof_task_success_path(
            database=MagicMock(),
            storage_client=MagicMock(),
            message=message(),
            lease=claimed_lease,
            work_directory=Path("/pod-scratch"),
        )

        self.assertEqual(result.outcome, ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST)
        self.assertIsNone(result.completion)
        self.assertEqual(events, ["running-context-open", "running-context-cleanup"])
        local_execution.assert_called_once()
        finalize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
