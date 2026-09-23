"""Static checks for the versioned Job API artifact-read policy handoff.

The tests keep the immutable Kubernetes policy and its short-lived attachment
Job aligned without contacting MinIO or reading any Secret.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_DIRECTORY = Path(__file__).resolve().parent
_POLICY = _DIRECTORY / "minio-job-api-artifact-read-policy-v003-configmap.yaml"
_JOB = _DIRECTORY / "minio-job-api-artifact-read-bootstrap-job.yaml"


class JobApiArtifactReadPolicyTests(unittest.TestCase):
    """Protect the least-privilege source/output scopes and bootstrap order."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = _POLICY.read_text(encoding="utf-8")
        cls.job = _JOB.read_text(encoding="utf-8")

    def test_policy_keeps_source_upload_and_adds_only_output_gets(self) -> None:
        """The signer can read private artifacts but cannot delete or list them."""

        self.assertIn("immutable: true", self.policy)
        self.assertIn('"Sid": "ReadAndWriteOnlySourceUploadObjects"', self.policy)
        self.assertIn('"Resource": "arn:aws:s3:::clouddsp-uploads/uploads/*"', self.policy)
        self.assertIn('"Sid": "ReadOnlyDemucsStemArtifacts"', self.policy)
        self.assertIn('"Resource": "arn:aws:s3:::clouddsp-uploads/stems/*"', self.policy)
        self.assertIn('"Sid": "ReadOnlyMidiAndTempoArtifacts"', self.policy)
        self.assertIn('"Resource": "arn:aws:s3:::clouddsp-uploads/midi/*"', self.policy)
        self.assertNotIn("s3:DeleteObject", self.policy)
        self.assertNotIn('"Action": "s3:ListBucket"', self.policy.split('"Sid": "ReadOnlyDemucsStemArtifacts"', 1)[1])
        self.assertNotIn('"Action": "s3:ListBucket"', self.policy.split('"Sid": "ReadOnlyMidiAndTempoArtifacts"', 1)[1])

    def test_attachment_job_uses_digest_pinned_client_and_existing_scoped_identity(self) -> None:
        """Only an ordered MinIO admin Job mutates identity policy attachments."""

        self.assertIn("kind: Job", self.job)
        self.assertIn("backoffLimit: 0", self.job)
        self.assertIn("automountServiceAccountToken: false", self.job)
        self.assertIn("clouddsp-minio-root-credentials", self.job)
        self.assertIn("clouddsp-job-api-minio-bootstrap-credentials", self.job)
        self.assertIn("clouddsp-job-api-artifact-read-v003", self.job)
        self.assertIn("--user=$(JOB_API_S3_ACCESS_KEY)", self.job)
        self.assertIn("args: [alias, remove, minio-admin]", self.job)
        self.assertNotIn("s3:DeleteObject", self.job)


if __name__ == "__main__":  # pragma: no cover - directly runnable learning aid.
    unittest.main()
