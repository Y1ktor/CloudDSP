"""Unit tests for the dependency-free ADTOF worker-smoke client contract.

No test opens a socket, reads Kubernetes, imports Boto3/Psycopg/Pika, invokes
ADTOF, or creates a media/object/database/broker artifact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from uuid import uuid4

from adtof_worker_smoke_contract import (
    ADTOFWorkerSmokeConfigurationError,
    ADTOFWorkerSmokeContractError,
    ADTOFWorkerSmokeObservation,
    ADTOFWorkerSmokeSettings,
    FixedObjectEvidence,
    PreparedADTOFWorkerSmoke,
    VerifiedADTOFWorkerSmokeOutputs,
    fixed_storage_bucket,
)
from adtof_worker_smoke_fixture import MIDI_KEY, SMOKE_EVENT_ID, SMOKE_JOB_ID, STEM_KEY, TEMPO_KEY


def _environment(**overrides: str) -> dict[str, str]:
    """Return valid non-secret-shaped inputs for pure settings tests."""

    values = {
        "ADTOF_WORKER_SMOKE_DB_NAME": "clouddsp_job_api",
        "ADTOF_WORKER_SMOKE_DB_USERNAME": "clouddsp-adtof-worker-smoke",
        "ADTOF_WORKER_SMOKE_DB_PASSWORD": "test-password",
        "ADTOF_WORKER_SMOKE_S3_ACCESS_KEY": "clouddsp-adtof-worker-smoke",
        "ADTOF_WORKER_SMOKE_S3_SECRET_KEY": "test-secret-key",
    }
    values.update(overrides)
    return values


class ADTOFWorkerSmokeContractTests(unittest.TestCase):
    """Keep settings and future adapter boundaries fixed before I/O exists."""

    def test_settings_use_only_fixed_private_routes_and_restricted_identities(self) -> None:
        """Secrets may vary, but endpoint/bucket/identity routing cannot broaden."""

        settings = ADTOFWorkerSmokeSettings.from_environment(_environment())
        self.assertEqual(settings.database_host, "clouddsp-postgresql.clouddsp-data.svc")
        self.assertEqual(settings.database_port, 5432)
        self.assertEqual(settings.minio_endpoint_url, "http://clouddsp-minio.clouddsp-data.svc:9000")
        self.assertEqual(settings.minio_region, "us-east-1")
        self.assertEqual(settings.database_name, "clouddsp_job_api")
        self.assertEqual(settings.database_username, "clouddsp-adtof-worker-smoke")
        self.assertEqual(settings.minio_access_key, "clouddsp-adtof-worker-smoke")
        self.assertEqual(fixed_storage_bucket(), "clouddsp-uploads")
        self.assertNotIn("test-password", repr(settings))
        self.assertNotIn("test-secret-key", repr(settings))

    def test_contract_stays_dependency_free_and_exposes_no_generic_io_clients(self) -> None:
        """Later focused adapters—not this layer—may introduce network libraries."""

        source = (Path(__file__).parent / "adtof_worker_smoke_contract.py").read_text(
            encoding="utf-8"
        )
        for forbidden_import in ("import boto3", "import psycopg", "import pika", "import requests"):
            self.assertNotIn(forbidden_import, source)
        for forbidden_capability in (
            "def execute(",
            "def put_object(",
            "def get_object(",
            "def delete_object(",
            "def publish(",
        ):
            self.assertNotIn(forbidden_capability, source)

    def test_settings_reject_redirects_identity_substitution_and_unbounded_waits(self) -> None:
        """A later Job cannot repurpose this client through environment variables."""

        for override in (
            {"ADTOF_WORKER_SMOKE_DB_HOST": "localhost"},
            {"ADTOF_WORKER_SMOKE_DB_PORT": "5433"},
            {"ADTOF_WORKER_SMOKE_MINIO_ENDPOINT_URL": "https://s3.amazonaws.com"},
            {"ADTOF_WORKER_SMOKE_DB_USERNAME": "clouddsp-job-api"},
            {"ADTOF_WORKER_SMOKE_S3_ACCESS_KEY": "another-user"},
            {"ADTOF_WORKER_SMOKE_TIMEOUT_SECONDS": "901"},
            {"ADTOF_WORKER_SMOKE_TIMEOUT_SECONDS": "not-a-number"},
        ):
            with self.subTest(override=override):
                with self.assertRaises(ADTOFWorkerSmokeConfigurationError):
                    ADTOFWorkerSmokeSettings.from_environment(_environment(**override))

    def test_prepared_event_rejects_any_coordinate_except_the_reserved_pair(self) -> None:
        """A future database adapter cannot point the client at a different Job/event."""

        self.assertEqual(
            PreparedADTOFWorkerSmoke(job_id=SMOKE_JOB_ID, event_id=SMOKE_EVENT_ID).job_id,
            SMOKE_JOB_ID,
        )
        with self.assertRaises(ADTOFWorkerSmokeContractError):
            PreparedADTOFWorkerSmoke(job_id=str(uuid4()), event_id=SMOKE_EVENT_ID)
        with self.assertRaises(ADTOFWorkerSmokeContractError):
            PreparedADTOFWorkerSmoke(job_id=SMOKE_JOB_ID.upper(), event_id=SMOKE_EVENT_ID)

    def test_only_exact_published_first_attempt_completion_is_successful(self) -> None:
        """Cleanup must wait for publication, one completed task, and a cleared lease."""

        completed_at = datetime(2026, 9, 14, tzinfo=UTC)
        success = ADTOFWorkerSmokeObservation(
            publication_status="published",
            published_at=completed_at,
            task_id=str(uuid4()),
            task_status="succeeded",
            task_attempt_count=1,
            task_lease_is_clear=True,
            task_completed_at=completed_at,
            job_status="failed_incomplete_stem_fixture",
        )
        self.assertTrue(success.is_successful_first_attempt)
        self.assertFalse(
            ADTOFWorkerSmokeObservation(
                publication_status="published",
                published_at=completed_at,
                task_id=success.task_id,
                task_status="succeeded",
                task_attempt_count=1,
                task_lease_is_clear=True,
                task_completed_at=completed_at,
                job_status="failed",
            ).is_successful_first_attempt
        )
        self.assertFalse(
            ADTOFWorkerSmokeObservation(
                publication_status="published",
                published_at=completed_at,
                task_id=str(uuid4()),
                task_status="succeeded",
                task_attempt_count=2,
                task_lease_is_clear=True,
                task_completed_at=completed_at,
                job_status="failed_incomplete_stem_fixture",
            ).is_successful_first_attempt
        )
        self.assertFalse(
            ADTOFWorkerSmokeObservation(
                publication_status="leased",
                published_at=None,
                task_id=None,
                task_status=None,
                task_attempt_count=None,
                task_lease_is_clear=False,
                task_completed_at=None,
                job_status="midi_processing",
            ).is_successful_first_attempt
        )

    def test_fixed_object_evidence_cannot_name_another_key_or_incomplete_output_pair(self) -> None:
        """The later S3 adapter must report exactly the policy-granted object set."""

        digest = "a" * 64
        input_evidence = FixedObjectEvidence(
            key=STEM_KEY,
            content_type="audio/wav",
            size_bytes=1,
            sha256=digest,
        )
        midi = FixedObjectEvidence(
            key=MIDI_KEY,
            content_type="audio/midi",
            size_bytes=1,
            sha256=digest,
        )
        tempo = FixedObjectEvidence(
            key=TEMPO_KEY,
            content_type="application/json",
            size_bytes=1,
            sha256=digest,
        )
        self.assertEqual(input_evidence.key, STEM_KEY)
        self.assertEqual(VerifiedADTOFWorkerSmokeOutputs(midi=midi, tempo=tempo).midi, midi)
        with self.assertRaises(ADTOFWorkerSmokeContractError):
            FixedObjectEvidence(
                key="midi/another-job/drums.mid",
                content_type="audio/midi",
                size_bytes=1,
                sha256=digest,
            )
        with self.assertRaises(ADTOFWorkerSmokeContractError):
            FixedObjectEvidence(
                key=MIDI_KEY,
                content_type="text/plain",
                size_bytes=1,
                sha256=digest,
            )
        with self.assertRaises(ADTOFWorkerSmokeContractError):
            VerifiedADTOFWorkerSmokeOutputs(midi=tempo, tempo=midi)


if __name__ == "__main__":
    unittest.main()
