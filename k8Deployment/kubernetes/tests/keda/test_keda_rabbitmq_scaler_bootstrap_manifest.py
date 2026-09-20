"""Structural guardrails for the prepared KEDA RabbitMQ observer bootstrap.

These tests inspect committed templates and manifest text only. They never
contact Kubernetes, RabbitMQ, Helm, a live Secret, or a queue; they create no
broker user, KEDA resource, Pod, HPA, or processing workload.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_KUBERNETES_DIRECTORY = Path(__file__).resolve().parents[2]
_HELM_KEDA_DIRECTORY = _KUBERNETES_DIRECTORY / "helm" / "keda"
_RABBITMQ_DIRECTORY = _KUBERNETES_DIRECTORY / "services" / "rabbitmq"
_RUNTIME_TEMPLATE = _HELM_KEDA_DIRECTORY / "keda-rabbitmq-scaler-credentials.secret.example.yaml"
_BOOTSTRAP_TEMPLATE = _RABBITMQ_DIRECTORY / "rabbitmq-keda-scaler-bootstrap-credentials.secret.example.yaml"
_BOOTSTRAP_JOB = _RABBITMQ_DIRECTORY / "rabbitmq-keda-scaler-bootstrap-job.yaml"


class KedaRabbitMQScalerBootstrapManifestTests(unittest.TestCase):
    """Prevent KEDA queue observation from becoming worker or broker authority."""

    def test_secrets_are_namespace_scoped_templates_for_one_identity(self) -> None:
        """The runtime and bootstrap copies are required by namespace isolation."""

        runtime = _RUNTIME_TEMPLATE.read_text(encoding="utf-8")
        bootstrap = _BOOTSTRAP_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("name: clouddsp-keda-rabbitmq-scaler-credentials", runtime)
        self.assertIn("namespace: clouddsp-app", runtime)
        self.assertIn("name: clouddsp-keda-rabbitmq-scaler-bootstrap-credentials", bootstrap)
        self.assertIn("namespace: clouddsp-data", bootstrap)
        for source in (runtime, bootstrap):
            self.assertIn("RABBITMQ_KEDA_SCALER_USERNAME: clouddsp-keda-scaler", source)
            self.assertIn("RABBITMQ_KEDA_SCALER_PASSWORD: REPLACE_WITH_", source)
            self.assertNotIn("RABBITMQ_DEFAULT_PASS:", source)

    def test_bootstrap_creates_only_a_monitoring_identity_with_no_resource_access(self) -> None:
        """Queue observation must never grant consumer, publisher, or topology rights."""

        manifest = _BOOTSTRAP_JOB.read_text(encoding="utf-8")
        self.assertIn("apiVersion: batch/v1\nkind: Job", manifest)
        self.assertIn("name: rabbitmq-keda-scaler-bootstrap", manifest)
        self.assertIn("namespace: clouddsp-data", manifest)
        self.assertIn("--tags monitoring", manifest)
        self.assertIn("--configure '^$'", manifest)
        self.assertIn("--write '^$'", manifest)
        self.assertIn("--read '^$'", manifest)
        self.assertIn("rabbitmqadmin --quiet queues list --vhost /clouddsp >/dev/null", manifest)
        self.assertNotIn("--tags administrator", manifest)
        self.assertNotIn("--tags policymaker", manifest)
        self.assertNotIn("clouddsp.demucs.requests", manifest)
        self.assertNotIn("clouddsp.basic-pitch.requests", manifest)
        self.assertNotIn("clouddsp.adtof.requests", manifest)

    def test_bootstrap_is_bounded_and_has_no_application_or_kubernetes_authority(self) -> None:
        """The administrator-bearing process stays finite and outside data flow."""

        manifest = _BOOTSTRAP_JOB.read_text(encoding="utf-8")
        for required_field in (
            "ttlSecondsAfterFinished: 600",
            "backoffLimit: 0",
            "activeDeadlineSeconds: 180",
            "restartPolicy: Never",
            "automountServiceAccountToken: false",
            "runAsUser: 999",
            "readOnlyRootFilesystem: true",
            "sizeLimit: 1Mi",
            "RABBITMQADMIN_TARGET_PORT\n              value: \"15672\"",
        ):
            self.assertIn(required_field, manifest)
        self.assertNotIn("kind: Deployment", manifest)
        # The conventional managed-by label legitimately contains `kubectl`.
        # Reject only an executable shell invocation, which would give this
        # broker bootstrap Pod an inappropriate Kubernetes API responsibility.
        self.assertNotIn("\n              kubectl", manifest)
        self.assertNotIn("POSTGRES_", manifest)
        self.assertNotIn("MINIO_", manifest)


if __name__ == "__main__":
    unittest.main()
