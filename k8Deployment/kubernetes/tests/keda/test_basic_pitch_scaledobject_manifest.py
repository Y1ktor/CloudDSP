"""Structural guardrails for the Basic Pitch queue-driven KEDA policy.

These tests inspect versioned source only. They do not query the Kubernetes
API, read a Secret, create an HPA, alter Basic Pitch replicas, or start a
TensorFlow worker Pod. A later explicit live task applies the ScaledObject and
observes a controlled queue transition.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_KUBERNETES_DIRECTORY = Path(__file__).resolve().parents[2]
_BASIC_PITCH_DIRECTORY = _KUBERNETES_DIRECTORY / "services" / "basic-pitch"
_SCALED_OBJECT = _BASIC_PITCH_DIRECTORY / "basic-pitch-scaledobject.yaml"
_DEPLOYMENT = _BASIC_PITCH_DIRECTORY / "basic-pitch-deployment.yaml"


class BasicPitchScaledObjectManifestTests(unittest.TestCase):
    """Keep scaling bounded, private, and separate from worker authority."""

    def test_targets_only_the_basic_pitch_deployment_in_the_app_namespace(self) -> None:
        """A stage scaler must not target another worker or a one-off Pod."""

        manifest = _SCALED_OBJECT.read_text(encoding="utf-8")

        self.assertIn("apiVersion: keda.sh/v1alpha1\nkind: ScaledObject", manifest)
        self.assertIn("name: clouddsp-basic-pitch-rabbitmq-scaler", manifest)
        self.assertIn("namespace: clouddsp-app", manifest)
        self.assertIn("apiVersion: apps/v1", manifest)
        self.assertIn("kind: Deployment", manifest)
        self.assertIn("name: clouddsp-basic-pitch", manifest)
        self.assertNotIn("kind: ScaledJob", manifest)
        self.assertNotIn("kind: ClusterTriggerAuthentication", manifest)

    def test_observes_only_the_private_basic_pitch_queue_with_the_observer_binding(self) -> None:
        """KEDA must observe management metrics rather than consume AMQP work."""

        manifest = _SCALED_OBJECT.read_text(encoding="utf-8")

        self.assertIn("type: rabbitmq", manifest)
        self.assertIn("protocol: http", manifest)
        self.assertIn("host: http://clouddsp-rabbitmq-management.clouddsp-data.svc:15672", manifest)
        self.assertIn("vhostName: /clouddsp", manifest)
        self.assertIn("queueName: clouddsp.basic-pitch.requests", manifest)
        self.assertIn("mode: QueueLength", manifest)
        self.assertIn("name: clouddsp-rabbitmq-scaler-authentication", manifest)
        self.assertNotIn("amqp://", manifest)
        self.assertNotIn("RABBITMQ_BASIC_PITCH_PASSWORD", manifest)
        self.assertNotIn("secretKeyRef:", manifest)

    def test_scale_to_zero_and_capacity_limits_follow_basic_pitch_flow_control(self) -> None:
        """Prefetch one and bounded CPU requests support a three-Pod burst cap."""

        manifest = _SCALED_OBJECT.read_text(encoding="utf-8")
        deployment = _DEPLOYMENT.read_text(encoding="utf-8")

        for required in (
            "pollingInterval: 5",
            "cooldownPeriod: 60",
            "minReplicaCount: 0",
            "maxReplicaCount: 3",
            'value: "1"',
            'activationValue: "0"',
            'excludeUnacknowledged: "false"',
            'timeout: "5000"',
            "restoreToOriginalReplicaCount: true",
            "value: 3",
            "periodSeconds: 15",
            "periodSeconds: 60",
        ):
            self.assertIn(required, manifest)
        self.assertNotIn("\n  replicas:", deployment)
        self.assertIn("KEDA ScaledObject", deployment)


if __name__ == "__main__":
    unittest.main()
