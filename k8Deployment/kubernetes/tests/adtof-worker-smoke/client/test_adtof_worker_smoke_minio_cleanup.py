"""Unit tests for successful-only fixed-key ADTOF smoke object cleanup.

The injected client is in-memory. Tests make no S3, database, broker, model,
or Kubernetes call and never delete a real object.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from uuid import uuid4

from adtof_worker_smoke_contract import (
    ADTOFWorkerSmokeObservation,
    FixedObjectEvidence,
    VerifiedADTOFWorkerSmokeOutputs,
)
from adtof_worker_smoke_fixture import MIDI_KEY, STEM_KEY, TEMPO_KEY
from adtof_worker_smoke_minio_cleanup import (
    ADTOFWorkerSmokeCleanupEvidenceError,
    ADTOFWorkerSmokeCleanupInfrastructureError,
    SuccessfulOnlyADTOFWorkerSmokeMinIOCleanupAdapter,
)


class FakeS3Client:
    """Record one configured DeleteObject outcome at a time."""

    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    def delete_object(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _successful_observation(**overrides: object) -> ADTOFWorkerSmokeObservation:
    """Return the exact completed first-attempt fact that authorizes cleanup."""

    now = datetime(2026, 9, 14, tzinfo=UTC)
    values: dict[str, object] = {
        "publication_status": "published",
        "published_at": now,
        "task_id": str(uuid4()),
        "task_status": "succeeded",
        "task_attempt_count": 1,
        "task_lease_is_clear": True,
        "task_completed_at": now,
        "job_status": "failed_incomplete_stem_fixture",
    }
    values.update(overrides)
    return ADTOFWorkerSmokeObservation(**values)  # type: ignore[arg-type]


def _input_evidence() -> FixedObjectEvidence:
    """Return bounded fixed WAV proof emitted by the earlier input adapter."""

    return FixedObjectEvidence(
        key=STEM_KEY,
        content_type="audio/wav",
        size_bytes=44,
        sha256="a" * 64,
    )


def _outputs() -> VerifiedADTOFWorkerSmokeOutputs:
    """Return bounded fixed result proofs emitted by the output reader."""

    return VerifiedADTOFWorkerSmokeOutputs(
        midi=FixedObjectEvidence(
            key=MIDI_KEY,
            content_type="audio/midi",
            size_bytes=26,
            sha256="b" * 64,
        ),
        tempo=FixedObjectEvidence(
            key=TEMPO_KEY,
            content_type="application/json",
            size_bytes=200,
            sha256="c" * 64,
        ),
    )


class SuccessfulOnlyADTOFWorkerSmokeMinIOCleanupAdapterTests(unittest.TestCase):
    """Keep the only destructive smoke-client capability narrow and proof-gated."""

    def test_deletes_exactly_the_verified_fixed_keys_in_documented_order(self) -> None:
        """Cleanup is available only after durable and storage success evidence exists."""

        client = FakeS3Client([{}, {}, {}])
        SuccessfulOnlyADTOFWorkerSmokeMinIOCleanupAdapter(client).delete_fixed_objects_after_success(
            observation=_successful_observation(),
            input_stem=_input_evidence(),
            outputs=_outputs(),
        )

        self.assertEqual(
            client.calls,
            [
                {"Bucket": "clouddsp-uploads", "Key": TEMPO_KEY},
                {"Bucket": "clouddsp-uploads", "Key": MIDI_KEY},
                {"Bucket": "clouddsp-uploads", "Key": STEM_KEY},
            ],
        )

    def test_invalid_or_incomplete_success_evidence_cannot_start_any_deletion(self) -> None:
        """A failed/retried task and malformed evidence preserve every fixed object."""

        failed_client = FakeS3Client([])
        with self.assertRaises(ADTOFWorkerSmokeCleanupEvidenceError):
            SuccessfulOnlyADTOFWorkerSmokeMinIOCleanupAdapter(
                failed_client
            ).delete_fixed_objects_after_success(
                observation=_successful_observation(task_attempt_count=2),
                input_stem=_input_evidence(),
                outputs=_outputs(),
            )
        self.assertEqual(failed_client.calls, [])

        malformed_client = FakeS3Client([])
        with self.assertRaises(ADTOFWorkerSmokeCleanupEvidenceError):
            SuccessfulOnlyADTOFWorkerSmokeMinIOCleanupAdapter(
                malformed_client
            ).delete_fixed_objects_after_success(
                observation=_successful_observation(),
                input_stem=_input_evidence(),
                outputs=VerifiedADTOFWorkerSmokeOutputs(
                    midi=FixedObjectEvidence(
                        key=MIDI_KEY,
                        content_type="audio/midi",
                        size_bytes=17 * 1024 * 1024,
                        sha256="b" * 64,
                    ),
                    tempo=_outputs().tempo,
                ),
            )
        self.assertEqual(malformed_client.calls, [])

    def test_failure_stops_in_order_and_redacts_the_underlying_s3_error(self) -> None:
        """No retry or speculative later deletion hides a partial cleanup failure."""

        client = FakeS3Client([{}, RuntimeError("private MinIO endpoint"), {}])
        with self.assertRaises(ADTOFWorkerSmokeCleanupInfrastructureError) as raised:
            SuccessfulOnlyADTOFWorkerSmokeMinIOCleanupAdapter(client).delete_fixed_objects_after_success(
                observation=_successful_observation(),
                input_stem=_input_evidence(),
                outputs=_outputs(),
            )
        self.assertEqual(str(raised.exception), "ADTOF smoke object cleanup is unavailable.")
        self.assertEqual(
            client.calls,
            [
                {"Bucket": "clouddsp-uploads", "Key": TEMPO_KEY},
                {"Bucket": "clouddsp-uploads", "Key": MIDI_KEY},
            ],
        )

    def test_adapter_exposes_no_generic_read_write_or_listing_api(self) -> None:
        """The cleanup adapter cannot be repurposed as a general MinIO client."""

        adapter = SuccessfulOnlyADTOFWorkerSmokeMinIOCleanupAdapter(FakeS3Client([]))
        for forbidden_method in ("put_object", "get_object", "head_object", "list_objects", "delete_object"):
            self.assertFalse(hasattr(adapter, forbidden_method))

        source = (Path(__file__).parent / "adtof_worker_smoke_minio_cleanup.py").read_text(
            encoding="utf-8"
        )
        for forbidden_import in ("import boto3", "import psycopg", "import pika", "import requests"):
            self.assertNotIn(forbidden_import, source)


if __name__ == "__main__":
    unittest.main()
