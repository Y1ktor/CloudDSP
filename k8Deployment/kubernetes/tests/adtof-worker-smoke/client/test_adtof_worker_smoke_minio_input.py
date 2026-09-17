"""Unit tests for fixed-key ADTOF smoke MinIO input handling.

The fake S3 client records method calls in memory. Tests import no Boto3 and
never contact MinIO/PostgreSQL/RabbitMQ/Kubernetes or invoke the ADTOF model.
"""

from __future__ import annotations

from dataclasses import replace
import unittest

from adtof_worker_smoke_contract import ADTOFWorkerSmokeContractError
from adtof_worker_smoke_fixture import MIDI_KEY, STEM_KEY, TEMPO_KEY, build_controlled_drum_fixture
from adtof_worker_smoke_minio_input import (
    ADTOFWorkerSmokeDirtyStateError,
    ADTOFWorkerSmokeObjectStoreInfrastructureError,
    ADTOFWorkerSmokeObjectStoreProtocolError,
    FixedKeyADTOFWorkerSmokeMinIOInputAdapter,
)


class FakeS3Error(Exception):
    """Minimal S3-shaped error without importing a vendor exception type."""

    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}
        super().__init__("private fake S3 diagnostic")


class FakeS3Client:
    """Record only the two S3 methods admitted by this focused adapter."""

    def __init__(self) -> None:
        self.head_results: list[object] = []
        self.put_result: object = {"ETag": "opaque"}
        self.put_calls: list[dict[str, object]] = []
        self.head_calls: list[dict[str, object]] = []

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.head_calls.append(dict(kwargs))
        result = self.head_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self.put_calls.append(dict(kwargs))
        if isinstance(self.put_result, BaseException):
            raise self.put_result
        return self.put_result  # type: ignore[return-value]


def _missing_three() -> list[object]:
    """Return the only three accepted clean-state HeadObject results."""

    return [FakeS3Error("NotFound"), FakeS3Error("NoSuchKey"), FakeS3Error("404")]


def _matching_stem_head() -> dict[str, object]:
    """Build the exact post-upload MinIO headers for the deterministic fixture."""

    fixture = build_controlled_drum_fixture()
    return {
        "ContentLength": fixture.size_bytes,
        "ContentType": "audio/wav",
        "Metadata": dict(fixture.metadata),
    }


class FixedKeyADTOFWorkerSmokeMinIOInputAdapterTests(unittest.TestCase):
    """Prove a future smoke client cannot broaden its first S3 storage phase."""

    def test_clean_preflight_heads_only_the_three_reserved_keys(self) -> None:
        """Every fixed key must be absent; no list operation or overwrite occurs."""

        client = FakeS3Client()
        client.head_results = _missing_three()
        adapter = FixedKeyADTOFWorkerSmokeMinIOInputAdapter(client)

        adapter.assert_fixed_objects_absent()

        self.assertEqual(
            client.head_calls,
            [
                {"Bucket": "clouddsp-uploads", "Key": STEM_KEY},
                {"Bucket": "clouddsp-uploads", "Key": MIDI_KEY},
                {"Bucket": "clouddsp-uploads", "Key": TEMPO_KEY},
            ],
        )
        self.assertEqual(client.put_calls, [])

    def test_present_object_or_unknown_head_error_fails_without_an_upload(self) -> None:
        """Existing evidence and unavailable MinIO are never treated as a clean state."""

        present = FakeS3Client()
        present.head_results = [{"ContentLength": 1}]
        with self.assertRaises(ADTOFWorkerSmokeDirtyStateError):
            FixedKeyADTOFWorkerSmokeMinIOInputAdapter(present).assert_fixed_objects_absent()
        self.assertEqual(present.put_calls, [])

        unavailable = FakeS3Client()
        unavailable.head_results = [FakeS3Error("AccessDenied")]
        with self.assertRaises(ADTOFWorkerSmokeObjectStoreInfrastructureError):
            FixedKeyADTOFWorkerSmokeMinIOInputAdapter(unavailable).assert_fixed_objects_absent()
        self.assertEqual(unavailable.put_calls, [])

    def test_upload_writes_only_controlled_wav_then_verifies_exact_stored_evidence(self) -> None:
        """A future DB prepare call can rely on a current exact input object fact."""

        fixture = build_controlled_drum_fixture()
        client = FakeS3Client()
        client.head_results = [_matching_stem_head()]
        adapter = FixedKeyADTOFWorkerSmokeMinIOInputAdapter(client)

        evidence = adapter.upload_controlled_drum_wav(fixture)

        self.assertEqual(evidence.key, STEM_KEY)
        self.assertEqual(evidence.size_bytes, fixture.size_bytes)
        self.assertEqual(evidence.sha256, fixture.sha256)
        self.assertEqual(
            client.put_calls,
            [
                {
                    "Bucket": "clouddsp-uploads",
                    "Key": STEM_KEY,
                    "Body": fixture.wav_bytes,
                    "ContentLength": fixture.size_bytes,
                    "ContentType": "audio/wav",
                    "Metadata": fixture.metadata,
                }
            ],
        )
        self.assertEqual(client.head_calls, [{"Bucket": "clouddsp-uploads", "Key": STEM_KEY}])

    def test_upload_rejects_forged_fixture_or_stored_evidence_and_preserves_the_object(self) -> None:
        """No arbitrary bytes or weak HeadObject response can become durable work."""

        fixture = build_controlled_drum_fixture()
        forged = replace(fixture, metadata={"producer": "not-demucs"})
        forged_client = FakeS3Client()
        with self.assertRaises(ADTOFWorkerSmokeObjectStoreProtocolError):
            FixedKeyADTOFWorkerSmokeMinIOInputAdapter(forged_client).upload_controlled_drum_wav(forged)
        self.assertEqual(forged_client.put_calls, [])

        mismatched_stored = FakeS3Client()
        mismatched_stored.head_results = [
            {**_matching_stem_head(), "Metadata": {**fixture.metadata, "sha256": "b" * 64}}
        ]
        with self.assertRaises(ADTOFWorkerSmokeObjectStoreProtocolError):
            FixedKeyADTOFWorkerSmokeMinIOInputAdapter(mismatched_stored).upload_controlled_drum_wav(
                fixture
            )
        self.assertEqual(len(mismatched_stored.put_calls), 1)

    def test_adapter_cannot_be_redirected_to_generic_keys_or_clients(self) -> None:
        """The focused type exposes no bucket-list, get, delete, or arbitrary-key API."""

        adapter = FixedKeyADTOFWorkerSmokeMinIOInputAdapter(FakeS3Client())
        self.assertFalse(hasattr(adapter, "list_objects"))
        self.assertFalse(hasattr(adapter, "delete_object"))
        self.assertFalse(hasattr(adapter, "get_object"))
        with self.assertRaises(ADTOFWorkerSmokeContractError):
            adapter._head_exact_key("uploads/another-job/source.wav")


if __name__ == "__main__":
    unittest.main()
