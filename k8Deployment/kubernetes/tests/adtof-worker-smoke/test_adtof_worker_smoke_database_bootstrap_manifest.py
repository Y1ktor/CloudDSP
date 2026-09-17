"""Structural guardrails for the unapplied ADTOF worker-smoke DB boundary.

These tests read manifest source only. They never connect to PostgreSQL,
MinIO, RabbitMQ, Kubernetes, or an ADTOF model, and they create no durable
smoke Job/event/object. Their purpose is to make later manifest edits preserve
the narrow fixed-coordinate capability required by the eventual end-to-end
worker smoke test.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_DIRECTORY = Path(__file__).resolve().parent
_MANIFEST = _DIRECTORY / "adtof-worker-smoke-database-bootstrap-job.yaml"
_BOOTSTRAP_TEMPLATE = _DIRECTORY / "adtof-worker-smoke-database-bootstrap-credentials.secret.example.yaml"
_RUNTIME_TEMPLATE = _DIRECTORY / "adtof-worker-smoke-database-credentials.secret.example.yaml"


class ADTOFWorkerSmokeDatabaseBootstrapManifestTests(unittest.TestCase):
    """Keep the future test client constrained to its three database functions."""

    def test_secret_templates_keep_real_passwords_out_of_version_control(self) -> None:
        """Both namespace-scoped copies must name only the dedicated smoke role."""

        bootstrap = _BOOTSTRAP_TEMPLATE.read_text(encoding="utf-8")
        runtime = _RUNTIME_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("namespace: clouddsp-data", bootstrap)
        self.assertIn("namespace: clouddsp-app", runtime)
        for source in (bootstrap, runtime):
            self.assertIn("ADTOF_WORKER_SMOKE_DB_NAME: clouddsp_job_api", source)
            self.assertIn(
                "ADTOF_WORKER_SMOKE_DB_USERNAME: clouddsp-adtof-worker-smoke",
                source,
            )
            self.assertIn("REPLACE_WITH_A_LOCAL_TEST_PASSWORD", source)
            self.assertNotIn("clouddsp-admin", source)

    def test_bootstrap_is_a_private_one_shot_postgresql_only_job(self) -> None:
        """Administrator credentials must not grow into cluster or pipeline access."""

        source = _MANIFEST.read_text(encoding="utf-8")
        self.assertIn("apiVersion: batch/v1\nkind: Job", source)
        self.assertIn("name: adtof-worker-smoke-database-bootstrap", source)
        self.assertIn("namespace: clouddsp-data", source)
        self.assertIn("restartPolicy: Never", source)
        self.assertIn("backoffLimit: 0", source)
        self.assertIn("automountServiceAccountToken: false", source)
        self.assertIn(
            "docker.io/library/postgres@sha256:051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0",
            source,
        )
        self.assertIn("clouddsp-postgresql.clouddsp-data.svc", source)
        self.assertIn("clouddsp-postgresql-credentials", source)
        self.assertIn("clouddsp-adtof-worker-smoke-database-bootstrap-credentials", source)
        self.assertNotIn("clouddsp-adtof-worker-smoke-database-credentials", source)
        self.assertNotIn("MINIO_", source)
        self.assertNotIn("RABBITMQ_", source)
        self.assertNotIn("kind: Deployment", source)
        # The standard managed-by label includes the literal word `kubectl`,
        # but the container script itself must not gain a Kubernetes client.
        self.assertNotIn("\n              kubectl", source)

    def test_fixed_security_definer_functions_cannot_become_arbitrary_table_access(self) -> None:
        """Only fixed ADTOF coordinates may be prepared, observed, or cleaned."""

        source = _MANIFEST.read_text(encoding="utf-8")
        for migration in (
            "v004_downstream_outbox_events",
            "v006_adtof_processing_tasks",
        ):
            self.assertIn(migration, source)
        for identifier in (
            "8d94ca4d-8f27-4d78-a01f-73ee0fb8c6bc",
            "0b37b59f-3634-47de-97f0-5a1f3e27ea2f",
        ):
            self.assertIn(identifier, source)
        self.assertNotIn("c0a24de2-01d0-4d51-a24d-87f76c30f47c", source)
        for function in (
            "clouddsp_adtof_worker_smoke_prepare",
            "clouddsp_adtof_worker_smoke_observe",
            "clouddsp_adtof_worker_smoke_cleanup",
        ):
            self.assertIn(f"CREATE OR REPLACE FUNCTION public.{function}", source)
            self.assertIn(f"REVOKE ALL ON FUNCTION public.{function}", source)
            self.assertIn(f"GRANT EXECUTE ON FUNCTION public.{function}", source)
        self.assertGreaterEqual(source.count("SECURITY DEFINER"), 3)
        self.assertIn("'stems/8d94ca4d-8f27-4d78-a01f-73ee0fb8c6bc/drums.wav'", source)
        self.assertIn("'midi_processing'", source)
        self.assertIn("'adtof.requested'", source)
        self.assertIn("'4-stems'", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public", source)
        self.assertNotIn("GRANT SELECT ON TABLE public.jobs", source)
        self.assertNotIn("GRANT INSERT ON TABLE public.jobs", source)
        self.assertNotIn("GRANT UPDATE ON TABLE public.jobs", source)
        self.assertNotIn("GRANT DELETE ON TABLE public.jobs", source)
        self.assertNotIn("DO $$", source)
        self.assertIn("DO $adtof_smoke_bootstrap$", source)

    def test_cleanup_is_success_only_and_preserves_failed_evidence(self) -> None:
        """A failed worker run must stay inspectable instead of being erased."""

        source = _MANIFEST.read_text(encoding="utf-8")
        cleanup_start = source.index("CREATE OR REPLACE FUNCTION public.clouddsp_adtof_worker_smoke_cleanup")
        cleanup_end = source.index("$adtof_smoke_cleanup$;", cleanup_start)
        cleanup = source[cleanup_start:cleanup_end]
        for required_fact in (
            "job.status = 'midi_processing'",
            "event.publication_status = 'published'",
            "task.status = 'succeeded'",
            "task.attempt_count = 1",
            "task.lease_token IS NULL",
            "task.lease_expires_at IS NULL",
            "task.completed_at IS NOT NULL",
        ):
            self.assertIn(required_fact, cleanup)
        self.assertNotIn("status = 'failed'", cleanup)


if __name__ == "__main__":
    unittest.main()
