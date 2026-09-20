"""Source-only checks for the three-request Basic Pitch KEDA burst Job.

The tests inspect the reviewed manifest text only. They do not read a local
Secret, pull an image, start a Pod, create a Job, query KEDA, or change the
cluster. The later explicit apply task is the integration proof.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_MANIFEST = Path(__file__).resolve().parent / "basic-pitch-keda-burst-smoke-job.yaml"
_IMAGE = (
    "clouddsp-registry.localhost:5001/basic-pitch-keda-burst-smoke-client"
    "@sha256:49d4bae35dcb36930c6a76e886819c4cf9cbea4eca0e3548658cc9d1e317a659"
)


class BasicPitchKedaBurstSmokeJobManifestTests(unittest.TestCase):
    """Prevent a finite scale observation from becoming a privileged worker."""

    def test_job_is_bounded_nonretrying_and_uses_only_its_pinned_client(self) -> None:
        """Fixed-name Job retries need review instead of colliding with evidence."""

        manifest = _MANIFEST.read_text(encoding="utf-8")

        self.assertIn("apiVersion: batch/v1", manifest)
        self.assertIn("kind: Job", manifest)
        self.assertIn("name: basic-pitch-keda-burst-smoke", manifest)
        self.assertIn("namespace: clouddsp-app", manifest)
        self.assertIn("ttlSecondsAfterFinished: 900", manifest)
        self.assertIn("backoffLimit: 0", manifest)
        self.assertIn("activeDeadlineSeconds: 420", manifest)
        self.assertIn("restartPolicy: Never", manifest)
        self.assertIn(f"image: {_IMAGE}", manifest)
        self.assertNotIn("image: clouddsp-registry.localhost:5001/basic-pitch-keda-burst-smoke-client:0.1.0", manifest)

    def test_job_receives_exactly_two_restricted_secret_identities(self) -> None:
        """No AMQP, Kubernetes, admin, or normal-user credential can enter the Pod."""

        manifest = _MANIFEST.read_text(encoding="utf-8")

        self.assertEqual(manifest.count("secretKeyRef:"), 5)
        self.assertIn("clouddsp-basic-pitch-keda-burst-smoke-database-credentials", manifest)
        self.assertIn("clouddsp-basic-pitch-keda-burst-smoke-minio-credentials", manifest)
        for forbidden in (
            "clouddsp-postgresql-credentials",
            "clouddsp-minio-credentials",
            "rabbitmq",
            "KUBERNETES_SERVICE_HOST",
            "serviceAccountName:",
        ):
            self.assertNotIn(forbidden, manifest)
        self.assertIn("automountServiceAccountToken: false", manifest)
        self.assertIn("enableServiceLinks: false", manifest)

    def test_job_has_private_routes_and_restrictive_pod_security(self) -> None:
        """The smoke client must leave scheduling and processing to KEDA/workers."""

        manifest = _MANIFEST.read_text(encoding="utf-8")

        self.assertIn("clouddsp-postgresql.clouddsp-data.svc", manifest)
        self.assertIn("http://clouddsp-minio.clouddsp-data.svc:9000", manifest)
        self.assertIn("AWS_EC2_METADATA_DISABLED", manifest)
        self.assertIn("runAsNonRoot: true", manifest)
        self.assertIn("runAsUser: 10007", manifest)
        self.assertIn("readOnlyRootFilesystem: true", manifest)
        self.assertIn("allowPrivilegeEscalation: false", manifest)
        self.assertIn("- ALL", manifest)
        self.assertNotIn("hostNetwork: true", manifest)
        self.assertNotIn("privileged: true", manifest)
        self.assertNotIn("command:", manifest)


if __name__ == "__main__":
    unittest.main()
