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


class BasicPitchMidiTaskCompletionTests(unittest.TestCase):
    """Prove stored object evidence can complete only the exact current task lease."""

    def test_current_running_token_marks_only_the_task_succeeded(self) -> None:
        """The SQL does not alter the overall Job before every MIDI-stage task is done."""

        completed_at = datetime(2026, 9, 11, 12, 30, tzinfo=UTC)
        cursor = FakeCursor({"task_id": TASK_ID, "job_id": JOB_ID, "completed_at": completed_at})

        completion = complete_running_basic_pitch_task(
            cursor,
            lease=lease(),
            stored_midi=stored_midi(),
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
            ),
        )
        self.assertIn("status = 'running'", COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL)
        self.assertIn("status = 'succeeded'", COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL)
        self.assertIn("lease_token = %s::uuid", COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL)
        self.assertIn("lease_expires_at > CURRENT_TIMESTAMP", COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL)
        self.assertNotIn("UPDATE public.jobs", COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL)

    def test_lost_current_lease_returns_none_without_fabricating_success(self) -> None:
        """Expiry/recovery/deletion is a normal no-row stop signal for a stale worker."""

        cursor = FakeCursor(None)

        self.assertIsNone(
            complete_running_basic_pitch_task(
                cursor,
                lease=lease(),
                stored_midi=stored_midi(),
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
            )
        self.assertEqual(cursor.calls, [])

        malformed_cursor = FakeCursor({"task_id": TASK_ID, "job_id": JOB_ID, "completed_at": "not-a-time"})
        with self.assertRaises(BasicPitchMidiTaskCompletionProtocolError):
            complete_running_basic_pitch_task(
                malformed_cursor,
                lease=lease(),
                stored_midi=stored_midi(),
            )
        self.assertEqual(len(malformed_cursor.calls), 1)
