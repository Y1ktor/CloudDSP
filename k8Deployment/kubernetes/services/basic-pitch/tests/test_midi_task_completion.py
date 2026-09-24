"""Unit tests for the pure Basic Pitch stored-MIDI task-completion SQL adapter.

These tests use a recording cursor only. They do not open PostgreSQL, MinIO,
RabbitMQ, Basic Pitch, Docker, or Kubernetes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import unittest

from app.midi_artifact_head_object import VerifiedStoredBasicPitchMidiObject
from app.midi_task_completion import (
    COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL,
    BasicPitchMidiTaskCompletionProtocolError,
    complete_running_basic_pitch_task,
)
from app.task_lease import BasicPitchTaskLease
from app.tempo_candidate import BasicPitchTempoCandidate


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
OUTPUT_SHA256 = "b" * 64


class FakeCursor:
    """Record the one parameterized statement and return one scripted row."""

    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        return self.row


def lease(**overrides: object) -> BasicPitchTaskLease:
    """Return one running Basic Pitch task's immutable lease identity."""

    values: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stem_name": "vocals",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"stems/{JOB_ID}/vocals.wav",
        "stem_mode": "4-stems",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": datetime(2026, 9, 11, 12, 15, tzinfo=UTC),
    }
    values.update(overrides)
    return BasicPitchTaskLease(**values)  # type: ignore[arg-type]


def stored_midi(**overrides: object) -> VerifiedStoredBasicPitchMidiObject:
    """Return HeadObject-proven private MIDI evidence for the matching vocals task."""

    values: dict[str, object] = {
        "bucket": "clouddsp-uploads",
        "object_key": f"midi/{JOB_ID}/vocals.mid",
        "content_length": 1200,
        "sha256": OUTPUT_SHA256,
    }
    values.update(overrides)
    return VerifiedStoredBasicPitchMidiObject(**values)  # type: ignore[arg-type]


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


class BasicPitchMidiTaskCompletionTests(unittest.TestCase):
    """Prove stored object evidence can complete only the exact current task lease."""

    def test_current_running_token_persists_artifact_and_completes_only_its_task(self) -> None:
        """The typed database capability commits output evidence with this stem task."""

        completed_at = datetime(2026, 9, 11, 12, 30, tzinfo=UTC)
        cursor = FakeCursor({"task_id": TASK_ID, "job_id": JOB_ID, "completed_at": completed_at})

        completion = complete_running_basic_pitch_task(
            cursor,
            lease=lease(),
            stored_midi=stored_midi(),
            tempo_candidate=tempo_candidate(),
        )

        self.assertEqual(completion.task_id if completion else None, TASK_ID)
        self.assertEqual(completion.completed_at if completion else None, completed_at)
        self.assertEqual(cursor.calls[0][0], COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL)
        self.assertEqual(
            cursor.calls[0][1],
            (
                TASK_ID,
                JOB_ID,
                "vocals",
                "clouddsp-uploads",
                f"stems/{JOB_ID}/vocals.wav",
                "4-stems",
                LEASE_TOKEN,
                f"midi/{JOB_ID}/vocals.mid",
                1200,
                OUTPUT_SHA256,
                '{"bpm":120.0,"beat_count":8,"duration_seconds":10.0,'
                '"interval_consistency":0.9,"credible":true,'
                '"confidence":"medium","source":"librosa_stem"}',
            ),
        )
        self.assertIn("clouddsp_complete_basic_pitch_task", COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL)
        self.assertIn("%s::bigint", COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL)

    def test_lost_current_lease_returns_none_without_fabricating_success(self) -> None:
        """Expiry/recovery/deletion is a normal no-row stop signal for a stale worker."""

        cursor = FakeCursor(None)

        self.assertIsNone(
            complete_running_basic_pitch_task(
                cursor,
                lease=lease(),
                stored_midi=stored_midi(),
                tempo_candidate=tempo_candidate(),
            )
        )
        self.assertEqual(len(cursor.calls), 1)

    def test_foreign_stored_key_or_invalid_returned_row_never_executes_successfully(self) -> None:
        """A stored MIDI proof cannot complete another stem or hide driver corruption."""

        cursor = FakeCursor(None)
        with self.assertRaises(BasicPitchMidiTaskCompletionProtocolError):
            complete_running_basic_pitch_task(
                cursor,
                lease=lease(),
                stored_midi=stored_midi(object_key=f"midi/{JOB_ID}/bass.mid"),
                tempo_candidate=tempo_candidate(),
            )
        self.assertEqual(cursor.calls, [])

        malformed_cursor = FakeCursor({"task_id": TASK_ID, "job_id": JOB_ID, "completed_at": "not-a-time"})
        with self.assertRaises(BasicPitchMidiTaskCompletionProtocolError):
            complete_running_basic_pitch_task(
                malformed_cursor,
                lease=lease(),
                stored_midi=stored_midi(),
                tempo_candidate=tempo_candidate(),
            )
        self.assertEqual(len(malformed_cursor.calls), 1)
