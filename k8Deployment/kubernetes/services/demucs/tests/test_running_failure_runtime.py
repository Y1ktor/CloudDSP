"""Tests for one-task Demucs post-``running`` failure routing.

The pre-model handoff and committed running transition are patched at public
seams. These unit tests prove only exception classification and durable-result
routing; they open no PostgreSQL connection, call no MinIO/RabbitMQ service,
run no FFprobe/Demucs command, sleep, create a Pod, or change KEDA.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.messaging.amqp_manual_ack import DemucsConsumeOneOutcome, DemucsConsumeOneResult
from app.processing.demucs_process import DemucsLeaseRenewalOwnershipLost
from app.processing.demucs_process import DemucsProcessTimedOut
from app.db.demucs_task_completion import CommittedDemucsStemSet
from app.runtime.pre_model_failure_runtime import DemucsOneTaskExecution, DemucsOneTaskExecutionOutcome
from app.runtime.running_failure_classification import DemucsRunningRetryCode, DemucsRunningTerminalCode
from app.runtime.running_failure_runtime import execute_acknowledged_demucs_task_with_running_failure_policy
from app.db.running_failure_transition import (
    DemucsRunningFailureTransition,
    DemucsRunningFailureTransitionDisposition,
    DemucsRunningRetryExhaustion,
    DemucsRunningRetryExhaustionCode,
    DemucsRunningRetrySchedule,
)
from app.artifacts.source_object import DemucsSourceStorageProtocolError
from app.db.task_lease import DemucsTaskLease


TASK_ID = "00000000-0000-4000-8000-000000000001"
JOB_ID = "00000000-0000-4000-8000-000000000002"
EVENT_ID = "00000000-0000-4000-8000-000000000003"
LEASE_TOKEN = "00000000-0000-4000-8000-000000000004"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def lease() -> DemucsTaskLease:
    """Build the acknowledged lease that a running SQL guard must re-check."""

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
    """Return the real acknowledged-result type carrying the exact lease."""

    return DemucsConsumeOneResult(
        outcome=DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE,
        lease=lease(),
    )


def retry_transition() -> DemucsRunningFailureTransition:
    """Build committed evidence for one reviewed running-process retry."""

    return DemucsRunningFailureTransition(
        disposition=DemucsRunningFailureTransitionDisposition.RETRY_SCHEDULED,
        retry_schedule=DemucsRunningRetrySchedule(
            task_id=TASK_ID,
            job_id=JOB_ID,
            attempt_count=1,
            failure_code=DemucsRunningRetryCode.PROCESS_FAILED,
            available_at=datetime(2026, 9, 20, 12, 0, 30, tzinfo=UTC),
        ),
    )


def terminal_transition() -> DemucsRunningFailureTransition:
    """Build committed evidence for final allowed running-attempt exhaustion."""

    return DemucsRunningFailureTransition(
        disposition=DemucsRunningFailureTransitionDisposition.TERMINAL_FAILURE,
        retry_exhaustion=DemucsRunningRetryExhaustion(
            task_id=TASK_ID,
            job_id=JOB_ID,
            attempt_count=3,
            failure_code=DemucsRunningTerminalCode.PROCESS_TIMED_OUT,
            completed_at=NOW,
            job_revision=8,
        ),
    )


class DemucsRunningFailureRuntimeTests(unittest.TestCase):
    """Prove only reviewed after-start failures receive running SQL outcomes."""

    def _run(self, **overrides: object) -> DemucsOneTaskExecution:
        """Call the handoff with inert dependencies owned by patched seams."""

        arguments: dict[str, object] = {
            "receive_result": receive_result(),
            "source_client": MagicMock(),
            "artifact_client": MagicMock(),
            "database": MagicMock(),
            "work_directory": MagicMock(),
        }
        arguments.update(overrides)
        return execute_acknowledged_demucs_task_with_running_failure_policy(**arguments)  # type: ignore[arg-type]

    @patch("app.runtime.running_failure_runtime.commit_running_demucs_failure_transition")
    @patch("app.runtime.running_failure_runtime.execute_acknowledged_demucs_task_with_pre_model_failure_policy")
    def test_inner_success_passes_through_without_running_failure_sql(
        self,
        execute_with_pre_model_policy,
        commit_running_failure,
    ) -> None:
        """The running layer changes nothing when the inner attempt completed."""

        completed = CommittedDemucsStemSet(
            lease=lease(),
            stems=(),
            completed_at=NOW,
            job_revision=6,
            downstream_events=(),
        )
        expected = DemucsOneTaskExecution(
            outcome=DemucsOneTaskExecutionOutcome.SUCCEEDED,
            completion=completed,
        )
        execute_with_pre_model_policy.return_value = expected

        result = self._run()

        self.assertIs(result, expected)
        commit_running_failure.assert_not_called()

    @patch("app.runtime.running_failure_runtime.commit_running_demucs_failure_transition")
    @patch("app.runtime.running_failure_runtime.execute_acknowledged_demucs_task_with_pre_model_failure_policy")
    def test_inner_pre_model_outcome_does_not_enter_running_policy(
        self,
        execute_with_pre_model_policy,
        commit_running_failure,
    ) -> None:
        """The outer catch sees exceptions only, not source-policy outcomes."""

        expected = DemucsOneTaskExecution(
            outcome=DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST,
        )
        execute_with_pre_model_policy.return_value = expected

        result = self._run()

        self.assertIs(result, expected)
        commit_running_failure.assert_not_called()

    @patch("app.runtime.running_failure_runtime.commit_running_demucs_failure_transition")
    @patch("app.runtime.running_failure_runtime.execute_acknowledged_demucs_task_with_pre_model_failure_policy")
    def test_reviewed_running_timeout_commits_terminal_result_without_retry(
        self,
        execute_with_pre_model_policy,
        commit_running_failure,
    ) -> None:
        """A post-start timeout fails the job on its first running attempt."""

        execute_with_pre_model_policy.side_effect = DemucsProcessTimedOut("private timeout detail")
        transition = terminal_transition()
        commit_running_failure.return_value = transition

        result = self._run(running_retry_after_seconds=45)

        self.assertEqual(result.outcome, DemucsOneTaskExecutionOutcome.TERMINAL_FAILURE)
        self.assertIs(result.failure_transition, transition)
        _args, kwargs = commit_running_failure.call_args
        self.assertEqual(kwargs["lease"], lease())
        self.assertEqual(kwargs["retry_after_seconds"], 45)
        self.assertEqual(
            kwargs["classification"].terminal_code,
            DemucsRunningTerminalCode.PROCESS_TIMED_OUT,
        )

    @patch("app.runtime.running_failure_runtime.commit_running_demucs_failure_transition")
    @patch("app.runtime.running_failure_runtime.execute_acknowledged_demucs_task_with_pre_model_failure_policy")
    def test_committed_exhaustion_becomes_terminal_worker_outcome(
        self,
        execute_with_pre_model_policy,
        commit_running_failure,
    ) -> None:
        """The shared result preserves concrete running exhaustion evidence."""

        execute_with_pre_model_policy.side_effect = DemucsProcessTimedOut("private timeout detail")
        transition = terminal_transition()
        commit_running_failure.return_value = transition

        result = self._run()

        self.assertEqual(result.outcome, DemucsOneTaskExecutionOutcome.TERMINAL_FAILURE)
        self.assertIs(result.failure_transition, transition)

    @patch("app.runtime.running_failure_runtime.commit_running_demucs_failure_transition")
    @patch("app.runtime.running_failure_runtime.execute_acknowledged_demucs_task_with_pre_model_failure_policy")
    def test_running_transition_no_row_becomes_ownership_loss(
        self,
        execute_with_pre_model_policy,
        commit_running_failure,
    ) -> None:
        """A stale timed-out worker must stop when its token no longer matches."""

        execute_with_pre_model_policy.side_effect = DemucsProcessTimedOut("private timeout detail")
        commit_running_failure.return_value = None

        result = self._run()

        self.assertEqual(result.outcome, DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
        self.assertIsNone(result.failure_transition)

    @patch("app.runtime.running_failure_runtime.commit_running_demucs_failure_transition")
    @patch("app.runtime.running_failure_runtime.execute_acknowledged_demucs_task_with_pre_model_failure_policy")
    def test_renewal_stop_becomes_ownership_loss_without_failure_transition(
        self,
        execute_with_pre_model_policy,
        commit_running_failure,
    ) -> None:
        """A stopped child after lease loss must not be retried as a model fault."""

        execute_with_pre_model_policy.side_effect = DemucsLeaseRenewalOwnershipLost(
            "Demucs task lease ownership was lost."
        )

        result = self._run()

        self.assertEqual(result.outcome, DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
        self.assertIsNone(result.failure_transition)
        commit_running_failure.assert_not_called()

    @patch("app.runtime.running_failure_runtime.commit_running_demucs_failure_transition")
    @patch("app.runtime.running_failure_runtime.execute_acknowledged_demucs_task_with_pre_model_failure_policy")
    def test_unclassified_error_escapes_without_running_failure_sql(
        self,
        execute_with_pre_model_policy,
        commit_running_failure,
    ) -> None:
        """A source protocol defect is not silently retried as model work."""

        failure = DemucsSourceStorageProtocolError("private protocol detail")
        execute_with_pre_model_policy.side_effect = failure

        with self.assertRaises(DemucsSourceStorageProtocolError) as raised:
            self._run()

        self.assertIs(raised.exception, failure)
        commit_running_failure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
