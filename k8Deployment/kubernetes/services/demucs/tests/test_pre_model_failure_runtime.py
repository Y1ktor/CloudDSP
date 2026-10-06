"""Tests for one-task Demucs source-failure runtime handoff.

The established one-task runtime and committed failure boundary are patched at
their public seams. These tests prove exception routing only; they do not open
PostgreSQL, contact MinIO/RabbitMQ, run FFprobe/Demucs, sleep, create a Pod, or
change the KEDA scaler.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.messaging.amqp_manual_ack import DemucsConsumeOneOutcome, DemucsConsumeOneResult
from app.db.demucs_task_completion import CommittedDemucsStemSet
from app.processing.demucs_process import DemucsProcessTimedOut
from app.runtime.pre_model_failure_runtime import (
    DemucsOneTaskExecutionOutcome,
    execute_acknowledged_demucs_task_with_pre_model_failure_policy,
)
from app.db.pre_model_failure_transition import (
    DemucsPreModelFailureTransition,
    DemucsPreModelFailureTransitionDisposition,
    DemucsPreModelRetrySchedule,
    DemucsPreModelTerminalFailure,
)
from app.runtime.source_failure_classification import (
    DemucsPreModelFailureDisposition,
    DemucsPreModelRetryCode,
    DemucsPreModelTerminalFailureCode,
)
from app.artifacts.source_object import (
    DemucsPermanentSourceVerificationError,
    DemucsSourceVerificationFailureCode,
)
from app.db.task_lease import DemucsTaskLease


TASK_ID = "00000000-0000-4000-8000-000000000001"
JOB_ID = "00000000-0000-4000-8000-000000000002"
EVENT_ID = "00000000-0000-4000-8000-000000000003"
LEASE_TOKEN = "00000000-0000-4000-8000-000000000004"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def lease() -> DemucsTaskLease:
    """Build the exact acknowledged source lease used by all test paths."""

    return DemucsTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2026, 9, 20, 12, 15, tzinfo=UTC),
    )


def receive_result() -> DemucsConsumeOneResult:
    """Return a real broker-acknowledged result carrying that exact lease."""

    return DemucsConsumeOneResult(
        outcome=DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE,
        lease=lease(),
    )


def retry_transition() -> DemucsPreModelFailureTransition:
    """Build committed evidence for one source-storage retry schedule."""

    return DemucsPreModelFailureTransition(
        disposition=DemucsPreModelFailureTransitionDisposition.RETRY_SCHEDULED,
        retry_schedule=DemucsPreModelRetrySchedule(
            task_id=TASK_ID,
            job_id=JOB_ID,
            attempt_count=1,
            failure_code=DemucsPreModelRetryCode.STORAGE_UNAVAILABLE,
            available_at=datetime(2026, 9, 20, 12, 0, 30, tzinfo=UTC),
        ),
    )


def terminal_transition() -> DemucsPreModelFailureTransition:
    """Build committed evidence for one immutable source terminal result."""

    return DemucsPreModelFailureTransition(
        disposition=DemucsPreModelFailureTransitionDisposition.TERMINAL_FAILURE,
        terminal_failure=DemucsPreModelTerminalFailure(
            task_id=TASK_ID,
            job_id=JOB_ID,
            attempt_count=1,
            failure_code=DemucsPreModelTerminalFailureCode.METADATA_MISMATCH,
            completed_at=NOW,
            job_revision=7,
        ),
    )


class DemucsPreModelFailureRuntimeTests(unittest.TestCase):
    """Prove only reviewed pre-model exceptions are converted to task results."""

    def _run(self) -> object:
        """Call the handoff with inert dependencies owned by patched boundaries."""

        return execute_acknowledged_demucs_task_with_pre_model_failure_policy(
            receive_result=receive_result(),
            source_client=MagicMock(),  # type: ignore[arg-type]
            artifact_client=MagicMock(),  # type: ignore[arg-type]
            database=MagicMock(),  # type: ignore[arg-type]
            work_directory=MagicMock(),
        )

    @patch("app.runtime.pre_model_failure_runtime.commit_leased_demucs_pre_model_failure_transition")
    @patch("app.runtime.pre_model_failure_runtime.execute_acknowledged_demucs_task_once")
    def test_success_keeps_existing_completion_and_never_enters_failure_policy(
        self,
        execute_once,
        commit_failure,
    ) -> None:
        """The happy path stays inside the established one-task runtime boundary."""

        completed = CommittedDemucsStemSet(
            lease=lease(),
            stems=(),
            completed_at=NOW,
            job_revision=6,
            downstream_events=(),
        )
        execute_once.return_value = completed

        result = self._run()

        self.assertEqual(result.outcome, DemucsOneTaskExecutionOutcome.SUCCEEDED)  # type: ignore[union-attr]
        self.assertIs(result.completion, completed)  # type: ignore[union-attr]
        commit_failure.assert_not_called()

    @patch("app.runtime.pre_model_failure_runtime.commit_leased_demucs_pre_model_failure_transition")
    @patch("app.runtime.pre_model_failure_runtime.execute_acknowledged_demucs_task_once")
    def test_permanent_source_error_commits_matching_terminal_result(
        self,
        execute_once,
        commit_failure,
    ) -> None:
        """A source mismatch is converted only through the committed SQL boundary."""

        source_error = DemucsPermanentSourceVerificationError(
            DemucsSourceVerificationFailureCode.METADATA_MISMATCH
        )
        execute_once.side_effect = source_error
        transition = terminal_transition()
        commit_failure.return_value = transition

        result = self._run()

        self.assertEqual(result.outcome, DemucsOneTaskExecutionOutcome.TERMINAL_FAILURE)  # type: ignore[union-attr]
        self.assertIs(result.failure_transition, transition)  # type: ignore[union-attr]
        _args, kwargs = commit_failure.call_args
        self.assertEqual(kwargs["lease"], lease())
        self.assertEqual(
            kwargs["classification"].disposition,
            DemucsPreModelFailureDisposition.TERMINAL_FAILURE,
        )
        self.assertEqual(
            kwargs["classification"].terminal_failure_code,
            DemucsPreModelTerminalFailureCode.METADATA_MISMATCH,
        )

    @patch("app.runtime.pre_model_failure_runtime.commit_leased_demucs_pre_model_failure_transition")
    @patch("app.runtime.pre_model_failure_runtime.execute_acknowledged_demucs_task_once")
    def test_committed_retry_and_no_row_race_have_distinct_safe_outcomes(
        self,
        execute_once,
        commit_failure,
    ) -> None:
        """A durable retry differs from another owner winning the transition race."""

        from app.artifacts.source_download import DemucsSourceDownloadUnavailable

        execute_once.side_effect = DemucsSourceDownloadUnavailable("private outage detail")
        commit_failure.return_value = retry_transition()

        retry_result = self._run()

        self.assertEqual(retry_result.outcome, DemucsOneTaskExecutionOutcome.RETRY_SCHEDULED)  # type: ignore[union-attr]
        self.assertEqual(
            retry_result.failure_transition.disposition,  # type: ignore[union-attr]
            DemucsPreModelFailureTransitionDisposition.RETRY_SCHEDULED,
        )

        commit_failure.return_value = None
        ownership_result = self._run()

        self.assertEqual(ownership_result.outcome, DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)  # type: ignore[union-attr]
        self.assertIsNone(ownership_result.failure_transition)  # type: ignore[union-attr]

    @patch("app.runtime.pre_model_failure_runtime.commit_leased_demucs_pre_model_failure_transition")
    @patch("app.runtime.pre_model_failure_runtime.execute_acknowledged_demucs_task_once")
    def test_unclassified_post_running_error_escapes_without_failure_sql(
        self,
        execute_once,
        commit_failure,
    ) -> None:
        """A model timeout needs its own later policy, never a source retry."""

        failure = DemucsProcessTimedOut("private process detail")
        execute_once.side_effect = failure

        with self.assertRaises(DemucsProcessTimedOut) as raised:
            self._run()

        self.assertIs(raised.exception, failure)
        commit_failure.assert_not_called()

    @patch("app.runtime.pre_model_failure_runtime.commit_leased_demucs_pre_model_failure_transition")
    @patch("app.runtime.pre_model_failure_runtime.execute_acknowledged_demucs_task_once")
    def test_existing_ownership_loss_stays_a_normal_stop_signal(
        self,
        execute_once,
        commit_failure,
    ) -> None:
        """A failed start/completion guard must not enter source-failure policy."""

        execute_once.return_value = None

        result = self._run()

        self.assertEqual(result.outcome, DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)  # type: ignore[union-attr]
        commit_failure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
