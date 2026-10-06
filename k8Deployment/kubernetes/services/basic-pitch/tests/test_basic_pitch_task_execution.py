"""Unit tests for the post-claim Basic Pitch runtime orchestration contract.

These tests replace every I/O/process/transaction boundary with a small fake.
They prove the coordinator's ordering and stop rules without importing Pika,
connecting to PostgreSQL or MinIO, running Basic Pitch, reading audio, or
changing a Kubernetes resource. RabbitMQ receive/ack/retry policy remains a
separate future task.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.runtime.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecutionOutcome,
    execute_claimed_basic_pitch_task,
)
from app.messaging.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.artifacts.midi_artifact_upload import BasicPitchMidiUploadUnavailable
from app.db.midi_task_completion import BasicPitchMidiTaskCompletion
from app.artifacts.stem_download import DownloadedBasicPitchStem
from app.db.stem_task_terminal_failure import (
    BasicPitchStemTerminalFailure,
    BasicPitchStemTerminalFailureCode,
)
from app.db.stem_task_retry_exhaustion import (
    BasicPitchStemRetryExhaustion,
    BasicPitchStemRetryExhaustionCode,
)
from app.db.stem_task_retry_schedule import BasicPitchStemRetrySchedule, BasicPitchStemRetryScheduleCode
from app.db.stem_task_start import RunningBasicPitchStem
from app.db.task_lease import BasicPitchTaskLease
from app.processing.tempo_candidate import BasicPitchTempoCandidate


JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
EVENT_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
TASK_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
LEASE_TOKEN = "123e4567-e89b-12d3-a456-426614174000"


def message() -> BasicPitchRequestedMessage:
    """Return one parser-shaped non-drum request for the coordinator boundary."""

    return BasicPitchRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        stem_name="vocals",
        stem_bucket="clouddsp-uploads",
        stem_object_key=f"stems/{JOB_ID}/vocals.wav",
        stem_content_length=1_024,
        stem_sha256="a" * 64,
    )


def lease() -> BasicPitchTaskLease:
    """Return the committed task lease that authorizes one post-claim attempt."""

    return BasicPitchTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        stem_name="vocals",
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"stems/{JOB_ID}/vocals.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2026, 9, 11, 12, 15, tzinfo=UTC),
    )


def running_stem(source_path: Path) -> RunningBasicPitchStem:
    """Return the real runtime value shape with a mocked, unread local path."""

    return RunningBasicPitchStem(
        lease=lease(),
        stem=DownloadedBasicPitchStem(
            stem_path=source_path,
            size_bytes=1_024,
            sha256="a" * 64,
        ),
        started_at=datetime(2026, 9, 11, 12, 20, tzinfo=UTC),
    )


class RecordingStemScope:
    """Expose temporary-stem scope lifetime without creating a local file."""

    def __init__(self, events: list[str], running: object | None) -> None:
        self._events = events
        self._running = running

    def __enter__(self) -> object | None:
        """Record that the committed model-input scope is now available."""

        self._events.append("stem-scope-enter")
        return self._running

    def __exit__(self, exception_type, exception, traceback) -> bool:
        """Record cleanup after success, ownership loss, or propagated error."""

        self._events.append("stem-scope-exit")
        return False


class BasicPitchTaskExecutionTests(unittest.TestCase):
    """Prove only the post-claim happy path reaches durable completion."""

    @patch("app.runtime.basic_pitch_task_execution.estimate_basic_pitch_tempo_candidate")
    @patch("app.runtime.basic_pitch_task_execution.commit_verified_basic_pitch_midi_task")
    @patch("app.runtime.basic_pitch_task_execution.verify_uploaded_basic_pitch_midi_head_object")
    @patch("app.runtime.basic_pitch_task_execution.upload_basic_pitch_midi_object")
    @patch("app.runtime.basic_pitch_task_execution.build_basic_pitch_midi_output_object")
    @patch("app.runtime.basic_pitch_task_execution.verify_and_hash_basic_pitch_midi")
    @patch("app.runtime.basic_pitch_task_execution.run_basic_pitch_inference")
    @patch("app.runtime.basic_pitch_task_execution.build_basic_pitch_inference_command")
    @patch("app.runtime.basic_pitch_task_execution.started_verified_basic_pitch_stem")
    @patch("app.runtime.basic_pitch_task_execution.verify_claimed_basic_pitch_stem_head_object")
    def test_happy_path_orders_each_boundary_before_committed_completion(
        self,
        verify_stem,
        start_stem,
        build_inference,
        run_inference,
        verify_midi,
        build_output,
        upload,
        verify_stored,
        commit,
        estimate_tempo,
    ) -> None:
        """The coordinator retains temporary bytes until MinIO proof and commit finish."""

        events: list[str] = []
        database = MagicMock()
        storage_client = MagicMock()
        # Use the validated value object so the coordinator's runtime boundary
        # sees exactly the structure yielded by the verified-download context.
        running = running_stem(Path("/pod-scratch/temporary-stem.wav"))
        verified_stem = object()
        inference = object()
        artifact = object()
        output_object = object()
        upload_receipt = object()
        stored_midi = MagicMock()
        completion = BasicPitchMidiTaskCompletion(
            task_id=TASK_ID,
            job_id=JOB_ID,
            completed_at=datetime(2026, 9, 11, 12, 30, tzinfo=UTC),
        )
        tempo_candidate = BasicPitchTempoCandidate(
            bpm=120.0,
            beat_count=8,
            duration_seconds=10.0,
            interval_consistency=0.9,
            credible=True,
            confidence="medium",
        )

        verify_stem.side_effect = lambda *args, **kwargs: (events.append("head-stem"), verified_stem)[1]
        start_stem.return_value = RecordingStemScope(events, running)
        build_inference.side_effect = lambda **kwargs: (events.append("build-inference"), inference)[1]
        run_inference.side_effect = lambda *args, **kwargs: (events.append("run-inference"), inference)[1]
        estimate_tempo.side_effect = lambda path: (
            events.append("estimate-tempo"),
            tempo_candidate,
        )[1]
        verify_midi.side_effect = lambda value: (events.append("verify-midi"), artifact)[1]
        build_output.side_effect = lambda **kwargs: (events.append("plan-midi"), output_object)[1]
        upload.side_effect = lambda **kwargs: (events.append("upload-midi"), upload_receipt)[1]
        verify_stored.side_effect = lambda *args, **kwargs: (events.append("head-midi"), stored_midi)[1]
        commit.side_effect = lambda **kwargs: (events.append("commit-completion"), completion)[1]

        result = execute_claimed_basic_pitch_task(
            database=database,
            storage_client=storage_client,
            message=message(),
            lease=lease(),
            work_directory=Path("/pod-scratch"),
        )

        self.assertEqual(result.outcome, BasicPitchClaimedTaskExecutionOutcome.SUCCEEDED)
        self.assertIs(result.completion, completion)
        self.assertEqual(
            events,
            [
                "head-stem",
                "stem-scope-enter",
                "build-inference",
                "run-inference",
                "estimate-tempo",
                "verify-midi",
                "plan-midi",
                "upload-midi",
                "head-midi",
                "commit-completion",
                "stem-scope-exit",
            ],
        )
        verify_stem.assert_called_once_with(storage_client, lease=lease(), message=message())
        start_stem.assert_called_once_with(
            database=database,
            client=storage_client,
            lease=lease(),
            source=verified_stem,
            work_directory=Path("/pod-scratch"),
        )
        build_inference.assert_called_once_with(running=running, work_directory=Path("/pod-scratch"))
        run_inference.assert_called_once_with(
            inference,
            timeout_seconds=300,
            runner=None,
        )
        estimate_tempo.assert_called_once_with(running.stem.stem_path)
        verify_midi.assert_called_once_with(inference)
        build_output.assert_called_once_with(lease=lease(), message=message(), artifact=artifact)
        upload.assert_called_once_with(client=storage_client, output_object=output_object)
        verify_stored.assert_called_once_with(
            storage_client,
            output_object=output_object,
            upload_receipt=upload_receipt,
        )
        commit.assert_called_once_with(
            database=database,
            lease=lease(),
            stored_midi=stored_midi,
            tempo_candidate=tempo_candidate,
        )

    @patch("app.runtime.basic_pitch_task_execution.commit_verified_basic_pitch_midi_task")
    @patch("app.runtime.basic_pitch_task_execution.build_basic_pitch_inference_command")
    @patch("app.runtime.basic_pitch_task_execution.started_verified_basic_pitch_stem")
    @patch("app.runtime.basic_pitch_task_execution.verify_claimed_basic_pitch_stem_head_object")
    def test_start_ownership_loss_stops_before_model_or_output_work(
        self,
        verify_stem,
        start_stem,
        build_inference,
        commit,
    ) -> None:
        """No CPU process, MIDI write, or completion is permitted after a no-row start."""

        events: list[str] = []
        storage_client = MagicMock()
        verify_stem.side_effect = lambda *args, **kwargs: (events.append("head-stem"), object())[1]
        start_stem.return_value = RecordingStemScope(events, None)

        result = execute_claimed_basic_pitch_task(
            database=MagicMock(),
            storage_client=storage_client,
            message=message(),
            lease=lease(),
            work_directory=Path("/pod-scratch"),
        )

        self.assertEqual(result.outcome, BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST)
        self.assertIsNone(result.completion)
        self.assertEqual(events, ["head-stem", "stem-scope-enter", "stem-scope-exit"])
        build_inference.assert_not_called()
        commit.assert_not_called()

    def test_execution_result_requires_exact_success_failure_retry_or_ownership_evidence(self) -> None:
        """The acknowledged gate cannot expose any durable outcome without its receipt."""

        from app.runtime.basic_pitch_task_execution import BasicPitchClaimedTaskExecution

        terminal_failure = BasicPitchStemTerminalFailure(
            task_id=TASK_ID,
            job_id=JOB_ID,
            failure_code=BasicPitchStemTerminalFailureCode.METADATA_MISMATCH,
            completed_at=datetime(2026, 9, 11, 12, 25, tzinfo=UTC),
        )
        valid = BasicPitchClaimedTaskExecution(
            outcome=BasicPitchClaimedTaskExecutionOutcome.TERMINAL_FAILURE,
            terminal_failure=terminal_failure,
        )
        self.assertIs(valid.terminal_failure, terminal_failure)
        retry_schedule = BasicPitchStemRetrySchedule(
            task_id=TASK_ID,
            job_id=JOB_ID,
            attempt_count=1,
            failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
            available_at=datetime(2026, 9, 11, 12, 26, tzinfo=UTC),
        )
        scheduled = BasicPitchClaimedTaskExecution(
            outcome=BasicPitchClaimedTaskExecutionOutcome.RETRY_SCHEDULED,
            retry_schedule=retry_schedule,
        )
        self.assertIs(scheduled.retry_schedule, retry_schedule)
        retry_exhaustion = BasicPitchStemRetryExhaustion(
            task_id=TASK_ID,
            job_id=JOB_ID,
            failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
            completed_at=datetime(2026, 9, 11, 12, 27, tzinfo=UTC),
        )
        exhausted = BasicPitchClaimedTaskExecution(
            outcome=BasicPitchClaimedTaskExecutionOutcome.RETRY_EXHAUSTED,
            retry_exhaustion=retry_exhaustion,
        )
        self.assertIs(exhausted.retry_exhaustion, retry_exhaustion)

        for outcome, completion, failure, schedule, exhaustion in (
            (BasicPitchClaimedTaskExecutionOutcome.TERMINAL_FAILURE, None, None, None, None),
            (BasicPitchClaimedTaskExecutionOutcome.RETRY_SCHEDULED, None, None, None, None),
            (BasicPitchClaimedTaskExecutionOutcome.RETRY_EXHAUSTED, None, None, None, None),
            (BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST, None, terminal_failure, None, None),
            (BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST, None, None, retry_schedule, None),
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(TypeError):
                    BasicPitchClaimedTaskExecution(
                        outcome=outcome,
                        completion=completion,
                        terminal_failure=failure,
                        retry_schedule=schedule,
                        retry_exhaustion=exhaustion,
                    )

    @patch("app.runtime.basic_pitch_task_execution.estimate_basic_pitch_tempo_candidate")
    @patch("app.runtime.basic_pitch_task_execution.commit_verified_basic_pitch_midi_task")
    @patch("app.runtime.basic_pitch_task_execution.verify_uploaded_basic_pitch_midi_head_object")
    @patch("app.runtime.basic_pitch_task_execution.upload_basic_pitch_midi_object")
    @patch("app.runtime.basic_pitch_task_execution.build_basic_pitch_midi_output_object")
    @patch("app.runtime.basic_pitch_task_execution.verify_and_hash_basic_pitch_midi")
    @patch("app.runtime.basic_pitch_task_execution.run_basic_pitch_inference")
    @patch("app.runtime.basic_pitch_task_execution.build_basic_pitch_inference_command")
    @patch("app.runtime.basic_pitch_task_execution.started_verified_basic_pitch_stem")
    @patch("app.runtime.basic_pitch_task_execution.verify_claimed_basic_pitch_stem_head_object")
    def test_upload_failure_propagates_and_never_attempts_head_or_completion(
        self,
        verify_stem,
        start_stem,
        build_inference,
        run_inference,
        verify_midi,
        build_output,
        upload,
        verify_stored,
        commit,
        estimate_tempo,
    ) -> None:
        """A retryable MinIO failure remains visible to the later supervisor."""

        events: list[str] = []
        running = running_stem(Path("/pod-scratch/temporary-stem.wav"))
        verify_stem.side_effect = lambda *args, **kwargs: (events.append("head-stem"), object())[1]
        start_stem.return_value = RecordingStemScope(events, running)
        estimate_tempo.return_value = BasicPitchTempoCandidate(
            bpm=None,
            beat_count=0,
            duration_seconds=0.0,
            interval_consistency=0.0,
            credible=False,
            confidence="low",
        )
        build_inference.side_effect = lambda **kwargs: (events.append("build-inference"), object())[1]
        run_inference.side_effect = lambda *args, **kwargs: (events.append("run-inference"), object())[1]
        verify_midi.side_effect = lambda value: (events.append("verify-midi"), object())[1]
        build_output.side_effect = lambda **kwargs: (events.append("plan-midi"), object())[1]
        upload.side_effect = BasicPitchMidiUploadUnavailable("Basic Pitch MIDI upload is unavailable.")

        with self.assertRaises(BasicPitchMidiUploadUnavailable):
            execute_claimed_basic_pitch_task(
                database=MagicMock(),
                storage_client=MagicMock(),
                message=message(),
                lease=lease(),
                work_directory=Path("/pod-scratch"),
            )

        self.assertEqual(
            events,
            ["head-stem", "stem-scope-enter", "build-inference", "run-inference", "verify-midi", "plan-midi", "stem-scope-exit"],
        )
        verify_stored.assert_not_called()
        commit.assert_not_called()

    def test_invalid_public_input_is_rejected_before_any_dependency_call(self) -> None:
        """The coordinator cannot act on an unparsed delivery or fabricated lease."""

        storage_client = MagicMock()
        with self.assertRaisesRegex(TypeError, "message must be BasicPitchRequestedMessage"):
            execute_claimed_basic_pitch_task(
                database=MagicMock(),
                storage_client=storage_client,
                message="not-a-parsed-request",  # type: ignore[arg-type]
                lease=lease(),
                work_directory=Path("/pod-scratch"),
            )
        self.assertEqual(storage_client.method_calls, [])


if __name__ == "__main__":
    unittest.main()
