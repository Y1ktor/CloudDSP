"""Structural tests for the focused ADTOF exhausted-lease live smoke Job.

These tests inspect source only. They do not use Kubernetes, PostgreSQL,
RabbitMQ, MinIO, Docker, a mounted Secret, or the deployed ADTOF worker.
"""

from __future__ import annotations

from pathlib import Path
import unittest


MANIFEST_PATH = Path(__file__).with_name("adtof-exhausted-lease-recovery-smoke-job.yaml")


class ADTOFExhaustedLeaseRecoverySmokeManifestTests(unittest.TestCase):
    """Keep the live test isolated to one fixed terminal-recovery coordinate."""

    def manifest(self) -> str:
        """Read declarative source; applying it remains a separate user action."""

        return MANIFEST_PATH.read_text(encoding="utf-8")

    def test_manifest_is_one_bounded_private_postgresql_job(self) -> None:
        """The verifier must not acquire browser, broker, storage, or API power."""

        manifest = self.manifest()
        self.assertIn("apiVersion: batch/v1\nkind: Job", manifest)
        self.assertIn("name: clouddsp-adtof-exhausted-lease-recovery-smoke", manifest)
        self.assertIn("namespace: clouddsp-data", manifest)
        self.assertIn("ttlSecondsAfterFinished: 600", manifest)
        self.assertIn("backoffLimit: 0", manifest)
        self.assertIn("activeDeadlineSeconds: 150", manifest)
        self.assertIn("automountServiceAccountToken: false", manifest)
        self.assertIn("enableServiceLinks: false", manifest)
        self.assertIn("value: clouddsp-postgresql", manifest)
        self.assertIn("value: clouddsp_job_api", manifest)
        self.assertNotIn("kind: Service", manifest)
        self.assertNotIn("kind: Ingress", manifest)
        self.assertNotIn("boto3", manifest)
        # Labels and explanatory comments may name Kubernetes/RabbitMQ, but
        # this Job must not receive executable client configuration for either.
        self.assertNotIn("RABBITMQ_", manifest)
        self.assertNotIn("AMQP_", manifest)
        self.assertNotIn("MINIO_", manifest)
        self.assertNotIn("\n              kubectl", manifest)

    def test_seed_is_exactly_one_expired_active_third_attempt(self) -> None:
        """A regression cannot turn this into a generic task writer or retry test."""

        manifest = self.manifest()
        for required_fragment in (
            "'af1a785b-2e71-476a-b483-405c10606a23'",
            "'b627b72e-e1b3-413a-a51d-80d019d5bbf0'",
            "'cc3acb39-04ac-4ab8-afc5-a66d50cb8b66'",
            "'e76dc37e-ef3b-4fa9-b6c5-2e0fc4e9ca79'",
            "'adtof'",
            "'drums'",
            "'leased'",
            "attempt_count, available_at, lease_token, lease_expires_at",
            "CURRENT_TIMESTAMP - INTERVAL '1 minute'",
            "'published'",
            "publication_status, published_at",
        ):
            self.assertIn(required_fragment, manifest)

    def test_verification_and_cleanup_require_the_terminal_task_fact(self) -> None:
        """Success must prove no fourth lease, no output, and safe scoped cleanup."""

        manifest = self.manifest()
        for required_fragment in (
            "task.status = 'failed'",
            "task.attempt_count = 3",
            "task.lease_token IS NULL",
            "task.lease_expires_at IS NULL",
            "task.completed_at IS NOT NULL",
            "task.last_error_code = 'lease_expired_attempts_exhausted'",
            "job.status = 'midi_processing'",
            "job.midi = '{}'::jsonb",
            "job.tempo IS NULL",
            "ADTOF exhausted-lease smoke cleanup refused.",
            "ADTOF exhausted-lease smoke passed and removed its fixed test rows",
        ):
            self.assertIn(required_fragment, manifest)


if __name__ == "__main__":
    unittest.main()
