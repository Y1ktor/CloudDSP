"""Structural tests for the prepared ADTOF runtime-MinIO policy smoke Job.

The tests inspect manifest source only. They do not read a Secret, contact
MinIO, create an upload, run a container, or apply a Kubernetes resource.
"""

from __future__ import annotations

from pathlib import Path
import unittest


SMOKE_JOB_PATH = (
    Path(__file__).resolve().parents[1] / "adtof-minio-runtime-policy-smoke-job.yaml"
)
AWS_CLI_IMAGE = (
    "public.ecr.aws/aws-cli/aws-cli@sha256:"
    "f0dcd994da5c36ab09becf4259bba4c45ed4ecfd03d51c701912f4d8425b2d49"
)


class ADTOFMinIORuntimePolicySmokeManifestTests(unittest.TestCase):
    """Keep the authorization proof narrowly restricted to runtime access."""

    def manifest(self) -> str:
        """Read source only; Kubernetes interpretation is a later explicit task."""

        return SMOKE_JOB_PATH.read_text(encoding="utf-8")

    def test_is_one_disposable_app_namespace_job(self) -> None:
        """The proof is neither a long-running worker nor an externally exposed API."""

        manifest = self.manifest()
        self.assertIn("apiVersion: batch/v1\nkind: Job", manifest)
        self.assertIn(
            "name: adtof-minio-runtime-policy-smoke\n  namespace: clouddsp-app",
            manifest,
        )
        self.assertIn("ttlSecondsAfterFinished: 600", manifest)
        self.assertIn("backoffLimit: 0", manifest)
        self.assertIn("activeDeadlineSeconds: 120", manifest)
        self.assertIn("automountServiceAccountToken: false", manifest)
        self.assertIn("enableServiceLinks: false", manifest)
        self.assertNotIn("kind: Deployment", manifest)
        self.assertNotIn("kind: Service", manifest)
        self.assertNotIn("kind: Ingress", manifest)

    def test_mounts_only_the_existing_adtof_runtime_minio_secret(self) -> None:
        """No bootstrap, administrator, database, or broker credential may enter this Pod."""

        manifest = self.manifest()
        self.assertEqual(manifest.count("secretKeyRef:"), 2)
        self.assertEqual(manifest.count("name: clouddsp-adtof-minio-credentials"), 2)
        for required_field in (
            "key: ADTOF_S3_ACCESS_KEY",
            "key: ADTOF_S3_SECRET_KEY",
            "name: AWS_EC2_METADATA_DISABLED",
            'value: "true"',
        ):
            self.assertIn(required_field, manifest)
        for forbidden_field in (
            "MINIO_ROOT_",
            "bootstrap-credentials",
            "clouddsp-postgresql",
            "clouddsp-rabbitmq",
            "clouddsp-adtof-database-credentials",
            "clouddsp-adtof-rabbitmq-credentials",
        ):
            self.assertNotIn(forbidden_field, manifest)

    def test_uses_the_locked_client_and_private_service_with_hardened_pod_settings(self) -> None:
        """The proof must use a reviewed client and no ambient cluster privileges."""

        manifest = self.manifest()
        for required_field in (
            f"image: {AWS_CLI_IMAGE}",
            "imagePullPolicy: IfNotPresent",
            "endpoint_url=http://clouddsp-minio.clouddsp-data.svc:9000",
            "runAsNonRoot: true",
            "readOnlyRootFilesystem: true",
            "allowPrivilegeEscalation: false",
            "- ALL",
            "type: RuntimeDefault",
            "mountPath: /work",
            "sizeLimit: 16Mi",
        ):
            self.assertIn(required_field, manifest)

    def test_proves_allowed_temporary_work_and_expected_foreign_denial(self) -> None:
        """A live run must leave no completed object while still proving policy scope."""

        manifest = self.manifest()
        for required_field in (
            'allowed_key="midi/${run_id}/drums.mid"',
            'foreign_key="midi/${run_id}/vocals.mid"',
            "s3api create-multipart-upload",
            "s3api upload-part",
            "s3api list-parts",
            "s3api abort-multipart-upload",
            "expect_access_denied",
            "trap cleanup EXIT",
            "trap 'cleanup; exit 143' HUP INT TERM",
            "ADTOF runtime MinIO policy smoke test passed",
        ):
            self.assertIn(required_field, manifest)
        self.assertNotIn("s3api complete-multipart-upload", manifest)
        self.assertNotIn("s3api put-object", manifest)
        self.assertNotIn("s3api delete-object", manifest)


if __name__ == "__main__":
    unittest.main()
