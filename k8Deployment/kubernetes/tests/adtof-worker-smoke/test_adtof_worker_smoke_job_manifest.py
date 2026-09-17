"""Structural guardrails for the unapplied ADTOF end-to-end smoke Job.

These tests read manifest source only. They never call Docker, Kubernetes,
PostgreSQL, MinIO, RabbitMQ, or the ADTOF model, and they create no smoke
object, durable event, task, or Job Pod.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_MANIFEST = Path(__file__).resolve().parent / "adtof-worker-smoke-job.yaml"


class ADTOFWorkerSmokeJobManifestTests(unittest.TestCase):
    """Keep the finite verifier independent from worker and administrator authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load only version-controlled YAML text; no cluster parser/API is involved."""

        cls.source = _MANIFEST.read_text(encoding="utf-8")

    def test_uses_the_published_arm64_smoke_image_as_a_one_shot_job(self) -> None:
        """A mutable tag, controller, or cross-architecture image could invalidate test evidence."""

        self.assertIn("apiVersion: batch/v1\nkind: Job", self.source)
        self.assertIn("name: adtof-worker-smoke", self.source)
        self.assertIn("namespace: clouddsp-app", self.source)
        self.assertIn("restartPolicy: Never", self.source)
        self.assertIn("backoffLimit: 0", self.source)
        self.assertIn("ttlSecondsAfterFinished: 600", self.source)
        self.assertIn("activeDeadlineSeconds: 780", self.source)
        self.assertIn("kubernetes.io/arch: arm64", self.source)
        self.assertIn(
            "clouddsp-registry.localhost:5001/adtof-worker-smoke-client@"
            "sha256:f6908bfabe930a1066fed18446aa6006d58dbea43764d75265557bdd6771c74f",
            self.source,
        )
        self.assertNotIn("image: clouddsp-registry.localhost:5001/adtof-worker-smoke-client:", self.source)
        self.assertNotIn("kind: Deployment", self.source)

    def test_mounts_only_the_two_restricted_app_namespace_identities(self) -> None:
        """The client must not inherit administrator, worker, broker, or browser credentials."""

        for name in (
            "clouddsp-adtof-worker-smoke-database-credentials",
            "clouddsp-adtof-worker-smoke-minio-credentials",
        ):
            self.assertIn(name, self.source)
        for forbidden in (
            "clouddsp-postgresql-credentials",
            "clouddsp-minio-root-credentials",
            "RABBITMQ_",
            "KEYCLOAK_",
            "ADTOF_DB_PASSWORD",
            "ADTOF_S3_SECRET_KEY",
        ):
            self.assertNotIn(forbidden, self.source)
        self.assertIn("AWS_EC2_METADATA_DISABLED", self.source)
        self.assertIn('value: "true"', self.source)

    def test_uses_only_fixed_private_routes_and_the_bounded_observation_window(self) -> None:
        """The workload has no browser, port-forward, public-cloud, or arbitrary-route setting."""

        for required in (
            "clouddsp-postgresql.clouddsp-data.svc",
            "http://clouddsp-minio.clouddsp-data.svc:9000",
            "ADTOF_WORKER_SMOKE_MINIO_REGION",
            "value: us-east-1",
            "ADTOF_WORKER_SMOKE_TIMEOUT_SECONDS",
            'value: "720"',
        ):
            self.assertIn(required, self.source)
        # The immutable local-registry image reference legitimately contains
        # `.localhost`; reject only an unsafe environment route rather than the
        # explanatory/provenance text surrounding it.
        for forbidden in (
            "value: http://localhost",
            "value: http://clouddsp.localhost",
            "value: https://",
            "amazonaws.com",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_enforces_no_kubernetes_api_token_no_root_and_no_writable_filesystem(self) -> None:
        """A compromised client dependency must not gain cluster authority or mutable image state."""

        for required in (
            "automountServiceAccountToken: false",
            "enableServiceLinks: false",
            "runAsNonRoot: true",
            "runAsUser: 10006",
            "runAsGroup: 10006",
            "type: RuntimeDefault",
            "allowPrivilegeEscalation: false",
            "readOnlyRootFilesystem: true",
            "- ALL",
        ):
            self.assertIn(required, self.source)
        self.assertNotIn("serviceAccountName:", self.source)
        self.assertNotIn("hostNetwork:", self.source)
        self.assertNotIn("privileged: true", self.source)
        self.assertNotIn("hostPath:", self.source)
        self.assertNotIn("volumes:", self.source)


if __name__ == "__main__":
    unittest.main()
