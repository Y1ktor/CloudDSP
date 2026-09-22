"""Offline fake-client tests for the fixed-coordinate Demucs smoke client."""

from __future__ import annotations

import hashlib
import io
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

import demucs_worker_smoke as smoke


class S3Error(Exception):
    """Tiny SDK-shaped failure with only an S3 error code for the smoke parser."""

    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}


class Body(io.BytesIO):
    """A BytesIO body that exposes the same close method as a Boto3 stream."""


class RecordingS3:
    """Record only exact-key fake operations without providing bucket listing."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.deleted: list[str] = []

    def put_object(self, **kwargs: object) -> dict[str, object]:
        key = kwargs["Key"]
        assert isinstance(key, str)
        body = kwargs["Body"]
        assert isinstance(body, bytes)
        self.objects[key] = {
            "ContentLength": len(body),
            "ContentType": kwargs["ContentType"],
            "Metadata": dict(kwargs["Metadata"]),
            "Body": body,
        }
        return {}

    def head_object(self, **kwargs: object) -> dict[str, object]:
        key = kwargs["Key"]
        assert isinstance(key, str)
        item = self.objects.get(key)
        if item is None:
            raise S3Error("404")
        return {name: value for name, value in item.items() if name != "Body"}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = kwargs["Key"]
        assert isinstance(key, str)
        item = self.objects.get(key)
        if item is None:
            raise S3Error("404")
        return {"Body": Body(item["Body"])}

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        key = kwargs["Key"]
        assert isinstance(key, str)
        self.deleted.append(key)
        self.objects.pop(key, None)
        return {}


class Cursor:
    """Return the next fixed function row without interpreting SQL text."""

    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.queries.append(query)

    def fetchone(self) -> dict[str, object] | None:
        return self.rows.pop(0) if self.rows else None


class Connection:
    """Expose one autocommit-like recording cursor for a deterministic test."""

    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self.cursor_value = Cursor(rows)
        self.closed = False

    def cursor(self) -> Cursor:
        return self.cursor_value

    def close(self) -> None:
        self.closed = True


def observation_row(**overrides: object) -> dict[str, object]:
    """Return the exact fixed observer shape for valid success-state tests."""

    value: dict[str, object] = {
        "source_event_publication_status": "published",
        "source_event_published_at": datetime.now(timezone.utc),
        "demucs_task_id": UUID("c6706065-6b31-4ce7-a932-d809955a7fd2"),
        "demucs_task_status": "succeeded",
        "demucs_task_attempt_count": 1,
        "demucs_task_lease_is_clear": True,
        "demucs_task_completed_at": datetime.now(timezone.utc),
        "job_status": "midi_processing",
        "stem_count": 2,
        "downstream_event_count": 2,
        "downstream_published_count": 2,
        "basic_pitch_task_count": 2,
        "basic_pitch_succeeded_first_attempt_count": 2,
        "basic_pitch_active_task_count": 0,
        "basic_pitch_failed_task_count": 0,
    }
    value.update(overrides)
    return value


class DemucsWorkerSmokeTests(unittest.TestCase):
    """Keep source stimulus, observation, artifact proof, and cleanup bounded."""

    def test_controlled_wav_is_deterministic_small_stereo_audio(self) -> None:
        first = smoke.build_controlled_wav()
        self.assertEqual(first, smoke.build_controlled_wav())
        self.assertLess(len(first), smoke.MAX_SOURCE_BYTES)
        self.assertEqual(first[:4], b"RIFF")
        self.assertEqual(first[8:12], b"WAVE")

    def test_source_upload_uses_only_normal_upload_metadata_and_checks_head(self) -> None:
        client = RecordingS3()
        source = smoke.build_controlled_wav()
        smoke.upload_and_verify_controlled_source(client, wav_bytes=source, sha256=hashlib.sha256(source).hexdigest())
        self.assertEqual(client.objects[smoke.SOURCE_KEY]["Metadata"], smoke.controlled_source_metadata())
        self.assertEqual(set(client.objects), {smoke.SOURCE_KEY})

    def test_observation_accepts_native_psycopg_uuid_and_requires_exact_shape(self) -> None:
        parsed = smoke._observation_from_row(observation_row())
        self.assertEqual(parsed.demucs_task_id, "c6706065-6b31-4ce7-a932-d809955a7fd2")
        malformed = observation_row()
        malformed.pop("stem_count")
        with self.assertRaises(smoke.DemucsWorkerSmokeAssertionError):
            smoke._observation_from_row(malformed)

    def test_wait_for_demucs_completion_uses_durable_observation_not_queue_read(self) -> None:
        pending = observation_row(source_event_publication_status="pending", source_event_published_at=None,
                                  demucs_task_id=None, demucs_task_status=None,
                                  demucs_task_attempt_count=None, demucs_task_lease_is_clear=False,
                                  demucs_task_completed_at=None, job_status="source_uploaded",
                                  stem_count=0, downstream_event_count=0)
        connection = Connection([pending, observation_row()])
        ticks = iter((0.0, 0.0, 1.0, 1.0))
        result = smoke.wait_for_demucs_completion(
            connection,
            timeout_seconds=5,
            sleep_function=lambda _: None,
            monotonic=lambda: next(ticks),
        )
        self.assertEqual(result.demucs_task_status, "succeeded")
        self.assertEqual(len(connection.cursor_value.queries), 2)

    def test_wait_for_demucs_completion_preserves_terminal_failure_evidence(self) -> None:
        connection = Connection([observation_row(demucs_task_status="failed")])
        with self.assertRaisesRegex(smoke.DemucsWorkerSmokeAssertionError, "terminal task failure"):
            smoke.wait_for_demucs_completion(connection, timeout_seconds=5, sleep_function=lambda _: None)

    def test_stem_verification_requires_current_task_metadata_and_stream_hash(self) -> None:
        client = RecordingS3()
        # Keep the fixture at least the 44-byte minimum asserted by the real
        # client while retaining the smallest possible non-audio test body.
        data = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 32
        digest = hashlib.sha256(data).hexdigest()
        task_id = "c6706065-6b31-4ce7-a932-d809955a7fd2"
        client.objects[smoke.STEM_KEYS[0]] = {
            "ContentLength": len(data),
            "ContentType": "audio/wav",
            "Metadata": {
                "schema-version": "1", "producer": "demucs", "job-id": smoke.SMOKE_JOB_ID,
                "task-id": task_id, "stem-name": "vocals", "stem-mode": smoke.STEM_MODE,
                "size-bytes": str(len(data)), "sha256": digest,
            },
            "Body": data,
        }
        verified = smoke.verify_stored_stem(
            client,
            observation=smoke._observation_from_row(observation_row(demucs_task_id=task_id)),
            stem_name="vocals",
        )
        self.assertEqual(verified.sha256, digest)
        client.objects[smoke.STEM_KEYS[0]]["Metadata"] = dict(client.objects[smoke.STEM_KEYS[0]]["Metadata"], **{"task-id": str(UUID(int=0))})
        with self.assertRaises(smoke.DemucsWorkerSmokeAssertionError):
            smoke.verify_stored_stem(
                client,
                observation=smoke._observation_from_row(observation_row(demucs_task_id=task_id)),
                stem_name="vocals",
            )

    def test_downstream_barrier_rejects_failed_child_without_deleting_evidence(self) -> None:
        connection = Connection([observation_row(basic_pitch_failed_task_count=1)])
        with self.assertRaisesRegex(smoke.DemucsWorkerSmokeAssertionError, "cleanup barrier"):
            smoke.wait_for_downstream_cleanup_barrier(connection, timeout_seconds=5, sleep_function=lambda _: None)

    def test_success_cleanup_deletes_only_fixed_objects_before_guarded_database_call(self) -> None:
        client = RecordingS3()
        connection = Connection([{"cleaned": True}])
        smoke.cleanup_successful_smoke(connection, client)
        self.assertEqual(client.deleted, [*smoke.MIDI_KEYS, *smoke.STEM_KEYS, smoke.SOURCE_KEY])
        self.assertEqual(connection.cursor_value.queries, [smoke.CLEANUP_SQL])

    def test_run_smoke_preserves_source_after_durable_prepare_but_removes_preprepare_upload_residue(self) -> None:
        connection = Connection([])
        client = RecordingS3()
        with patch.object(smoke, "upload_and_verify_controlled_source", side_effect=RuntimeError("upload failed")):
            with self.assertRaises(RuntimeError):
                smoke.run_smoke(connection, client, report=lambda _: None)
        self.assertEqual(client.deleted, [smoke.SOURCE_KEY])


if __name__ == "__main__":
    unittest.main()
