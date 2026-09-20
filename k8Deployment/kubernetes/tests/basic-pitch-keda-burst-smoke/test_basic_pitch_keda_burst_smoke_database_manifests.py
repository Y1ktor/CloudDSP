"""Source-only checks for the Basic Pitch KEDA burst PostgreSQL boundary.

These assertions inspect reviewed manifest text. They do not read a local
Secret, connect to PostgreSQL, provision a role, create a durable Job/event, or
start a Kubernetes Pod. A later explicit bootstrap run is the integration
verification of the same boundary.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_DIRECTORY = Path(__file__).resolve().parent
_RUNTIME_SECRET = _DIRECTORY / "basic-pitch-keda-burst-smoke-database-credentials.secret.example.yaml"
_BOOTSTRAP_SECRET = _DIRECTORY / "basic-pitch-keda-burst-smoke-database-bootstrap-credentials.secret.example.yaml"
_BOOTSTRAP_JOB = _DIRECTORY / "basic-pitch-keda-burst-smoke-database-bootstrap-job.yaml"

_COORDINATES = (
    ("vocals", "2a1e8097-7da6-4d06-8b27-c006b02e0d91", "32e10a72-b5e7-4f11-8dfe-b922ec7ebbd9"),
    ("bass", "8e27f4ad-6c19-4e3f-9b56-fa287429c0a2", "c4b23de8-4a63-47e3-a96e-155abfd38c36"),
    ("other", "96ba2e39-e035-4fe8-a50a-b1e6db208ac3", "47ae0c63-e1b1-4663-b47a-8efaa8f37bc7"),
)


class BasicPitchKedaBurstSmokeDatabaseManifestTests(unittest.TestCase):
    """Keep the test client confined to three fixed durable coordinates."""

    def test_runtime_and_bootstrap_secrets_are_namespaced_copies_of_one_role(self) -> None:
        """Runtime access stays in app; admin-assisted provisioning stays in data."""

        runtime = _RUNTIME_SECRET.read_text(encoding="utf-8")
        bootstrap = _BOOTSTRAP_SECRET.read_text(encoding="utf-8")

        self.assertIn("namespace: clouddsp-app", runtime)
        self.assertIn("namespace: clouddsp-data", bootstrap)
        for manifest in (runtime, bootstrap):
            self.assertIn("BASIC_PITCH_KEDA_BURST_SMOKE_DB_NAME", manifest)
            self.assertIn("BASIC_PITCH_KEDA_BURST_SMOKE_DB_USERNAME", manifest)
            self.assertIn("BASIC_PITCH_KEDA_BURST_SMOKE_DB_PASSWORD", manifest)
            self.assertIn("clouddsp-basic-pitch-keda-burst-smoke", manifest)
            self.assertNotIn("POSTGRES_PASSWORD", manifest)

    def test_bootstrap_grants_only_fixed_security_definer_functions(self) -> None:
        """The client may execute an API-shaped database boundary, not table DML."""

        job = _BOOTSTRAP_JOB.read_text(encoding="utf-8")

        self.assertIn("name: basic-pitch-keda-burst-smoke-database-bootstrap", job)
        self.assertIn("namespace: clouddsp-data", job)
        self.assertIn("automountServiceAccountToken: false", job)
        self.assertIn("clouddsp-postgresql-credentials", job)
        self.assertIn("clouddsp-basic-pitch-keda-burst-smoke-database-bootstrap-credentials", job)
        self.assertIn("migration_id = 'v004_downstream_outbox_events'", job)
        self.assertIn("migration_id = 'v005_basic_pitch_processing_tasks'", job)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public", job)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public", job)
        self.assertIn("REVOKE ALL ON FUNCTION public.clouddsp_basic_pitch_keda_burst_smoke_prepare", job)
        self.assertIn("FROM PUBLIC", job)
        for function in ("prepare", "observe", "cleanup"):
            self.assertIn(
                f"clouddsp_basic_pitch_keda_burst_smoke_{function}",
                job,
            )
        self.assertGreaterEqual(job.count("SECURITY DEFINER"), 3)
        self.assertNotIn("GRANT SELECT ON ALL TABLES", job)
        self.assertNotIn("GRANT INSERT ON ALL TABLES", job)
        self.assertNotIn("GRANT DELETE ON ALL TABLES", job)

    def test_three_coordinates_are_literal_and_cleanup_requires_normal_success(self) -> None:
        """A failure must retain evidence rather than permit partial reuse or deletion."""

        job = _BOOTSTRAP_JOB.read_text(encoding="utf-8")

        self.assertIn("pg_advisory_xact_lock(842930519)", job)
        self.assertIn("stem_mode, status, stems, expires_at", job)
        self.assertIn("'4-stems', 'midi_processing'", job)
        for label, job_id, event_id in _COORDINATES:
            self.assertIn(label, job)
            self.assertIn(job_id, job)
            self.assertIn(event_id, job)
            self.assertIn(f"stems/{job_id}/{label}.wav", job)
        self.assertIn("event.publication_status = 'published'", job)
        self.assertIn("task.status = 'succeeded'", job)
        self.assertIn("task.attempt_count = 1", job)
        self.assertIn("RETURN v_deleted_count = 3", job)
        self.assertIn("p_vocals_sha256 IS NULL", job)
        self.assertIn("p_bass_sha256 IS NULL", job)
        self.assertIn("p_other_sha256 IS NULL", job)


if __name__ == "__main__":
    unittest.main()
