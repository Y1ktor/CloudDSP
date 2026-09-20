"""In-memory tests for the narrow MinIO and PostgreSQL burst adapters.

The fakes imitate only the reviewed S3/cursor methods. No SDK is imported and
no network, Secret, MinIO object, PostgreSQL row, RabbitMQ queue, container, or
Kubernetes resource is touched by these tests.
"""

from __future__ import annotations

import hashlib
import io
import unittest
from datetime import UTC, datetime

from basic_pitch_keda_burst_smoke import BURST_COORDINATES, build_all_controlled_wavs
from minio_adapter import (
    BasicPitchKedaBurstMinioInfrastructureError,
    MinioBurstAdapter,
)
from postgresql_adapter import (
    BasicPitchKedaBurstPostgresqlInfrastructureError,
    PostgresqlBurstAdapter,
)
from basic_pitch_keda_burst_smoke import BasicPitchKedaBurstContractError


_TASK_IDS = (
    "91156a09-b5d9-4c8c-a8ec-426494c0ee62",
    "b53fc0a6-2e8d-4a12-9cae-01b278b58b9a",
    "c7c74654-bca7-4fea-a81b-135136b9e59c",
)


class S3Error(Exception):
    """Minimal S3-shaped error used to distinguish absence from an outage."""

    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}


