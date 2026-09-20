"""Source-only safety checks for the Basic Pitch KEDA burst MinIO boundary.

These tests inspect reviewed YAML/JSON text only. They do not read a Secret,
create a MinIO user/policy/object, call RabbitMQ/PostgreSQL, or create a Pod.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_DIRECTORY = Path(__file__).resolve().parent
_POLICY = _DIRECTORY / "basic-pitch-keda-burst-smoke-minio-policy-v001-configmap.yaml"
_RUNTIME_SECRET = _DIRECTORY / "basic-pitch-keda-burst-smoke-minio-credentials.secret.example.yaml"
_BOOTSTRAP_SECRET = _DIRECTORY / "basic-pitch-keda-burst-smoke-minio-bootstrap-credentials.secret.example.yaml"
_BOOTSTRAP_JOB = _DIRECTORY / "minio-basic-pitch-keda-burst-smoke-objects-bootstrap-job.yaml"


class BasicPitchKedaBurstSmokeMinioManifestTests(unittest.TestCase):
    """Prevent this burst fixture from silently widening MinIO access."""

    def test_policy_allows_only_six_literal_contract_object_keys(self) -> None:
        """The client needs three WAV/MIDI pairs, not a bucket or prefix grant."""

        policy = _POLICY.read_text(encoding="utf-8")

        self.assertIn("immutable: true", policy)
        for action in ("s3:GetObject", "s3:PutObject", "s3:DeleteObject"):
            self.assertIn(action, policy)
        self.assertNotIn("s3:ListBucket", policy)
        self.assertNotIn("s3:*", policy)
        self.assertNotIn("/*", policy)
        self.assertEqual(policy.count("arn:aws:s3:::clouddsp-uploads/"), 6)
        for path in (
            "stems/2a1e8097-7da6-4d06-8b27-c006b02e0d91/vocals.wav",
            "midi/2a1e8097-7da6-4d06-8b27-c006b02e0d91/vocals.mid",
            "stems/8e27f4ad-6c19-4e3f-9b56-fa287429c0a2/bass.wav",
            "midi/8e27f4ad-6c19-4e3f-9b56-fa287429c0a2/bass.mid",
            "stems/96ba2e39-e035-4fe8-a50a-b1e6db208ac3/other.wav",
            "midi/96ba2e39-e035-4fe8-a50a-b1e6db208ac3/other.mid",
        ):
            self.assertIn(path, policy)

    def test_runtime_and_temporary_secrets_are_namespaced_copies_of_one_identity(self) -> None:
        """The bootstrap needs a local duplicate; the runtime never gets root."""

        runtime = _RUNTIME_SECRET.read_text(encoding="utf-8")
        bootstrap = _BOOTSTRAP_SECRET.read_text(encoding="utf-8")

        self.assertIn("namespace: clouddsp-app", runtime)
        self.assertIn("namespace: clouddsp-data", bootstrap)
        for manifest in (runtime, bootstrap):
            self.assertIn("BASIC_PITCH_KEDA_BURST_SMOKE_S3_ACCESS_KEY", manifest)
            self.assertIn("BASIC_PITCH_KEDA_BURST_SMOKE_S3_SECRET_KEY", manifest)
            self.assertIn("clouddsp-basic-pitch-keda-burst-smoke", manifest)
            self.assertNotIn("MINIO_ROOT_PASSWORD", manifest)

    def test_bootstrap_attaches_only_the_versioned_fixed_key_policy(self) -> None:
        """Admin authority stays in a short-lived data-namespace bootstrap Pod."""

        job = _BOOTSTRAP_JOB.read_text(encoding="utf-8")

        self.assertIn("name: minio-basic-pitch-keda-burst-smoke-objects-bootstrap", job)
        self.assertIn("namespace: clouddsp-data", job)
        self.assertIn("automountServiceAccountToken: false", job)
        self.assertIn("clouddsp-minio-root-credentials", job)
        self.assertIn("clouddsp-basic-pitch-keda-burst-smoke-minio-bootstrap-credentials", job)
        self.assertIn("clouddsp-basic-pitch-keda-burst-smoke-objects-policy-v001", job)
        self.assertIn("clouddsp-basic-pitch-keda-burst-smoke-objects-v001", job)
        self.assertIn("mc admin policy update", job)
        self.assertIn("readOnlyRootFilesystem: true", job)
        # The policy/user init container previously hit the 128 MiB cap after
        # creating the user but before attaching its policy. Keep this exact
        # bounded repair visible rather than raising every bootstrap container.
        self.assertIn("create-or-rotate-six-key-burst-identity", job)
        self.assertIn("memory: 512Mi", job)
        self.assertNotIn("s3:ListBucket", job)


if __name__ == "__main__":
    unittest.main()
