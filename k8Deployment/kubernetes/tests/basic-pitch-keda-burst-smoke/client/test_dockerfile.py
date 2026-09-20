"""Source checks for the Basic Pitch KEDA burst client's future container image.

These assertions inspect Dockerfile text only. They do not build/pull an image,
install a package, access a registry, read a Secret, or make a cluster change.
The following image-build task is the integration validation of this source.
"""

from __future__ import annotations

import unittest
from pathlib import Path


_DOCKERFILE = Path(__file__).resolve().parent / "Dockerfile"
_PYTHON_DIGEST = "docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254"


class DockerfileTests(unittest.TestCase):
    """Prevent a disposable verifier from widening into a privileged runtime."""

    def test_validation_stage_installs_only_hash_locked_closure_and_tests_it(self) -> None:
        """Package resolution and test source remain outside the final image."""

        dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
        self.assertEqual(dockerfile.count(f"FROM {_PYTHON_DIGEST}"), 2)
        self.assertIn("--no-deps", dockerfile)
        self.assertIn("--require-hashes", dockerfile)
        self.assertIn("COPY requirements.lock ./requirements.lock", dockerfile)
        self.assertIn("COPY test_*.py ./", dockerfile)
        self.assertIn("python -W error::ResourceWarning -m unittest discover", dockerfile)

    def test_final_stage_has_only_runtime_modules_and_non_root_pid_one(self) -> None:
        """Secrets and test/build material must not survive into the Job image."""

        dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
        runtime = dockerfile.split(" AS runtime", maxsplit=1)[1]
        self.assertNotIn("requirements.lock", runtime)
        self.assertNotIn("test_*.py", runtime)
        self.assertNotIn("python -m pip install", runtime)
        self.assertIn("/usr/local/lib/python3.12/site-packages", runtime)
        for module in (
            "basic_pitch_keda_burst_smoke.py",
            "minio_adapter.py",
            "postgresql_adapter.py",
            "burst_runner.py",
            "burst_entrypoint.py",
        ):
            self.assertIn(f"/app/{module}", runtime)
        self.assertIn("USER 10007:10007", runtime)
        self.assertIn('ENTRYPOINT ["python", "/app/burst_entrypoint.py"]', runtime)
        for forbidden in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "POSTGRES_PASSWORD", "kubectl", "pika"):
            self.assertNotIn(forbidden, runtime)


if __name__ == "__main__":
    unittest.main()
