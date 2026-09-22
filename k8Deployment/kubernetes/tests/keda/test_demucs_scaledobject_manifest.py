"""Structural guardrails for Demucs's conservative local KEDA policy.

These tests read committed YAML only. They do not contact KEDA or RabbitMQ,
read a Secret, create an HPA, change a Deployment replica count, or start a
CPU-heavy Demucs Pod. A later explicit live task will apply this resource and
observe a controlled queued-work transition.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_KUBERNETES_DIRECTORY = Path(__file__).resolve().parents[2]
_DEMUCS_DIRECTORY = _KUBERNETES_DIRECTORY / "services" / "demucs"
_SCALED_OBJECT = _DEMUCS_DIRECTORY / "demucs-scaledobject.yaml"
_DEPLOYMENT = _DEMUCS_DIRECTORY / "demucs-deployment.yaml"


class DemucsScaledObjectManifestTests(unittest.TestCase):
    """Keep local CPU Demucs scaling private, bounded, and stage-specific."""

    def test_targets_only_the_demucs_deployment_in_the_app_namespace(self) -> None:
        """The scaler must adjust one controller, never a Pod or another stage."""

        manifest = _SCALED_OBJECT.read_text(encoding="utf-8")

        self.assertIn("apiVersion: keda.sh/v1alpha1\nkind: ScaledObject", manifest)
        self.assertIn("name: clouddsp-demucs-rabbitmq-scaler", manifest)
        self.assertIn("namespace: clouddsp-app", manifest)
        self.assertIn("apiVersion: apps/v1", manifest)
        self.assertIn("kind: Deployment", manifest)
        self.assertIn("name: clouddsp-demucs", manifest)
        self.assertNotIn("kind: ScaledJob", manifest)
        self.assertNotIn("kind: ClusterTriggerAuthentication", manifest)

    def test_observes_only_the_private_demucs_queue_with_monitoring_authentication(self) -> None:
        """KEDA reads management metrics; it never receives an AMQP worker key."""

        manifest = _SCALED_OBJECT.read_text(encoding="utf-8")

        for required in (
            "type: rabbitmq",
            "protocol: http",
            "host: http://clouddsp-rabbitmq-management.clouddsp-data.svc:15672",
            "vhostName: /clouddsp",
            "queueName: clouddsp.demucs.requests",
            "mode: QueueLength",
            "name: clouddsp-rabbitmq-scaler-authentication",
        ):
            self.assertIn(required, manifest)
        self.assertNotIn("amqp://", manifest)
        self.assertNotIn("RABBITMQ_DEMUCS_PASSWORD", manifest)
        self.assertNotIn("secretKeyRef:", manifest)

    def test_scale_to_zero_is_limited_to_one_local_cpu_worker(self) -> None:
        """One Pod protects the laptop while prefetched work remains visible."""

        manifest = _SCALED_OBJECT.read_text(encoding="utf-8")
        deployment = _DEPLOYMENT.read_text(encoding="utf-8")

        for required in (
            "pollingInterval: 15",
            "cooldownPeriod: 300",
            "minReplicaCount: 0",
            "maxReplicaCount: 1",
            'value: "1"',
            'activationValue: "0"',
            'excludeUnacknowledged: "false"',
            'timeout: "5000"',
            "restoreToOriginalReplicaCount: true",
            "periodSeconds: 15",
            "periodSeconds: 60",
        ):
            self.assertIn(required, manifest)
        self.assertNotIn("\n  replicas:", deployment)
        self.assertIn("Demucs KEDA", deployment)


if __name__ == "__main__":
    unittest.main()
