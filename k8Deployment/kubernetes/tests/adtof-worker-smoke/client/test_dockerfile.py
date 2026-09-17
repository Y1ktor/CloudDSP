"""Structural tests for the source-only ADTOF smoke-client image recipe.

These tests inspect text only. They do not invoke Docker, install a package,
open a network connection, access a Secret, or create a Kubernetes resource.
"""

from __future__ import annotations

from pathlib import Path
import unittest


DOCKERFILE_PATH = Path(__file__).with_name("Dockerfile")
PYTHON_RUNTIME = (
    "docker.io/library/python@sha256:"
    "782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254"
)


class ADTOFWorkerSmokeDockerfileTests(unittest.TestCase):
    """Keep the future one-shot Job image reproducible and least-privilege."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load only the checked-in recipe whose static contract is under test."""

        cls.source = DOCKERFILE_PATH.read_text(encoding="utf-8")

    def test_uses_the_reviewed_python_digest_for_matching_validation_and_runtime_stages(self) -> None:
        """The ARM64 binary-wheel lock must never be paired with a mutable or mismatched base."""

        self.assertEqual(self.source.count(f"FROM {PYTHON_RUNTIME}"), 2)
        self.assertNotIn("FROM python:", self.source)
        self.assertNotIn("FROM python@", self.source)

    def test_install_is_complete_hash_enforced_and_build_time_only(self) -> None:
        """Dependencies are immutable image inputs, never a runtime network operation."""

        self.assertIn("--no-deps", self.source)
        self.assertIn("--require-hashes", self.source)
        self.assertIn("--no-cache-dir", self.source)
        self.assertIn("--no-compile", self.source)
        self.assertEqual(self.source.count("python -m pip install"), 1)
        self.assertIn("COPY requirements.lock ./requirements.lock", self.source)

    def test_runtime_copies_only_the_bounded_client_graph_and_uses_a_dedicated_non_root_account(self) -> None:
        """Tests, credentials, installers, and broad operating-system privileges stay out of runtime."""

        runtime = self.source.split("AS runtime", maxsplit=1)[1]
        self.assertIn("COPY --from=validation /usr/local/lib/python3.12/site-packages", runtime)
        self.assertIn("--uid 10006", runtime)
        self.assertIn("USER 10006:10006", runtime)
        self.assertIn("/usr/sbin/nologin", runtime)
        self.assertIn("ENTRYPOINT [\"python\", \"/app/adtof_worker_smoke_entrypoint.py\"]", runtime)
        self.assertNotIn("test_", runtime)
        self.assertNotIn("requirements.lock", runtime)
        self.assertNotIn("apt-get", runtime)
        self.assertNotIn("curl ", runtime)
        self.assertNotIn("wget ", runtime)

    def test_recipe_does_not_install_or_copy_broker_cloud_cli_or_cluster_client_capabilities(self) -> None:
        """Boto3 is required for restricted MinIO access; unrelated clients must not become image inputs."""

        # Comments may correctly explain that the image is *not* a RabbitMQ or
        # Kubernetes client, so inspect the executable/copy instructions rather
        # than treating explanatory prose as a capability.
        instructions = "\n".join(
            line.strip().lower()
            for line in self.source.splitlines()
            if line.startswith(("COPY ", "RUN ", "ENTRYPOINT "))
        )
        for forbidden in (
            "pika",
            "kubernetes",
            "kubectl",
            "helm",
            "awscli",
            "aws-cli",
            "nvidia",
            "cuda",
        ):
            self.assertNotIn(forbidden, instructions)


if __name__ == "__main__":
    unittest.main()
