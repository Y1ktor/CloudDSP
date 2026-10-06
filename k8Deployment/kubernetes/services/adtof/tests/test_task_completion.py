"""Unit tests for ADTOF's pure guarded Job-and-task completion SQL adapter.

These tests use a recording cursor. They do not open PostgreSQL, contact MinIO
or RabbitMQ, invoke ADTOF, build an image, or create Kubernetes state.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
import unittest

from app.artifacts.output_artifact import ADTOFTempoCandidate
from app.artifacts.output_artifact_head_object import VerifiedStoredADTOFObject, VerifiedStoredADTOFObjects
from app.db.task_claim import ADTOFTaskLease
from app.db.task_completion import (
    ADTOF_TASK_COMPLETION_FUNCTION,
    COMPLETE_RUNNING_ADTOF_TASK_SQL,
    ADTOFTaskCompletionProtocolError,
    complete_running_adtof_task,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


class FakeCursor:
    """Record one parameterized statement and return one scripted row."""

    def __init__(self, row: dict[str, object] | None) -> None:
        """Keep the test's response independent of a PostgreSQL driver."""

        self.row = row
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        """Record parameterized SQL without evaluating it locally."""

        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        """Return the one row PostgreSQL would have produced, if any."""

        return self.row


def lease(**overrides: object) -> ADTOFTaskLease:
    """Return one exact current drums-task lease for controlled tests."""

    values: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stem_name": "drums",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"stems/{JOB_ID}/drums.wav",
        "stem_mode": "4-stems",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": datetime(2026, 9, 13, 12, 15, tzinfo=UTC),
    }
    values.update(overrides)
    return ADTOFTaskLease(**values)  # type: ignore[arg-type]


def stored_outputs(**overrides: object) -> VerifiedStoredADTOFObjects:
    """Return the preceding HeadObject boundary's fixed MIDI/tempo evidence."""

    values: dict[str, object] = {
        "midi": VerifiedStoredADTOFObject(
            bucket="clouddsp-uploads",
            object_key=f"midi/{JOB_ID}/drums.mid",
            content_type="audio/midi",
            content_length=1200,
            sha256="b" * 64,
        ),
        "tempo_candidate": VerifiedStoredADTOFObject(
            bucket="clouddsp-uploads",
            object_key=f"midi/{JOB_ID}/drums_bpm.json",
            content_type="application/json",
            content_length=220,
            sha256="c" * 64,
        ),
    }
    values.update(overrides)
    return VerifiedStoredADTOFObjects(**values)  # type: ignore[arg-type]


def tempo_candidate(**overrides: object) -> ADTOFTempoCandidate:
    """Return one strict cloud-compatible drum-tempo observation."""

    values: dict[str, object] = {
        "bpm": 120.0,
        "beat_count": 16,
        "duration_seconds": 32.5,
        "interval_consistency": 0.9,
        "drum_event_count": 24,
        "credible": True,
        "confidence": "high",
        "source": "adtof_drums",
    }
    values.update(overrides)
    return ADTOFTempoCandidate(**values)  # type: ignore[arg-type]


class ADTOFTaskCompletionTests(unittest.TestCase):
    """Prove only the exact current lease can create one durable drums result."""

    def test_current_lease_writes_midi_tempo_and_task_success_in_one_statement(self) -> None:
        """The Job revision changes but overall status remains `midi_processing`."""

        completed_at = datetime(2026, 9, 13, 12, 30, tzinfo=UTC)
        cursor = FakeCursor(
            {
                "task_id": TASK_ID,
                "job_id": JOB_ID,
                "completed_at": completed_at,
                "revision": 8,
            }
        )

        completion = complete_running_adtof_task(
            cursor,
            lease=lease(),
            stored_outputs=stored_outputs(),
            tempo_candidate=tempo_candidate(),
        )

        self.assertEqual(completion.task_id if completion else None, TASK_ID)
        self.assertEqual(completion.completed_at if completion else None, completed_at)
        self.assertEqual(completion.resulting_revision if completion else None, 8)
        self.assertEqual(cursor.calls[0][0], COMPLETE_RUNNING_ADTOF_TASK_SQL)
        params = cursor.calls[0][1]
        self.assertEqual(
            params[:8],
            (
                TASK_ID,
                JOB_ID,
                "clouddsp-uploads",
                f"stems/{JOB_ID}/drums.wav",
                "4-stems",
                LEASE_TOKEN,
                f"midi/{JOB_ID}/drums.mid",
                f"midi/{JOB_ID}/drums_bpm.json",
            ),
        )
        self.assertEqual(json.loads(params[8]), {
            "extractor": "adtof",
            "bpm": 120.0,
            "beat_count": 16,
            "duration_seconds": 32.5,
            "interval_consistency": 0.9,
            "drum_event_count": 24,
            "credible": True,
            "confidence": "high",
            "source": "adtof_drums",
        })
        self.assertIn(ADTOF_TASK_COMPLETION_FUNCTION, COMPLETE_RUNNING_ADTOF_TASK_SQL)
        self.assertIn("%s::uuid", COMPLETE_RUNNING_ADTOF_TASK_SQL)
        self.assertIn("%s::jsonb", COMPLETE_RUNNING_ADTOF_TASK_SQL)
        self.assertNotIn("UPDATE public.jobs", COMPLETE_RUNNING_ADTOF_TASK_SQL)
        self.assertNotIn("status = 'completed'", COMPLETE_RUNNING_ADTOF_TASK_SQL)

    def test_lost_lease_returns_none_without_inventing_a_completion(self) -> None:
        """Expiry, recovery, deletion, or a prior result is a normal no-row outcome."""

        cursor = FakeCursor(None)

        self.assertIsNone(
            complete_running_adtof_task(
                cursor,
                lease=lease(),
                stored_outputs=stored_outputs(),
                tempo_candidate=tempo_candidate(),
            )
        )
        self.assertEqual(len(cursor.calls), 1)

    def test_foreign_output_or_invalid_tempo_never_reaches_the_database_cursor(self) -> None:
        """Stored private keys and tempo structure are rechecked before SQL runs."""

        cursor = FakeCursor(None)
        with self.assertRaises(ADTOFTaskCompletionProtocolError):
            complete_running_adtof_task(
                cursor,
                lease=lease(),
                stored_outputs=stored_outputs(
                    midi=replace(stored_outputs().midi, object_key=f"midi/{JOB_ID}/bass.mid")
                ),
                tempo_candidate=tempo_candidate(),
            )
        self.assertEqual(cursor.calls, [])

        with self.assertRaises(ADTOFTaskCompletionProtocolError):
            complete_running_adtof_task(
                cursor,
                lease=lease(),
                stored_outputs=stored_outputs(),
                tempo_candidate=tempo_candidate(credible=True, confidence="low"),
            )
        self.assertEqual(cursor.calls, [])

    def test_malformed_returned_row_never_fabricates_success(self) -> None:
        """A driver/cursor must return the exact durable identity and UTC time."""

        cursor = FakeCursor(
            {
                "task_id": TASK_ID,
                "job_id": JOB_ID,
                "completed_at": "not-a-time",
                "revision": 2,
            }
        )

        with self.assertRaises(ADTOFTaskCompletionProtocolError):
            complete_running_adtof_task(
                cursor,
                lease=lease(),
                stored_outputs=stored_outputs(),
                tempo_candidate=tempo_candidate(),
            )
        self.assertEqual(len(cursor.calls), 1)


if __name__ == "__main__":
    unittest.main()
