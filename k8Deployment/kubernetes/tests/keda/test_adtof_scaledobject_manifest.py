"""Structural guardrails for the first queue-driven ADTOF KEDA policy.

These tests inspect committed source only. They do not contact KEDA or
RabbitMQ, read a Secret, create an HPA, alter ADTOF replicas, or start a worker
Pod. The later explicit live test will apply the ScaledObject and observe its
generated HPA plus a controlled queue transition.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_KUBERNETES_DIRECTORY = Path(__file__).resolve().parents[2]
_ADTOF_DIRECTORY = _KUBERNETES_DIRECTORY / "services" / "adtof"
_SCALED_OBJECT = _ADTOF_DIRECTORY / "adtof-scaledobject.yaml"
_DEPLOYMENT = _ADTOF_DIRECTORY / "adtof-deployment.yaml"


class ADTOFScaledObjectManifestTests(unittest.TestCase):
    """Keep queue scaling private, bounded, and separate from worker authority."""

    def test_targets_only_the_adtof_deployment_in_the_app_namespace(self) -> None:
        """A stage scaler must never target another worker or a one-off Pod."""

        manifest = _SCALED_OBJECT.read_text(encoding="utf-8")

        self.assertIn("apiVersion: keda.sh/v1alpha1\nkind: ScaledObject", manifest)
        self.assertIn("name: clouddsp-adtof-rabbitmq-scaler", manifest)
        self.assertIn("namespace: clouddsp-app", manifest)
        self.assertIn("apiVersion: apps/v1", manifest)
        self.assertIn("kind: Deployment", manifest)
        self.assertIn("name: clouddsp-adtof", manifest)
        self.assertNotIn("kind: ScaledJob", manifest)
        self.assertNotIn("kind: ClusterTriggerAuthentication", manifest)

    def test_uses_the_private_http_queue_metric_and_restricted_observer_binding(self) -> None:
        """KEDA observes management metrics; it must never become an AMQP worker."""

        manifest = _SCALED_OBJECT.read_text(encoding="utf-8")

        self.assertIn("type: rabbitmq", manifest)
        self.assertIn("protocol: http", manifest)
        self.assertIn("host: http://clouddsp-rabbitmq-management.clouddsp-data.svc:15672", manifest)
        self.assertIn("vhostName: /clouddsp", manifest)
        self.assertIn("queueName: clouddsp.adtof.requests", manifest)
        self.assertIn("mode: QueueLength", manifest)
        self.assertIn("name: clouddsp-rabbitmq-scaler-authentication", manifest)
        self.assertNotIn("amqp://", manifest)
        self.assertNotIn("RABBITMQ_ADTOF_PASSWORD", manifest)
        self.assertNotIn("secretKeyRef:", manifest)

    def test_scale_to_zero_and_local_capacity_limits_match_worker_flow_control(self) -> None:
        """One task per Pod and two maximum Pods protect CPU/memory capacity."""

        manifest = _SCALED_OBJECT.read_text(encoding="utf-8")
        deployment = _DEPLOYMENT.read_text(encoding="utf-8")

        for required in (
            "pollingInterval: 15",
            "cooldownPeriod: 180",
            "minReplicaCount: 0",
            "maxReplicaCount: 2",
            "value: \"1\"",
            "activationValue: \"0\"",
            "excludeUnacknowledged: \"false\"",
            "timeout: \"5000\"",
            "restoreToOriginalReplicaCount: true",
            "periodSeconds: 30",
            "periodSeconds: 60",
        ):
            self.assertIn(required, manifest)
        self.assertNotIn("\n  replicas:", deployment)
        self.assertIn("KEDA ScaledObject", deployment)


if __name__ == "__main__":
    unittest.main()
