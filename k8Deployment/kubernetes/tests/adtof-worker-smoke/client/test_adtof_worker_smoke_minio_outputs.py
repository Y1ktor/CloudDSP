"""Unit tests for the read-only fixed ADTOF MIDI/tempo smoke-output adapter.

All S3 responses are in-memory fakes.  The tests import no Boto3, open no
socket, and do not create/delete MinIO objects, PostgreSQL rows, RabbitMQ
messages, Kubernetes resources, or model output.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest
from uuid import uuid4

from adtof_worker_smoke_contract import ADTOFWorkerSmokeContractError, FixedObjectEvidence
from adtof_worker_smoke_fixture import (
    MIDI_KEY,
    SMOKE_EVENT_ID,
    SMOKE_JOB_ID,
    STEM_KEY,
    STEM_MODE,
    STEM_NAME,
    TEMPO_KEY,
)
from adtof_worker_smoke_minio_outputs import (
    ADTOFWorkerSmokeOutputProtocolError,
    FixedKeyADTOFWorkerSmokeMinIOOutputAdapter,
)


class FakeS3Error(Exception):
    """Minimal S3-shaped error without a vendor SDK exception class."""

    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}
        super().__init__("private fake S3 diagnostic")


class FakeBody:
    """A deterministic, inspectable response stream for bounded-read tests."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._position = 0
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self._data) - self._position
        result = self._data[self._position : self._position + amount]
        self._position += len(result)
        return result

    def close(self) -> None:
        self.closed = True


class FakeS3Client:
    """Expose exactly the reader's HeadObject/GetObject surface in memory."""

    def __init__(self, heads: list[object], gets: list[object]) -> None:
        self.heads = heads
        self.gets = gets
        self.head_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []

    def head_object(self, **kwargs: object) -> object:
        self.head_calls.append(dict(kwargs))
        result = self.heads.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def get_object(self, **kwargs: object) -> object:
        self.get_calls.append(dict(kwargs))
        result = self.gets.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _midi_bytes() -> bytes:
    """Return a smallest valid format-zero Standard MIDI File with one track."""

    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk\x00\x00\x00\x04\x00\xff\x2f\x00"


def _tempo_bytes(*, credible: bool = True) -> bytes:
    """Return complete cloud-compatible ADTOF tempo JSON for the fixed short clip."""

    value = {
        "extractor": "adtof",
        "bpm": 120.0 if credible else None,
        "beat_count": 8,
        "duration_seconds": 4.0,
        "interval_consistency": 0.95,
        "drum_event_count": 16,
        "credible": credible,
        "confidence": "high" if credible else "low",
        "source": "adtof_drums",
    }
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _input_evidence() -> FixedObjectEvidence:
    """Return the exact type/key-shaped proof from the earlier input adapter."""

    return FixedObjectEvidence(
        key=STEM_KEY,
        content_type="audio/wav",
        size_bytes=44,
        sha256="a" * 64,
    )


def _headers(*, key: str, body: bytes, task_id: str, input_sha256: str) -> dict[str, object]:
    """Build the complete current S3 evidence the deployed worker is expected to write."""

    is_midi = key == MIDI_KEY
    sha256 = hashlib.sha256(body).hexdigest()
    return {
        "ContentLength": len(body),
        "ContentType": "audio/midi" if is_midi else "application/json",
        "Metadata": {
            "schema-version": "1",
            "producer": "adtof",
            "job-id": SMOKE_JOB_ID,
            "task-id": task_id,
            "request-event-id": SMOKE_EVENT_ID,
            "stem-name": STEM_NAME,
            "stem-mode": STEM_MODE,
            "artifact-kind": "drum-midi" if is_midi else "tempo-candidate",
            "input-stem-sha256": input_sha256,
            "model-config-id": (
                "adtof-pytorch@85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9"
                ";fps=100;thresholds=0.22,0.24,0.32,0.22,0.30;device=cpu"
            ),
            "size-bytes": str(len(body)),
            "sha256": sha256,
        },
    }


def _get_response(headers: dict[str, object], body: bytes) -> tuple[dict[str, object], FakeBody]:
    """Return a GetObject-shaped response and its body so a test can inspect closure."""

    stream = FakeBody(body)
    return ({**headers, "Body": stream}, stream)


