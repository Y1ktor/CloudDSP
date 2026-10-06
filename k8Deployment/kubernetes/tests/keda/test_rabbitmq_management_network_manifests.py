"""Structural guardrails for RabbitMQ's private KEDA management endpoint.

These tests read source files only. They do not query Kubernetes, connect to
RabbitMQ, read a live Secret, start a Pod, create a Service, or enforce a
NetworkPolicy. A later explicit live smoke task must prove the policy blocks an
unauthorized Pod after the user applies these manifests.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_KUBERNETES_DIRECTORY = Path(__file__).resolve().parents[2]
_RABBITMQ_DIRECTORY = _KUBERNETES_DIRECTORY / "services" / "rabbitmq"
_MANAGEMENT_SERVICE = _RABBITMQ_DIRECTORY / "rabbitmq-management-service.yaml"
_INGRESS_POLICY = _RABBITMQ_DIRECTORY / "rabbitmq-ingress-network-policy.yaml"


class RabbitMQManagementNetworkManifestTests(unittest.TestCase):
    """Keep queue observation private without disrupting reviewed AMQP flow."""

    def test_management_service_is_internal_and_selects_only_rabbitmq(self) -> None:
        """The scaler needs one stable HTTP endpoint, never a host-facing port."""

        manifest = _MANAGEMENT_SERVICE.read_text(encoding="utf-8")

        self.assertIn("apiVersion: v1\nkind: Service", manifest)
        self.assertIn("name: clouddsp-rabbitmq-management", manifest)
        self.assertIn("namespace: clouddsp-data", manifest)
        self.assertIn("type: ClusterIP", manifest)
        self.assertIn("port: 15672", manifest)
        self.assertIn("targetPort: management", manifest)
        self.assertIn("app.kubernetes.io/name: rabbitmq", manifest)
        self.assertIn("app.kubernetes.io/instance: clouddsp-rabbitmq", manifest)
        self.assertNotIn("type: NodePort", manifest)
        self.assertNotIn("type: LoadBalancer", manifest)
        self.assertNotIn("kind: Ingress", manifest)

    def test_policy_isolates_broker_ingress_and_allows_keda_only_on_management(self) -> None:
        """An internal Service is insufficient without endpoint-level isolation."""

        manifest = _INGRESS_POLICY.read_text(encoding="utf-8")

        self.assertIn("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy", manifest)
        self.assertIn("name: clouddsp-rabbitmq-ingress", manifest)
        self.assertIn("namespace: clouddsp-data", manifest)
        self.assertIn("policyTypes:\n    - Ingress", manifest)
        # Four existing source groups deliberately occupy separate ingress
        # items: KEDA, finite bootstrap Jobs, and the two named integration
        # probes. The older count of two predates those reviewed smoke paths.
        self.assertEqual(manifest.count("port: 15672"), 4)
        self.assertIn("app.kubernetes.io/name: source-to-outbox-smoke", manifest)
        self.assertIn("app.kubernetes.io/name: six-stem-load", manifest)
        self.assertIn("kubernetes.io/metadata.name: keda", manifest)
        self.assertIn("app.kubernetes.io/name: keda-operator", manifest)
        self.assertNotIn("port: 15672\n      from: []", manifest)

    def test_policy_retains_only_reviewed_amqp_clients_and_broker_peers(self) -> None:
        """The policy must not break the durable pipeline while closing broad access."""

        manifest = _INGRESS_POLICY.read_text(encoding="utf-8")

        self.assertIn("port: 5672", manifest)
        for component in (
            "source-event-consumer",
            "outbox-publisher",
            "generic-outbox-publisher",
            "audio-separation-worker",
            "midi-extraction-worker",
            "drum-midi-worker",
            "object-storage",
            "integration-test",
        ):
            self.assertIn(component, manifest)
        self.assertIn("port: 4369", manifest)
        self.assertIn("port: 25672", manifest)
        self.assertNotIn("namespaceSelector: {}", manifest)
        self.assertNotIn("podSelector: {}", manifest)


if __name__ == "__main__":
    unittest.main()
