"""Structural tests for the prepared namespaced KEDA RabbitMQ credential binding.

The tests read committed YAML text only. They do not query a Kubernetes API,
read a Secret, invoke KEDA, authenticate to RabbitMQ, create an HPA, or change
a Deployment's replica count.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2]
    / "helm"
    / "keda"
    / "keda-rabbitmq-scaler-trigger-authentication.yaml"
)


class KedaRabbitMQTriggerAuthenticationManifestTests(unittest.TestCase):
    """Keep queue-observer credential binding private and namespaced."""

    def manifest(self) -> str:
        """Read local source only; parsing/applying is outside this test boundary."""

        return _MANIFEST_PATH.read_text(encoding="utf-8")

    def test_is_one_namespaced_keda_trigger_authentication(self) -> None:
        """A ClusterTriggerAuthentication would unnecessarily widen Secret scope."""

        manifest = self.manifest()
        self.assertIn("apiVersion: keda.sh/v1alpha1\nkind: TriggerAuthentication", manifest)
        self.assertIn("name: clouddsp-rabbitmq-scaler-authentication", manifest)
        self.assertIn("namespace: clouddsp-app", manifest)
        self.assertNotIn("kind: ClusterTriggerAuthentication", manifest)
        self.assertNotIn("kind: ScaledObject", manifest)
        self.assertNotIn("kind: Deployment", manifest)

    def test_binds_only_the_dedicated_observer_secret_username_and_password(self) -> None:
        """KEDA must never receive a broker admin or worker consumer credential."""

        manifest = self.manifest()
        self.assertIn("secretTargetRef:", manifest)
        self.assertIn("parameter: username", manifest)
        self.assertIn("parameter: password", manifest)
        self.assertIn("name: clouddsp-keda-rabbitmq-scaler-credentials", manifest)
        self.assertIn("key: RABBITMQ_KEDA_SCALER_USERNAME", manifest)
        self.assertIn("key: RABBITMQ_KEDA_SCALER_PASSWORD", manifest)
        self.assertNotIn("clouddsp-rabbitmq-credentials", manifest)
        self.assertNotIn("RABBITMQ_DEFAULT_PASS", manifest)
        self.assertNotIn("clouddsp-demucs-rabbitmq-credentials", manifest)
        self.assertNotIn("clouddsp-basic-pitch-rabbitmq-credentials", manifest)
        self.assertNotIn("clouddsp-adtof-rabbitmq-credentials", manifest)

    def test_does_not_hide_a_queue_endpoint_or_scaling_policy_in_credentials(self) -> None:
        """Queue selection and thresholds belong to each later ScaledObject."""

        manifest = self.manifest()
        self.assertNotIn("queueName:", manifest)
        self.assertNotIn("activationValue:", manifest)
        self.assertNotIn("minReplicaCount:", manifest)
        self.assertNotIn("maxReplicaCount:", manifest)
        self.assertNotIn("host:", manifest)
        self.assertNotIn("clouddsp-rabbitmq.clouddsp-data.svc", manifest)


if __name__ == "__main__":
    unittest.main()