class FixedKeyADTOFWorkerSmokeMinIOOutputAdapterTests(unittest.TestCase):
    """Prove output evidence remains read-only, fixed-coordinate, and complete."""

    def test_reads_head_and_streams_the_exact_midi_tempo_pair(self) -> None:
        """Valid worker bytes must match fixed coordinates, metadata, and hashes."""

        task_id = str(uuid4())
        midi = _midi_bytes()
        tempo = _tempo_bytes()
        midi_head = _headers(key=MIDI_KEY, body=midi, task_id=task_id, input_sha256="a" * 64)
        tempo_head = _headers(key=TEMPO_KEY, body=tempo, task_id=task_id, input_sha256="a" * 64)
        midi_get, midi_stream = _get_response(midi_head, midi)
        tempo_get, tempo_stream = _get_response(tempo_head, tempo)
        client = FakeS3Client([midi_head, tempo_head], [midi_get, tempo_get])

        result = FixedKeyADTOFWorkerSmokeMinIOOutputAdapter(client).read_verified_adtof_outputs(
            task_id=task_id,
            input_stem=_input_evidence(),
        )

        self.assertEqual(result.midi.key, MIDI_KEY)
        self.assertEqual(result.midi.sha256, hashlib.sha256(midi).hexdigest())
        self.assertEqual(result.tempo.key, TEMPO_KEY)
        self.assertEqual(result.tempo.sha256, hashlib.sha256(tempo).hexdigest())
        self.assertEqual(
            client.head_calls,
            [
                {"Bucket": "clouddsp-uploads", "Key": MIDI_KEY},
                {"Bucket": "clouddsp-uploads", "Key": TEMPO_KEY},
            ],
        )
        self.assertEqual(
            client.get_calls,
            [
                {"Bucket": "clouddsp-uploads", "Key": MIDI_KEY},
                {"Bucket": "clouddsp-uploads", "Key": TEMPO_KEY},
            ],
        )
        self.assertTrue(midi_stream.closed)
        self.assertTrue(tempo_stream.closed)

    def test_rejects_wrong_provenance_or_stale_stream_bytes_without_reading_the_other_key(self) -> None:
        """A task/input mismatch or TOCTOU-style body mismatch can never be success."""

        task_id = str(uuid4())
        midi = _midi_bytes()
        wrong_task = _headers(key=MIDI_KEY, body=midi, task_id=str(uuid4()), input_sha256="a" * 64)
        provenance_client = FakeS3Client([wrong_task], [])
        with self.assertRaises(ADTOFWorkerSmokeOutputProtocolError):
            FixedKeyADTOFWorkerSmokeMinIOOutputAdapter(provenance_client).read_verified_adtof_outputs(
                task_id=task_id,
                input_stem=_input_evidence(),
            )
        self.assertEqual(provenance_client.get_calls, [])

        midi_head = _headers(key=MIDI_KEY, body=midi, task_id=task_id, input_sha256="a" * 64)
        changed_get, changed_stream = _get_response(midi_head, midi + b"x")
        changed_client = FakeS3Client([midi_head], [changed_get])
        with self.assertRaises(ADTOFWorkerSmokeOutputProtocolError):
            FixedKeyADTOFWorkerSmokeMinIOOutputAdapter(changed_client).read_verified_adtof_outputs(
                task_id=task_id,
                input_stem=_input_evidence(),
            )
        self.assertTrue(changed_stream.closed)
        self.assertEqual(changed_client.head_calls, [{"Bucket": "clouddsp-uploads", "Key": MIDI_KEY}])

    def test_rejects_malformed_midi_tempo_or_missing_output(self) -> None:
        """Format validation complements metadata checks without model-quality assertions."""

        task_id = str(uuid4())
        bad_midi = b"not-midi"
        bad_midi_headers = _headers(key=MIDI_KEY, body=bad_midi, task_id=task_id, input_sha256="a" * 64)
        bad_midi_get, _ = _get_response(bad_midi_headers, bad_midi)
        with self.assertRaises(ADTOFWorkerSmokeOutputProtocolError):
            FixedKeyADTOFWorkerSmokeMinIOOutputAdapter(
                FakeS3Client([bad_midi_headers], [bad_midi_get])
            ).read_verified_adtof_outputs(task_id=task_id, input_stem=_input_evidence())

        midi = _midi_bytes()
        midi_head = _headers(key=MIDI_KEY, body=midi, task_id=task_id, input_sha256="a" * 64)
        midi_get, _ = _get_response(midi_head, midi)
        invalid_tempo = b'{"extractor":"adtof"}'
        tempo_head = _headers(key=TEMPO_KEY, body=invalid_tempo, task_id=task_id, input_sha256="a" * 64)
        tempo_get, _ = _get_response(tempo_head, invalid_tempo)
        with self.assertRaises(ADTOFWorkerSmokeOutputProtocolError):
            FixedKeyADTOFWorkerSmokeMinIOOutputAdapter(
                FakeS3Client([midi_head, tempo_head], [midi_get, tempo_get])
            ).read_verified_adtof_outputs(task_id=task_id, input_stem=_input_evidence())

        missing_client = FakeS3Client([FakeS3Error("NoSuchKey")], [])
        with self.assertRaises(ADTOFWorkerSmokeOutputProtocolError):
            FixedKeyADTOFWorkerSmokeMinIOOutputAdapter(missing_client).read_verified_adtof_outputs(
                task_id=task_id,
                input_stem=_input_evidence(),
            )

    def test_rejects_noncanonical_task_or_non_input_evidence_and_exposes_no_mutation_api(self) -> None:
        """The read-only adapter cannot be repurposed for a different task or key set."""

        adapter = FixedKeyADTOFWorkerSmokeMinIOOutputAdapter(FakeS3Client([], []))
        with self.assertRaises(ADTOFWorkerSmokeContractError):
            adapter.read_verified_adtof_outputs(task_id=str(uuid4()).upper(), input_stem=_input_evidence())
        with self.assertRaises(ADTOFWorkerSmokeContractError):
            adapter.read_verified_adtof_outputs(
                task_id=str(uuid4()),
                input_stem=FixedObjectEvidence(
                    key=MIDI_KEY,
                    content_type="audio/midi",
                    size_bytes=1,
                    sha256="a" * 64,
                ),
            )
        self.assertFalse(hasattr(adapter, "delete_object"))
        self.assertFalse(hasattr(adapter, "put_object"))
        self.assertFalse(hasattr(adapter, "list_objects"))
        self.assertFalse(hasattr(adapter, "get_object"))

        source = (Path(__file__).parent / "adtof_worker_smoke_minio_outputs.py").read_text(
            encoding="utf-8"
        )
        for forbidden_import in ("import boto3", "import psycopg", "import pika", "import requests"):
            self.assertNotIn(forbidden_import, source)


if __name__ == "__main__":
    unittest.main()
