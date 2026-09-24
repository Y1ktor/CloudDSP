"""Unit tests for committing one verified Basic Pitch task completion.

The tests use a recording context manager and patch the pure SQL adapter. They
do not connect to PostgreSQL, MinIO, RabbitMQ, Basic Pitch, Docker, or
Kubernetes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import unittest
from unittest.mock import patch

from app.midi_artifact_head_object import VerifiedStoredBasicPitchMidiObject
from app.midi_task_completion import (
    BasicPitchMidiTaskCompletion,
    BasicPitchMidiTaskCompletionProtocolError,
)
from app.midi_task_completion_commit import commit_verified_basic_pitch_midi_task
from app.task_lease import BasicPitchTaskLease
from app.tempo_candidate import BasicPitchTempoCandidate


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


class RecordingDatabase:
    """Expose normal commit/exception rollback ordering without a database server."""

    def __init__(self) -> None:
        self.cursor = object()
        self.events: list[str] = []

    @contextmanager
    def write_cursor(self) -> Iterator[object]:
        """Model the restricted client's explicit short transaction scope."""

        self.events.append("transaction-open")
        try:
            yield self.cursor
        except Exception:
            self.events.append("transaction-rollback")
            raise
        else:
            self.events.append("transaction-commit")


def lease() -> BasicPitchTaskLease:
    """Return one running per-stem Basic Pitch lease for the composition tests."""

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


def stored_midi() -> VerifiedStoredBasicPitchMidiObject:
    """Return already-HeadObject-verified output evidence for the matching task."""

    return VerifiedStoredBasicPitchMidiObject(
        bucket="clouddsp-uploads",
        object_key=f"midi/{JOB_ID}/vocals.mid",
        content_length=1200,
        sha256="b" * 64,
    )


def tempo_candidate() -> BasicPitchTempoCandidate:
    """Return one credible cloud-compatible stem BPM estimate."""

    return BasicPitchTempoCandidate(
        bpm=120.0,
        beat_count=8,
        duration_seconds=10.0,
        interval_consistency=0.9,
        credible=True,
        confidence="medium",
    )


class BasicPitchMidiTaskCompletionCommitTests(unittest.TestCase):
    """Prove success only escapes after commit and errors roll back before return."""

    @patch("app.midi_task_completion_commit.complete_running_basic_pitch_task")
    def test_returns_completion_only_after_the_short_transaction_commits(self, complete) -> None:
        """No MinIO/model/broker work shares this short PostgreSQL transaction."""

        database = RecordingDatabase()
        completion = BasicPitchMidiTaskCompletion(
            task_id=TASK_ID,
            job_id=JOB_ID,
            completed_at=datetime(2026, 9, 11, 12, 30, tzinfo=UTC),
        )
        complete.return_value = completion

        returned = commit_verified_basic_pitch_midi_task(
            database=database,  # type: ignore[arg-type]
            lease=lease(),
            stored_midi=stored_midi(),
            tempo_candidate=tempo_candidate(),
        )

        self.assertIs(returned, completion)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        complete.assert_called_once_with(
            database.cursor,
            lease=lease(),
            stored_midi=stored_midi(),
            tempo_candidate=tempo_candidate(),
        )

    @patch("app.midi_task_completion_commit.complete_running_basic_pitch_task")
    def test_stale_owner_commits_no_mutation_then_returns_none(self, complete) -> None:
        """A no-row SQL result is normal and never becomes fabricated completion evidence."""

        database = RecordingDatabase()
        complete.return_value = None

        self.assertIsNone(
            commit_verified_basic_pitch_midi_task(
                database=database,  # type: ignore[arg-type]
                lease=lease(),
                stored_midi=stored_midi(),
                tempo_candidate=tempo_candidate(),
            )
        )
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])

    @patch("app.midi_task_completion_commit.complete_running_basic_pitch_task")
    def test_completion_validation_failure_rolls_back_before_the_caller_sees_it(self, complete) -> None:
        """A malformed SQL result cannot leave a partial task mutation committed."""

        database = RecordingDatabase()
        complete.side_effect = BasicPitchMidiTaskCompletionProtocolError(
            "Basic Pitch MIDI task completion evidence is invalid."
        )

        with self.assertRaises(BasicPitchMidiTaskCompletionProtocolError):
            commit_verified_basic_pitch_midi_task(
                database=database,  # type: ignore[arg-type]
                lease=lease(),
                stored_midi=stored_midi(),
                tempo_candidate=tempo_candidate(),
            )
        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])
