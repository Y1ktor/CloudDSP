"""Structural guardrails for Demucs smoke source manifests.

These tests inspect local version-controlled text only. They never build an
image, contact Kubernetes/MinIO/PostgreSQL/RabbitMQ, create a Secret, or add a
durable Job/event/object. The real resources remain explicit operator actions.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest


_DIRECTORY = Path(__file__).resolve().parent
_JOB = _DIRECTORY / "demucs-worker-smoke-job.yaml"
_POLICY = _DIRECTORY / "demucs-worker-smoke-minio-policy-v001-configmap.yaml"
_MINIO_BOOTSTRAP = _DIRECTORY / "minio-demucs-worker-smoke-objects-bootstrap-job.yaml"
_DATABASE_BOOTSTRAP = _DIRECTORY / "demucs-worker-smoke-database-bootstrap-job.yaml"
_FAILED_RUN_CLEANUP = _DIRECTORY / "demucs-worker-smoke-failed-run-cleanup-job.yaml"
_LOCK = _DIRECTORY.parent.parent / "images.lock.yaml"


def _policy_document() -> dict[str, object]:
    """Parse the literal JSON without introducing a YAML runtime dependency."""

    marker = "  demucs-worker-smoke-objects-policy.json: |\n"
    return json.loads(_POLICY.read_text(encoding="utf-8").split(marker, maxsplit=1)[1])


class DemucsWorkerSmokeManifestTests(unittest.TestCase):
    """Keep the test finite, digest-pinned, least-privileged, and exact-key."""

    def test_job_uses_only_the_published_arm64_one_shot_client(self) -> None:
        """A mutable tag/controller would make test bytes or retry behavior ambiguous."""

        source = _JOB.read_text(encoding="utf-8")
        self.assertIn("apiVersion: batch/v1\nkind: Job", source)
        for required in (
            "name: demucs-worker-smoke", "namespace: clouddsp-app", "restartPolicy: Never",
            "backoffLimit: 0", "ttlSecondsAfterFinished: 600", "activeDeadlineSeconds: 840",
            "kubernetes.io/arch: arm64", "automountServiceAccountToken: false",
            "enableServiceLinks: false", "runAsNonRoot: true", "runAsUser: 10003",
            "readOnlyRootFilesystem: true", "allowPrivilegeEscalation: false",
            "clouddsp-registry.localhost:5001/demucs-worker-smoke-client@sha256:da6ff550d60acdb84949cf8e26c47d30ca3f434bb7e1cc5121034cc2e95cb743",
        ):
            self.assertIn(required, source)
        self.assertNotIn("kind: Deployment", source)
        self.assertNotIn("serviceAccountName:", source)
        self.assertNotIn("hostPath:", source)
        self.assertNotIn("ports:", source)

    def test_job_mounts_only_restricted_private_service_credentials(self) -> None:
        """The test may not inherit worker, broker, Keycloak, root, or cloud access."""

        source = _JOB.read_text(encoding="utf-8")
        for required in (
            "clouddsp-postgresql.clouddsp-data.svc", "http://clouddsp-minio.clouddsp-data.svc:9000",
            "clouddsp-demucs-worker-smoke-database-credentials",
            "clouddsp-demucs-worker-smoke-minio-credentials", "AWS_EC2_METADATA_DISABLED",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "clouddsp-postgresql-credentials", "clouddsp-minio-root-credentials",
            "RABBITMQ_", "KEYCLOAK_", "DEMUCS_AMQP_", "DEMUCS_DB_PASSWORD",
            "DEMUCS_S3_SECRET_KEY", "value: http://localhost",
        ):
            self.assertNotIn(forbidden, source)

    def test_minio_policy_has_one_writable_source_and_read_delete_only_outputs(self) -> None:
        """No bucket list/presign/wildcard access is needed to observe real outputs."""

        document = _policy_document()
        statements = document["Statement"]
        self.assertEqual(document["Version"], "2012-10-17")
        self.assertIsInstance(statements, list)
        self.assertEqual(len(statements), 2)
        source_statement, output_statement = statements
        self.assertEqual(source_statement["Action"], ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"])
        self.assertIn("/uploads/12139966-5891-4d53-a817-f3ce1f264c61/", source_statement["Resource"])
        self.assertEqual(output_statement["Action"], ["s3:GetObject", "s3:DeleteObject"])
        self.assertEqual(len(output_statement["Resource"]), 4)
        rendered = json.dumps(document, sort_keys=True)
        self.assertNotIn("s3:ListBucket", rendered)
        self.assertNotIn("s3:PutObject", json.dumps(output_statement, sort_keys=True))
        self.assertNotIn("*", rendered)

    def test_bootstraps_are_separate_and_never_gain_pipeline_client_duties(self) -> None:
        """Administrator identity setup must not become a source/job/message operation."""

        database = _DATABASE_BOOTSTRAP.read_text(encoding="utf-8")
        minio = _MINIO_BOOTSTRAP.read_text(encoding="utf-8")
        self.assertIn("clouddsp-demucs-worker-smoke-database-bootstrap-credentials", database)
        self.assertIn("clouddsp-postgresql-credentials", database)
        self.assertIn("clouddsp_demucs_worker_smoke_prepare", database)
        self.assertIn("has_table_privilege", database)
        self.assertNotIn("RABBITMQ_", database)
        self.assertIn("clouddsp-minio-root-credentials", minio)
        self.assertIn("clouddsp-demucs-worker-smoke-minio-bootstrap-credentials", minio)
        self.assertIn("clouddsp-demucs-worker-smoke-objects-v001", minio)
        self.assertIn("remove-root-alias-from-temporary-config", minio)
        self.assertNotIn("POSTGRES_", minio)
        self.assertNotIn("RABBITMQ_", minio)

    def test_manifest_digest_is_recorded_by_the_image_lock(self) -> None:
        """The Job's immutable byte reference must have reproducible local provenance."""

        lock = _LOCK.read_text(encoding="utf-8")
        self.assertIn("demucs-worker-smoke-client:", lock)
        self.assertIn("sha256:da6ff550d60acdb84949cf8e26c47d30ca3f434bb7e1cc5121034cc2e95cb743", lock)

    def test_failed_run_cleanup_is_exact_and_has_no_broker_authority(self) -> None:
        """A diagnosed cleanup may remove only the documented fixed evidence."""

        source = _FAILED_RUN_CLEANUP.read_text(encoding="utf-8")
        for required in (
            "name: demucs-worker-smoke-failed-run-cleanup",
            "namespace: clouddsp-data",
            "backoffLimit: 0",
            "automountServiceAccountToken: false",
            "clouddsp-minio-root-credentials",
            "clouddsp-postgresql-credentials",
            "clouddsp-registry.localhost:5001/demucs-worker-smoke-client@sha256:da6ff550d60acdb84949cf8e26c47d30ca3f434bb7e1cc5121034cc2e95cb743",
            "client.delete_object(Bucket=\"clouddsp-uploads\", Key=object_key)",
            "uploads/12139966-5891-4d53-a817-f3ce1f264c61/demucs-worker-smoke.wav",
            "stems/12139966-5891-4d53-a817-f3ce1f264c61/vocals.wav",
            "stems/12139966-5891-4d53-a817-f3ce1f264c61/no_vocals.wav",
            "midi/12139966-5891-4d53-a817-f3ce1f264c61/vocals.mid",
            "midi/12139966-5891-4d53-a817-f3ce1f264c61/no_vocals.mid",
            "e20cf942-ef7b-478e-89c4-f4795ed801ec",
            "AND job.status = 'source_uploaded'",
            "AND task.status IN ('leased', 'running')",
            "AND task.attempt_count >= 1",
            "other_task.task_id <> task.task_id",
            "AND NOT EXISTS",
            "expected one diagnosed stopped Demucs smoke task",
        ):
            self.assertIn(required, source)
        self.assertLess(
            source.index("name: delete-only-diagnosed-stopped-task"),
            source.index("name: delete-only-fixed-smoke-objects"),
        )
        self.assertNotIn("RABBITMQ_", source)
        self.assertNotIn("purge_queue", source)
        self.assertNotIn("mc rm", source)
        self.assertNotIn("serviceAccountName:", source)
        # Task IDs and lease tokens are generated anew per smoke attempt.
        # Keeping neither in the recovery source makes this operator Job
        # reusable while its fixed Job/event/object coordinates stay narrow.
        self.assertNotIn("lease_token =", source)


if __name__ == "__main__":
    unittest.main()
