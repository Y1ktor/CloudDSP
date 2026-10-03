"""Structural tests for the Demucs source-only CPU worker container recipe.

These tests read Dockerfile text only. They do not build an image, invoke
Docker, resolve/install Python packages, import Demucs, read a Secret, or
change a Kubernetes resource.
"""

from __future__ import annotations

from pathlib import Path
import unittest


DOCKERFILE_PATH = Path(__file__).resolve().parents[1] / "Dockerfile"


class DemucsDockerfileTests(unittest.TestCase):
    """Keep the runtime's process and scratch-volume boundaries explicit."""

    def dockerfile(self) -> str:
        """Read the recipe as immutable source rather than invoking Docker."""

        return DOCKERFILE_PATH.read_text(encoding="utf-8")

    def test_runtime_is_non_root_and_uses_the_reviewed_process_entrypoint(self) -> None:
        """Kubernetes must signal the Python worker directly, never a root shell."""

        dockerfile = self.dockerfile()
        self.assertIn("USER clouddsp-demucs", dockerfile)
        self.assertIn('ENTRYPOINT ["python", "-m", "app.worker_main"]', dockerfile)

    def test_recipe_leaves_required_scratch_directory_to_the_future_pod_volume(self) -> None:
        """A missing bounded emptyDir must fail rather than use image filesystem space."""

        dockerfile = self.dockerfile()
        self.assertNotIn("mkdir /worker-scratch", dockerfile)
        self.assertNotIn("mkdir -p /worker-scratch", dockerfile)
        self.assertIn("Do not create that directory here.", dockerfile)

    def test_validation_stage_receives_manifest_only_for_source_structure_tests(self) -> None:
        """The runtime image must not carry Kubernetes source it never reads."""

        dockerfile = self.dockerfile()
        self.assertIn("COPY demucs-deployment.yaml ./demucs-deployment.yaml", dockerfile)
        self.assertIn(
            "COPY demucs-recovery-verifier-bootstrap-job.yaml "
            "./demucs-recovery-verifier-bootstrap-job.yaml",
            dockerfile,
        )
        self.assertIn("neither manifest enters the final runtime stage.", dockerfile)

    def test_validation_runs_real_cpu_inference_through_the_reviewed_launcher(self) -> None:
        """Imports alone cannot detect the reproduced ARM64 MKLDNN SIGILL path."""

        dockerfile = self.dockerfile()
        self.assertIn("cd /tmp/demucs-local-arm64-cpu-smoke/output", dockerfile)
        self.assertIn("/usr/local/bin/python /app/app/processing/demucs_cpu_cli.py", dockerfile)
        self.assertIn("demucs-local-arm64-cpu-smoke", dockerfile)
        self.assertIn("no_vocals.wav", dockerfile)
        # The explanatory prose may wrap this phrase across source lines; the
        # important structural contract is that the recipe documents the
        # MKLDNN-specific validation, not one particular whitespace layout.
        self.assertIn("MKLDNN", dockerfile)


if __name__ == "__main__":
    unittest.main()
