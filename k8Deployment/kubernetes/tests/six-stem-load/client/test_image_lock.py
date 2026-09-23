"""Source-only guardrails for the published six-stem lifecycle image lock."""

from __future__ import annotations

from pathlib import Path
import unittest


_CLIENT_DIRECTORY = Path(__file__).resolve().parent
_IMAGE_LOCK = _CLIENT_DIRECTORY.parents[2] / "images.lock.yaml"
_DIGEST = "sha256:96b002198c81cf3f9c436a0849e05d2df0336fc647196a7a4d7a83f6a60b9edf"
_REFERENCE = f"clouddsp-registry.localhost:5001/six-stem-load-client@{_DIGEST}"


class SixStemLoadClientImageLockTests(unittest.TestCase):
    """Keep all load-test containers pinned to the reviewed ARM64 image bytes."""

    def test_published_image_and_source_lock_describe_the_same_client(self) -> None:
        """The catalog captures digest, architecture, source, tests, and local size."""

        source = _IMAGE_LOCK.read_text(encoding="utf-8")
        for required in (
            "six-stem-load-client:",
            'sourceTag: "0.1.19-minio-owned-volume-fix"',
            f"immutableReference: {_REFERENCE}",
            "localDockerSizeBytes: 78161300",
            'localDockerSize: "74.54 MiB"',
            'pushedAt: "2026-09-23"',
            "sourceLock: buildSources.six-stem-load-client",
            "publishedImageLock: images.six-stem-load-client",
            "targetPlatform: linux/arm64",
            "requirementsLock: kubernetes/tests/six-stem-load/client/requirements.lock",
            "auxiliaryImageLocks:",
            "- images.minio-mc",
            'version: "1.43.89"',
            'version: "1.43.93"',
            'version: "4.16.0"',
            "dependency: transitive",
            "kubernetes/tests/six-stem-load/client/Dockerfile",
            "test_authenticated_load_client.py",
            "test_owner_bound_job_verifier.py",
            "test_observer_capability_contract.py",
            "test_postgresql_observer_contract.py",
            "test_postgresql_durable_state_observer.py",
            "test_postgresql_admin_boundary.py",
            "test_postgresql_observer_report.py",
            "test_load_test_terminal_outcome.py",
            "test_minio_observer_bootstrap.py",
            "test_minio_artifact_observer.py",
            "test_rabbitmq_queue_observer.py",
            "test_keda_scale_observer.py",
            "test_observer_start_gate.py",
            "test_private_volume_setup.py",
            "test_six_stem_load_observer.py",
        ):
            self.assertIn(required, source)


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
