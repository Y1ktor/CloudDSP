"""Isolated tests for the Basic Pitch worker smoke client.

These fakes prove the future client follows its strict database/MinIO contract
without importing Boto3/Psycopg, contacting a cluster, creating an object, or
publishing/consuming a RabbitMQ message.
"""

from __future__ import annotations

import hashlib
import io
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock

from basic_pitch_worker_smoke import (
    MIDI_KEY,
    SMOKE_EVENT_ID,
    SMOKE_JOB_ID,
    STEM_KEY,
    BasicPitchWorkerSmokeAssertionError,
    BasicPitchWorkerSmokeConfigurationError,
    BasicPitchWorkerSmokeInfrastructureError,
    TaskObservation,
    assert_object_absent,
    build_controlled_wav,
    cleanup_successful_smoke,
    controlled_stem_metadata,
    run_smoke,
    verify_stored_midi,
)


class S3Error(Exception):
    """Minimal S3-shaped exception for absence/error classification tests."""

    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}


class Cursor:
    """Record function-only SQL calls and return queued dictionary rows."""

    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        return self.rows.pop(0)


class Connection:
    """Expose the same cursor for a known sequence of one-row function calls."""

    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self.cursor_value = Cursor(rows)

    def cursor(self) -> Cursor:
        return self.cursor_value

    def close(self) -> None:
        return None