class MemoryS3:
    """Store only explicit test objects and record which S3 method/key was used."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[str, str]] = []
        self.failure: Exception | None = None

    def put_object(self, **kwargs: object) -> dict[str, object]:
        if self.failure is not None:
            raise self.failure
        key = str(kwargs["Key"])
        self.calls.append(("put", key))
        self.objects[key] = {
            "Body": bytes(kwargs["Body"]),
            "ContentLength": kwargs["ContentLength"],
            "ContentType": kwargs["ContentType"],
            "Metadata": kwargs["Metadata"],
        }
        return {}

    def head_object(self, **kwargs: object) -> dict[str, object]:
        if self.failure is not None:
            raise self.failure
        key = str(kwargs["Key"])
        self.calls.append(("head", key))
        if key not in self.objects:
            raise S3Error("404")
        value = self.objects[key]
        return {name: value[name] for name in ("ContentLength", "ContentType", "Metadata")}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        if self.failure is not None:
            raise self.failure
        key = str(kwargs["Key"])
        self.calls.append(("get", key))
        return {"Body": io.BytesIO(self.objects[key]["Body"])}

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        if self.failure is not None:
            raise self.failure
        key = str(kwargs["Key"])
        self.calls.append(("delete", key))
        self.objects.pop(key, None)
        return {}


def valid_midi_bytes() -> bytes:
    """Return a compact, one-track Standard MIDI File for output verification."""

    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk\x00\x00\x00\x04\x00\xff\x2f\x00"


def add_valid_midi(s3: MemoryS3, *, coordinate_index: int, input_sha256: str) -> None:
    """Install a worker-shaped MIDI result under one exact contract key."""

    coordinate = BURST_COORDINATES[coordinate_index]
    body = valid_midi_bytes()
    s3.objects[coordinate.midi_key] = {
        "Body": body,
        "ContentLength": len(body),
        "ContentType": "audio/midi",
        "Metadata": {
            "schema-version": "1",
            "producer": "basic-pitch",
            "job-id": coordinate.job_id,
            "task-id": _TASK_IDS[coordinate_index],
            "request-event-id": coordinate.event_id,
            "stem-name": coordinate.stem_name,
            "stem-mode": "4-stems",
            "size-bytes": str(len(body)),
            "sha256": hashlib.sha256(body).hexdigest(),
            "input-stem-sha256": input_sha256,
        },
    }


class Cursor:
    """Return prearranged function rows and retain all SQL calls for assertions."""

    def __init__(self, results: list[list[dict[str, object]]]) -> None:
        self.results = results
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.calls.append((query, params))

    def fetchall(self) -> list[dict[str, object]]:
        return self.results.pop(0)


class Connection:
    """Expose one stateful fake cursor across the expected function-call sequence."""

    def __init__(self, results: list[list[dict[str, object]]]) -> None:
        self.cursor_value = Cursor(results)

    def cursor(self) -> Cursor:
        return self.cursor_value


def prepared_rows() -> list[dict[str, object]]:
    """Return the three database rows allowed from the fixed prepare function."""

    return [
        {
            "request_label": coordinate.label,
            "smoke_job_id": coordinate.job_id,
            "smoke_event_id": coordinate.event_id,
        }
        for coordinate in BURST_COORDINATES
    ]


def observation_rows(*, succeeded: bool) -> list[dict[str, object]]:
    """Return the compact three-row projection before or after worker completion."""

    now = datetime.now(UTC)
    return [
        {
            "request_label": coordinate.label,
            "publication_status": "published" if succeeded else "pending",
            "published_at": now if succeeded else None,
            "task_id": _TASK_IDS[index] if succeeded else None,
            "task_status": "succeeded" if succeeded else None,
            "task_attempt_count": 1 if succeeded else None,
            "task_lease_is_clear": succeeded,
            "task_completed_at": now if succeeded else None,
            "job_status": "midi_processing",
        }
        for index, coordinate in enumerate(BURST_COORDINATES)
    ]


class MinioAdapterTests(unittest.TestCase):
    """Prove storage access remains limited to the three reviewed WAV/MIDI pairs."""

    def test_upload_verify_and_cleanup_use_only_the_six_fixed_keys(self) -> None:
        """The S3 fake never receives a list/prefix/arbitrary-key operation."""

        s3 = MemoryS3()
        adapter = MinioBurstAdapter(s3)
        wavs = build_all_controlled_wavs()
        adapter.assert_all_coordinates_absent()
        adapter.upload_controlled_wavs(wavs)
        for index, wav in enumerate(wavs):
            add_valid_midi(s3, coordinate_index=index, input_sha256=wav.sha256)
        verified = tuple(
            adapter.verify_midi(
                coordinate=coordinate,
                task_id=_TASK_IDS[index],
                input_sha256=wavs[index].sha256,
            )
            for index, coordinate in enumerate(BURST_COORDINATES)
        )
        self.assertEqual([item.coordinate.label for item in verified], ["vocals", "bass", "other"])
        self.assertTrue(all(item.size_bytes == len(valid_midi_bytes()) for item in verified))
        adapter.delete_all_fixed_objects()
        self.assertEqual(s3.objects, {})
        self.assertTrue(all(key in {item.stem_key for item in BURST_COORDINATES} | {item.midi_key for item in BURST_COORDINATES} for _, key in s3.calls))
        self.assertNotIn("list", [method for method, _ in s3.calls])

    def test_present_coordinate_or_minio_outage_is_not_considered_clean(self) -> None:
        """A retained object and an unavailable store are distinct safe failures."""

        s3 = MemoryS3()
        s3.objects[BURST_COORDINATES[0].stem_key] = {"Body": b"x", "ContentLength": 1, "ContentType": "audio/wav", "Metadata": {}}
        with self.assertRaises(BasicPitchKedaBurstContractError):
            MinioBurstAdapter(s3).assert_all_coordinates_absent()
        s3 = MemoryS3()
        s3.failure = S3Error("AccessDenied")
        with self.assertRaises(BasicPitchKedaBurstMinioInfrastructureError):
            MinioBurstAdapter(s3).assert_all_coordinates_absent()

    def test_midi_metadata_cannot_bind_an_output_to_another_request(self) -> None:
        """A matching file body is insufficient when provenance is inconsistent."""

        s3 = MemoryS3()
        wav = build_all_controlled_wavs()[0]
        add_valid_midi(s3, coordinate_index=0, input_sha256=wav.sha256)
        s3.objects[BURST_COORDINATES[0].midi_key]["Metadata"]["request-event-id"] = BURST_COORDINATES[1].event_id
        with self.assertRaises(BasicPitchKedaBurstContractError):
            MinioBurstAdapter(s3).verify_midi(
                coordinate=BURST_COORDINATES[0],
                task_id=_TASK_IDS[0],
                input_sha256=wav.sha256,
            )


class PostgresqlAdapterTests(unittest.TestCase):
    """Prove the adapter calls only restricted functions and validates all rows."""

    def test_prepare_observe_and_cleanup_use_only_the_three_fixed_functions(self) -> None:
        """No direct table DML is available to the eventual restricted database role."""

        connection = Connection([[], prepared_rows(), observation_rows(succeeded=True), [{"cleaned": True}]])
        adapter = PostgresqlBurstAdapter(connection)
        adapter.assert_coordinates_clean()
        adapter.prepare_durable_burst(build_all_controlled_wavs())
        observations = adapter.read_observations()
        self.assertEqual({item.request_label for item in observations}, {"vocals", "bass", "other"})
        self.assertTrue(all(item.task_status == "succeeded" for item in observations))
        adapter.cleanup_successful_burst()
        all_sql = "\n".join(call[0] for call in connection.cursor_value.calls)
        self.assertIn("clouddsp_basic_pitch_keda_burst_smoke_prepare", all_sql)
        self.assertIn("clouddsp_basic_pitch_keda_burst_smoke_observe", all_sql)
        self.assertIn("clouddsp_basic_pitch_keda_burst_smoke_cleanup", all_sql)
        self.assertNotIn("public.jobs", all_sql)
        self.assertNotIn("public.outbox_events", all_sql)
        self.assertNotIn("public.processing_tasks", all_sql)

    def test_unexpected_prepare_result_or_database_outage_fails_closed(self) -> None:
        """A partial success cannot be interpreted as three durable requests."""

        connection = Connection([prepared_rows()[:2]])
        with self.assertRaises(BasicPitchKedaBurstContractError):
            PostgresqlBurstAdapter(connection).prepare_durable_burst(build_all_controlled_wavs())
        connection = Connection([])
        with self.assertRaises(BasicPitchKedaBurstPostgresqlInfrastructureError):
            PostgresqlBurstAdapter(connection).read_observations()

    def test_cleanup_requires_the_database_function_to_confirm_true(self) -> None:
        """The adapter never treats a refused success-only cleanup as deletion."""

        connection = Connection([[{"cleaned": False}]])
        with self.assertRaises(BasicPitchKedaBurstContractError):
            PostgresqlBurstAdapter(connection).cleanup_successful_burst()


if __name__ == "__main__":
    unittest.main()
