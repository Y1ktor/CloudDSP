"""Structural tests for ADTOF's source-only CPU worker container recipe.

These tests read Dockerfile text only.  They do not build an image, invoke
Docker, resolve/install Python packages, import the model, read a Secret, or
change a Kubernetes resource.
"""

from __future__ import annotations

from pathlib import Path
import unittest


DOCKERFILE_PATH = Path(__file__).resolve().parents[1] / "Dockerfile"
PYTHON_311_IMAGE_DIGEST = (
    "docker.io/library/python@sha256:"
    "528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84"
)


class ADTOFDockerfileTests(unittest.TestCase):
    """Keep the image's CPU, verification, and runtime boundaries explicit."""

    def dockerfile(self) -> str:
        """Read the recipe as immutable source rather than invoking Docker."""

        return DOCKERFILE_PATH.read_text(encoding="utf-8")

    def test_recipe_uses_the_reviewed_python_311_base_in_both_stages(self) -> None:
        """Build and runtime must share the one reviewed multi-architecture base."""

        self.assertEqual(self.dockerfile().count(f"FROM {PYTHON_311_IMAGE_DIGEST}"), 2)

    def test_install_fails_closed_against_the_complete_cpu_lock(self) -> None:
        """A rebuild may not let pip select unreviewed dependencies or hashes."""

        dockerfile = self.dockerfile()
        for required_flag in (
            "--no-deps",
            "--no-build-isolation",
            "--require-hashes",
            "--extra-index-url https://download.pytorch.org/whl/cpu",
            "--requirement requirements.lock",
        ):
            self.assertIn(required_flag, dockerfile)

    def test_runtime_is_non_root_and_uses_the_reviewed_process_entrypoint(self) -> None:
        """Kubernetes must signal the Python worker directly, not a shell as root."""

        dockerfile = self.dockerfile()
        self.assertIn("USER 10005:10005", dockerfile)
        self.assertIn('ENTRYPOINT ["python", "-m", "app.worker_main"]', dockerfile)

    def test_recipe_leaves_required_scratch_directory_to_the_future_pod_volume(self) -> None:
        """A missing bounded emptyDir must fail rather than use image filesystem space."""

        dockerfile = self.dockerfile()
        self.assertNotIn("mkdir /worker-scratch", dockerfile)
        self.assertNotIn("mkdir -p /worker-scratch", dockerfile)
        self.assertIn("Do not make `/worker-scratch` here.", dockerfile)


if __name__ == "__main__":
    unittest.main()
