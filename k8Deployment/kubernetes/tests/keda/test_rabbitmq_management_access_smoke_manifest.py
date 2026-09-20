"""Structural checks for the controlled RabbitMQ management access probes.

The tests inspect source only. They never create probe Jobs, open a socket,
read a credential, or contact the Kubernetes API. The paired live test is an
explicit user-authorized operation because it creates two short-lived Pods.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_MANIFEST = Path(__file__).resolve().with_name(
    "rabbitmq-management-access-smoke-jobs.yaml"
)


class RabbitMQManagementAccessSmokeManifestTests(unittest.TestCase):
    """Keep policy proof limited to TCP reachability and a fixed source identity."""

    def manifest(self) -> str:
        """Read the versioned manifest; YAML parsing/applying is outside this test."""

        return _MANIFEST.read_text(encoding="utf-8")

    def test_declares_one_allowed_and_one_denied_finite_job(self) -> None:
        """Both directions are needed to prove a policy is selective, not merely live."""

        manifest = self.manifest()

        self.assertEqual(manifest.count("apiVersion: batch/v1\nkind: Job"), 2)
        self.assertIn("name: rabbitmq-management-allowed-smoke", manifest)
        self.assertIn("name: rabbitmq-management-denied-smoke", manifest)
        self.assertIn("namespace: clouddsp-data", manifest)
        self.assertIn("namespace: clouddsp-app", manifest)
        self.assertEqual(manifest.count("ttlSecondsAfterFinished: 300"), 2)
        self.assertEqual(manifest.count("backoffLimit: 0"), 2)
        self.assertEqual(manifest.count("automountServiceAccountToken: false"), 2)

    def test_source_labels_match_the_intended_policy_decision(self) -> None:
        """The allowed test must prove an exception while the denied test cannot match one."""

        manifest = self.manifest()

        self.assertEqual(manifest.count("app.kubernetes.io/component: message-identity-bootstrap"), 2)
        self.assertEqual(manifest.count("app.kubernetes.io/component: network-policy-probe"), 2)
        self.assertNotIn("app.kubernetes.io/name: keda-operator", manifest)
        self.assertNotIn("app.kubernetes.io/component: integration-test", manifest)

    def test_probes_use_the_locked_local_client_without_broker_authority(self) -> None:
        """A TCP policy check needs no management request, Secret, or AMQP action."""

        manifest = self.manifest()

        self.assertEqual(
            manifest.count(
                "clouddsp-registry.localhost:5001/rabbitmq-amqp-smoke-client@"
                "sha256:4f4564fca1c8080688396e9b7f1457def92e720c1826083189eccff38629c4d2"
            ),
            2,
        )
        self.assertEqual(manifest.count("clouddsp-rabbitmq-management.clouddsp-data.svc"), 2)
        self.assertEqual(manifest.count("port = 15672"), 2)
        self.assertNotIn("secretKeyRef:", manifest)
        self.assertNotIn("rabbitmqadmin", manifest)
        self.assertNotIn("pika", manifest)


if __name__ == "__main__":
    unittest.main()