class S3:
    """Small in-memory S3 fake that records exact coordinate operations."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[str, str]] = []
        # A successful worker cannot produce MIDI until after the first output
        # preflight. This deferred record lets the integration fake model that
        # real asynchronous ordering instead of starting with a stale object.
        self.deferred_midi: dict[str, object] | None = None
        self.midi_absence_confirmed = False

    def put_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        self.calls.append(("put", key))
        self.objects[key] = {
            "ContentLength": kwargs["ContentLength"],
            "ContentType": kwargs["ContentType"],
            "Metadata": kwargs["Metadata"],
            "Body": bytes(kwargs["Body"]),
        }
        return {}

    def head_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        self.calls.append(("head", key))
        if key not in self.objects:
            if key == MIDI_KEY and self.deferred_midi is not None and self.midi_absence_confirmed:
                self.objects[key] = self.deferred_midi
                self.deferred_midi = None
            elif key == MIDI_KEY:
                self.midi_absence_confirmed = True
                raise S3Error("404")
            else:
                raise S3Error("404")
        value = self.objects[key]
        return {name: value[name] for name in ("ContentLength", "ContentType", "Metadata")}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        self.calls.append(("get", key))
        value = self.objects[key]
        return {"Body": io.BytesIO(value["Body"])}

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        self.calls.append(("delete", key))
        self.objects.pop(key, None)
        return {}


def observation_row(*, succeeded: bool) -> dict[str, object]:
    """Return the compact database projection before or after worker success."""

    now = datetime.now(UTC)
    return {
        "publication_status": "published" if succeeded else "pending",
        "published_at": now if succeeded else None,
        "task_id": "ab8b30e9-92f6-4d3b-b2e0-a836289fdbef" if succeeded else None,
        "task_status": "succeeded" if succeeded else None,
        "task_attempt_count": 1 if succeeded else None,
        "task_lease_is_clear": succeeded,
        "task_completed_at": now if succeeded else None,
        "job_status": "midi_processing",
    }


def midi_bytes() -> bytes:
    """Return a tiny valid single-track Standard MIDI File for storage tests."""

    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk\x00\x00\x00\x04\x00\xff\x2f\x00"


def add_valid_midi(s3: S3, *, task_id: str, input_sha256: str) -> None:
    """Install an object shaped exactly like a real Basic Pitch stored result."""

    body = midi_bytes()
    s3.deferred_midi = {
        "ContentLength": len(body),
        "ContentType": "audio/midi",
        "Metadata": {
            "schema-version": "1",
            "producer": "basic-pitch",
            "job-id": SMOKE_JOB_ID,
            "task-id": task_id,
            "request-event-id": SMOKE_EVENT_ID,
            "stem-name": "vocals",
            "stem-mode": "2-stems",
            "size-bytes": str(len(body)),
            "sha256": hashlib.sha256(body).hexdigest(),
            "input-stem-sha256": input_sha256,
        },
        "Body": body,
    }


class ControlledWavTests(unittest.TestCase):
    """Prove the generated source is stable and carries complete provenance."""

    def test_wav_is_deterministic_and_valid(self) -> None:
        """No random/file input can change the source evidence between calls."""

        first = build_controlled_wav()
        self.assertEqual(first, build_controlled_wav())
        self.assertEqual(first[:4], b"RIFF")
        self.assertEqual(first[8:12], b"WAVE")

    def test_metadata_has_only_the_worker_required_demucs_fields(self) -> None:
        """The synthetic object must pass the same metadata gate as a stem."""

        metadata = controlled_stem_metadata(size_bytes=44, sha256="a" * 64)
        self.assertEqual(metadata["job-id"], SMOKE_JOB_ID)
        self.assertEqual(metadata["task-id"], "3ed3ddfa-3165-4365-af60-5ae4f7f656ba")
        self.assertEqual(set(metadata), {"schema-version", "producer", "job-id", "task-id", "stem-name", "stem-mode", "size-bytes", "sha256"})


class StorageSafetyTests(unittest.TestCase):
    """Prove preflight/verification stay on the two fixed object coordinates."""

    def test_absence_accepts_only_a_definite_not_found(self) -> None:
        """An outage must not be mistaken for a clean coordinate."""

        client = MagicMock()
        client.head_object.side_effect = S3Error("404")
        assert_object_absent(client, object_key=STEM_KEY)
        client.head_object.side_effect = S3Error("AccessDenied")
        with self.assertRaises(BasicPitchWorkerSmokeInfrastructureError):
            assert_object_absent(client, object_key=STEM_KEY)

    def test_absence_rejects_an_object_outside_the_two_fixed_coordinates(self) -> None:
        """A helper caller cannot turn this narrow test into prefix access."""

        with self.assertRaises(BasicPitchWorkerSmokeConfigurationError):
            assert_object_absent(S3(), object_key="stems/another-job/vocals.wav")

    def test_midi_rejects_metadata_for_another_worker_task(self) -> None:
        """A result must bind to the dynamically observed task, not just Job ID."""

        client = S3()
        input_hash = "b" * 64
        add_valid_midi(client, task_id="ab8b30e9-92f6-4d3b-b2e0-a836289fdbef", input_sha256=input_hash)
        # This unit test begins after a hypothetical worker completion, unlike
        # the full-sequence test that first proves the output key is absent.
        client.midi_absence_confirmed = True
        with self.assertRaises(BasicPitchWorkerSmokeAssertionError):
            verify_stored_midi(
                client,
                observation=TaskObservation("published", datetime.now(UTC), "f63a37bf-3ac6-4dbf-b920-58df1b0e89c4", "succeeded", 1, True, datetime.now(UTC), "midi_processing"),
                input_sha256=input_hash,
            )


class FullSequenceTests(unittest.TestCase):
    """Prove the successful order uses DB functions, not raw table/AMQP calls."""

    def test_successful_sequence_uploads_waits_verifies_and_cleans(self) -> None:
        """The only automatic cleanup occurs after worker/MIDI evidence exists."""

        wav = build_controlled_wav()
        stem_hash = hashlib.sha256(wav).hexdigest()
        completed = observation_row(succeeded=True)
        connection = Connection(
            [
                None,
                {"smoke_job_id": SMOKE_JOB_ID, "smoke_event_id": SMOKE_EVENT_ID},
                completed,
                {"cleaned": True},
            ]
        )
        client = S3()
        add_valid_midi(client, task_id=str(completed["task_id"]), input_sha256=stem_hash)
        messages: list[str] = []

        verified = run_smoke(connection, client, timeout_seconds=5, report=messages.append)

        self.assertEqual(verified.size_bytes, len(midi_bytes()))
        self.assertNotIn(STEM_KEY, client.objects)
        self.assertNotIn(MIDI_KEY, client.objects)
        self.assertEqual(
            [call[0].strip().split()[0] for call in connection.cursor_value.calls],
            ["SELECT", "SELECT", "SELECT", "SELECT"],
        )
        all_sql = "\n".join(call[0] for call in connection.cursor_value.calls)
        self.assertNotIn("public.jobs", all_sql)
        self.assertIn("smoke_prepare", all_sql)
        self.assertIn("smoke_cleanup", all_sql)
        self.assertEqual(messages[-1], "Basic Pitch worker smoke passed")

    def test_timeout_preserves_durable_evidence_for_diagnosis(self) -> None:
        """A post-prepare timeout must not delete input/output/Job evidence."""

        connection = Connection(
            [
                None,
                {"smoke_job_id": SMOKE_JOB_ID, "smoke_event_id": SMOKE_EVENT_ID},
                observation_row(succeeded=False),
            ]
        )
        client = S3()
        clock = iter((0.0, 0.0, 1.0))
        with self.assertRaises(BasicPitchWorkerSmokeAssertionError):
            run_smoke(
                connection,
                client,
                timeout_seconds=1,
                sleep_function=MagicMock(),
                monotonic=lambda: next(clock),
            )
        self.assertIn(STEM_KEY, client.objects)
        self.assertNotIn(("delete", STEM_KEY), client.calls)

    def test_cleanup_requires_both_object_deletes_before_database_function(self) -> None:
        """A database row cannot disappear before storage cleanup is attempted."""

        connection = Connection([{"cleaned": True}])
        client = S3()
        cleanup_successful_smoke(connection, client)
        self.assertEqual(client.calls, [("delete", MIDI_KEY), ("delete", STEM_KEY)])
        self.assertIn("smoke_cleanup", connection.cursor_value.calls[0][0])


if __name__ == "__main__":
    unittest.main()
