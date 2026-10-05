"""Structural tests for the prepared, not-applied ADTOF Deployment manifest.

These tests inspect committed YAML text only. They do not contact a Kubernetes
API server, read a live Secret, pull an image, open RabbitMQ/PostgreSQL/MinIO,
or create a Pod.
"""

from __future__ import annotations

from pathlib import Path
import unittest


DEPLOYMENT_PATH = Path(__file__).resolve().parents[1] / "adtof-deployment.yaml"
IMMUTABLE_IMAGE = (
    "clouddsp-registry.localhost:5001/adtof@sha256:"
    "c8706bb0b814ed3a0c1e58455a7c153848ddf7808e65d4e3f4ed52a37b521b91"
)


class ADTOFDeploymentManifestTests(unittest.TestCase):
    """Keep the first worker controller private, bounded, and least-privilege."""

    def manifest(self) -> str:
        """Read source only; parsing/applying remains outside this test boundary."""

        return DEPLOYMENT_PATH.read_text(encoding="utf-8")

    def test_manifest_is_one_private_app_namespace_deployment(self) -> None:
        """A queue worker has no listener and therefore needs no Service/Ingress."""

        manifest = self.manifest()
        self.assertIn("apiVersion: apps/v1\nkind: Deployment", manifest)
        self.assertIn("name: clouddsp-adtof\n  namespace: clouddsp-app", manifest)
        self.assertNotIn("kind: Service", manifest)
        self.assertNotIn("kind: Ingress", manifest)
        # KEDA owns the Deployment's scale subresource. A source apply must
        # not restore a handwritten replica count over that controller.
        self.assertNotIn("\n  replicas:", manifest)
        self.assertIn("automountServiceAccountToken: false", manifest)
        self.assertIn("enableServiceLinks: false", manifest)

    def test_manifest_uses_only_the_locked_arm64_cpu_runtime(self) -> None:
        """A readable tag, CUDA request, or wrong architecture must not slip in."""

        manifest = self.manifest()
        self.assertIn(f"image: {IMMUTABLE_IMAGE}", manifest)
        self.assertIn("imagePullPolicy: IfNotPresent", manifest)
        self.assertIn("kubernetes.io/arch: arm64", manifest)
        self.assertIn("runAsUser: 10005", manifest)
        self.assertIn("runAsGroup: 10005", manifest)
        self.assertIn("readOnlyRootFilesystem: true", manifest)
        self.assertNotIn("nvidia.com/gpu", manifest)

    def test_manifest_mounts_only_bounded_disposable_worker_writes(self) -> None:
        """Temporary audio/cache space must not become image-root or durable state."""

        manifest = self.manifest()
        for required_field in (
            "terminationGracePeriodSeconds: 660",
            "mountPath: /worker-scratch",
            "mountPath: /tmp",
            "mountPath: /home/clouddsp-adtof",
            "sizeLimit: 512Mi",
            "sizeLimit: 128Mi",
            "sizeLimit: 64Mi",
            "ephemeral-storage: 1Gi",
        ):
            self.assertIn(required_field, manifest)

    def test_manifest_uses_exact_private_services_and_restricted_secret_names(self) -> None:
        """The worker cannot receive public endpoints or administrator credentials."""

        manifest = self.manifest()
        for required_field in (
            "value: clouddsp-postgresql.clouddsp-data.svc",
            "value: http://clouddsp-minio.clouddsp-data.svc:9000",
            "value: clouddsp-rabbitmq.clouddsp-data.svc",
            "value: clouddsp.adtof.requests",
            "name: clouddsp-adtof-database-credentials",
            "name: clouddsp-adtof-minio-credentials",
            "name: clouddsp-adtof-rabbitmq-credentials",
            "value: \"1\"",
        ):
            self.assertIn(required_field, manifest)
        self.assertNotIn("bootstrap-credentials", manifest)
        self.assertNotIn("clouddsp-admin", manifest)


if __name__ == "__main__":
    unittest.main()
