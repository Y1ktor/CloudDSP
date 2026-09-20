"""Source-only guardrails for the Basic Pitch container-startup benchmark.

The benchmark is intentionally unable to consume a worker task. These tests
read manifest text only: they do not create Jobs, pull the image, or inspect a
cluster node.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_MANIFEST = (
    Path(__file__).resolve().parent / "basic-pitch-container-startup-benchmark-jobs.yaml"
)


class BasicPitchContainerStartupBenchmarkManifestTests(unittest.TestCase):
    """Keep cached/cold timing evidence separate from AMQP worker behavior."""

    def test_uses_the_deployed_immutable_image_on_both_local_agent_nodes(self) -> None:
        """The comparison is useful only when image bytes and node targets match."""

        manifest = _MANIFEST.read_text(encoding="utf-8")

        self.assertEqual(manifest.count("basic-pitch@sha256:c0455e52c2e484900b16ef3289e264591d23efc4cfca9533d6f0ac29fc79e4ca"), 2)
        self.assertIn("k3d-clouddsp-local-agent-0", manifest)
        self.assertIn("k3d-clouddsp-local-agent-1", manifest)
        self.assertIn("imagePullPolicy: IfNotPresent", manifest)
        self.assertIn("cached-container-instantiation-test", manifest)
        self.assertIn("uncached-container-instantiation-test", manifest)

    def test_overrides_the_worker_entrypoint_and_never_mounts_runtime_credentials(self) -> None:
        """A timing Job must not connect to RabbitMQ or consume durable work."""

        manifest = _MANIFEST.read_text(encoding="utf-8")

        self.assertEqual(manifest.count('command: ["python", "-c"]'), 2)
        self.assertNotIn("secretKeyRef:", manifest)
        self.assertNotIn("BASIC_PITCH_AMQP_", manifest)
        self.assertNotIn("RABBITMQ_BASIC_PITCH", manifest)
        self.assertNotIn("clouddsp.basic-pitch.requests", manifest)
        self.assertIn("automountServiceAccountToken: false", manifest)
        self.assertIn("readOnlyRootFilesystem: true", manifest)


if __name__ == "__main__":
    unittest.main()
