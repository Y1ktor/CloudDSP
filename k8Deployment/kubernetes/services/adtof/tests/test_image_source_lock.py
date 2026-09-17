"""Structural tests for ADTOF's source and immutable output image locks.

These tests read the human-reviewed image-lock catalog as text. They do not
parse a mutable registry response, build/push an OCI image, use Docker, read a
Secret, or create a Kubernetes resource.
"""

from __future__ import annotations

import os
from pathlib import Path
import unittest


_IMAGE_LOCK_ENVIRONMENT_VARIABLE = "CLOUDDSP_IMAGE_LOCK_PATH"


def default_image_lock_path() -> Path:
    """Locate the catalog in a repository checkout without assuming Docker paths.

    The worker Docker build intentionally uses only this service directory as
    context, so the repository-level catalog is unavailable there.  The
    Dockerfile supplies a non-existent override only for its throwaway test
    stage; the tests then skip their repository-structure assertions while all
    executable worker tests continue. Local source test runs use this checkout
    path and must exercise the provenance assertions normally.
    """

    configured_path = os.environ.get(_IMAGE_LOCK_ENVIRONMENT_VARIABLE)
    if configured_path is not None:
        return Path(configured_path)
    return Path(__file__).resolve().parents[3] / "images.lock.yaml"


IMAGE_LOCK_PATH = default_image_lock_path()


def adtof_source_section() -> str:
    """Return only the reviewed `buildSources.adtof` section from the catalog."""

    image_lock = IMAGE_LOCK_PATH.read_text(encoding="utf-8")
    sources = image_lock.split("\nbuildSources:\n", maxsplit=1)[1]
    return sources.split("\n  adtof:\n", maxsplit=1)[1].split("\n  demucs:\n", maxsplit=1)[0]


def adtof_image_section() -> str:
    """Return only the reviewed `images.adtof` output record from the catalog."""

    image_lock = IMAGE_LOCK_PATH.read_text(encoding="utf-8")
    images = image_lock.split("images:\n", maxsplit=1)[1].split("\nbuildSources:\n", maxsplit=1)[0]
    return images.split("\n  adtof:\n", maxsplit=1)[1].split("\n  job-api:\n", maxsplit=1)[0]


@unittest.skipUnless(
    IMAGE_LOCK_PATH.is_file(),
    "the repository-level image lock is intentionally outside the worker build context",
)
class ADTOFImageSourceLockTests(unittest.TestCase):
    """Keep the reviewed registry artifact tied to its exact worker source."""

    def test_source_record_fixes_the_local_cpu_image_inputs(self) -> None:
        """A future build has one reviewed base, source tree, lock, platform, and PID 1."""

        section = adtof_source_section()
        for required_field in (
            "baseImageLock: images.basic-pitch-python-runtime",
            "sourceDirectory: kubernetes/services/adtof/app",
            "dockerfile: kubernetes/services/adtof/Dockerfile",
            "requirementsLock: kubernetes/services/adtof/requirements.lock",
            "unitTestDirectory: kubernetes/services/adtof/tests",
            "intendedRepository: clouddsp-registry.localhost:5001/adtof",
            "publishedImageLock: images.adtof",
            "targetPlatform: linux/arm64",
            "devicePolicy: cpu",
            "pythonVersion: \"3.11\"",
            "- app.worker_main",
            "85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9",
        ):
            self.assertIn(required_field, section)

    def test_registry_output_lock_is_immutable_and_matches_the_reviewed_build(self) -> None:
        """A future workload receives only the proven ARM64 CPU artifact bytes."""

        section = adtof_image_section()
        for required_field in (
            "repository: clouddsp-registry.localhost:5001/adtof",
            "sourceTag: \"0.1.4-exhausted-lease-recovery\"",
            "digest: sha256:c8706bb0b814ed3a0c1e58455a7c153848ddf7808e65d4e3f4ed52a37b521b91",
            "immutableReference: clouddsp-registry.localhost:5001/adtof@sha256:c8706bb0b814ed3a0c1e58455a7c153848ddf7808e65d4e3f4ed52a37b521b91",
            "- linux/arm64/v8",
            "sourceLock: buildSources.adtof",
            "localDockerSizeBytes: 346446624",
            "localDockerSize: \"330.40 MiB\"",
            "pushedAt: \"2026-09-16\"",
        ):
            self.assertIn(required_field, section)


if __name__ == "__main__":
    unittest.main()
