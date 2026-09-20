"""Source-only checks for the published Basic Pitch KEDA burst client image.

These assertions read the image-lock catalog only. They do not pull/build an
image, access the local registry, read a Secret, or create a Kubernetes Job.
The reviewed `docker pull` is the separate integration proof that the registry
can resolve the immutable reference written here.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_CLIENT_DIRECTORY = Path(__file__).resolve().parent
# `client/` → burst-smoke directory → `tests/` → `kubernetes/`, where the
# shared image catalog lives. Keeping this relative derivation lets the source
# test run from any current working directory without a machine-specific path.
_IMAGE_LOCK = _CLIENT_DIRECTORY.parents[2] / "images.lock.yaml"
_DIGEST = "sha256:49d4bae35dcb36930c6a76e886819c4cf9cbea4eca0e3548658cc9d1e317a659"
_REFERENCE = (
    "clouddsp-registry.localhost:5001/basic-pitch-keda-burst-smoke-client"
    f"@{_DIGEST}"
)


class ImageLockTests(unittest.TestCase):
    """Keep a finite test Job pinned to reviewed local-registry bytes."""

    def test_published_image_and_source_lock_describe_the_same_client(self) -> None:
        """A future manifest gets a digest, ARM64 scope, and reproducible source."""

        lock = _IMAGE_LOCK.read_text(encoding="utf-8")

        self.assertIn("basic-pitch-keda-burst-smoke-client:", lock)
        self.assertIn('sourceTag: "0.1.0-three-request-keda-burst"', lock)
        self.assertIn(f"immutableReference: {_REFERENCE}", lock)
        self.assertIn("localDockerSizeBytes: 66136836", lock)
        self.assertIn('localDockerSize: "63.07 MiB"', lock)
        self.assertIn('pushedAt: "2026-09-20"', lock)
        self.assertIn("sourceLock: buildSources.basic-pitch-keda-burst-smoke-client", lock)
        self.assertIn("publishedImageLock: images.basic-pitch-keda-burst-smoke-client", lock)
        self.assertIn("targetPlatform: linux/arm64", lock)
        self.assertIn("kubernetes/tests/basic-pitch-keda-burst-smoke/client/Dockerfile", lock)
        self.assertIn("kubernetes/tests/basic-pitch-keda-burst-smoke/client/requirements.lock", lock)


if __name__ == "__main__":
    unittest.main()
