"""Structural tests for the applied Demucs Deployment source manifest.

These tests inspect committed YAML text only. They do not contact a Kubernetes
API server, read a live Secret, pull an image, open RabbitMQ/PostgreSQL/MinIO,
or create a Pod.
"""

from __future__ import annotations

from pathlib import Path
import unittest


DEPLOYMENT_PATH = Path(__file__).resolve().parents[1] / "demucs-deployment.yaml"
IMMUTABLE_IMAGE = (
    "clouddsp-registry.localhost:5001/demucs@sha256:"
    "e6cab988fa3786d66dcfd7f608dfa6479582a4acd3bfb9b47f948d637cf6fd59"
)


class DemucsDeploymentManifestTests(unittest.TestCase):
    """Keep the first worker controller private, bounded, and least-privilege."""

    def manifest(self) -> str:
        """Read source only; parsing/applying remains outside this test boundary."""

        return DEPLOYMENT_PATH.read_text(encoding="utf-8")

    def test_manifest_is_one_private_app_namespace_deployment(self) -> None:
        """A queue worker has no listener and therefore needs no Service/Ingress."""

        manifest = self.manifest()
        self.assertIn("apiVersion: apps/v1\nkind: Deployment", manifest)
        self.assertIn("name: clouddsp-demucs\n  namespace: clouddsp-app", manifest)
        self.assertNotIn("\n  replicas:", manifest)
        self.assertNotIn("kind: Service", manifest)
        self.assertNotIn("kind: Ingress", manifest)
        self.assertIn("automountServiceAccountToken: false", manifest)
        self.assertIn("enableServiceLinks: false", manifest)

    def test_manifest_uses_only_the_locked_arm64_cpu_runtime(self) -> None:
        """A readable tag, CUDA request, or wrong architecture must not slip in."""

        manifest = self.manifest()
        self.assertIn(f"image: {IMMUTABLE_IMAGE}", manifest)
        self.assertIn("imagePullPolicy: IfNotPresent", manifest)
        self.assertIn("kubernetes.io/arch: arm64", manifest)
        self.assertIn("runAsUser: 10003", manifest)
        self.assertIn("runAsGroup: 10003", manifest)
        self.assertIn("readOnlyRootFilesystem: true", manifest)
        self.assertNotIn("nvidia.com/gpu", manifest)

    def test_manifest_mounts_only_bounded_disposable_worker_writes(self) -> None:
        """Temporary audio/cache space must not become image-root or durable state."""

        manifest = self.manifest()
        for required_field in (
            "terminationGracePeriodSeconds: 780",
            "mountPath: /worker-scratch",
            "mountPath: /tmp",
            "mountPath: /home/clouddsp-demucs",
            "sizeLimit: 2Gi",
            "sizeLimit: 128Mi",
            "sizeLimit: 64Mi",
            "ephemeral-storage: 3Gi",
        ):
            self.assertIn(required_field, manifest)

    def test_manifest_uses_exact_private_services_and_restricted_secret_names(self) -> None:
        """The worker cannot receive public endpoints or administrator credentials."""

        manifest = self.manifest()
        for required_field in (
            "value: clouddsp-postgresql.clouddsp-data.svc",
            "value: http://clouddsp-minio.clouddsp-data.svc:9000",
            "value: clouddsp-rabbitmq.clouddsp-data.svc",
            "- name: DEMUCS_AMQP_PORT\n              # This is the broker's Service port, not a Demucs container port.\n              # RabbitMQ's NetworkPolicy admits this labeled Pod to TCP 5672.\n              value: \"5672\"",
            "value: clouddsp.demucs.requests",
            "name: clouddsp-demucs-database-credentials",
            "name: clouddsp-demucs-minio-credentials",
            "name: clouddsp-demucs-rabbitmq-credentials",
        ):
            self.assertIn(required_field, manifest)
        self.assertNotIn("bootstrap-credentials", manifest)
        self.assertNotIn("clouddsp-admin", manifest)
        # The explanatory comment may name `containerPort`, but an actual
        # container `ports` field would wrongly claim an inbound listener.
        self.assertNotIn("\n          ports:", manifest)


if __name__ == "__main__":
    unittest.main()
