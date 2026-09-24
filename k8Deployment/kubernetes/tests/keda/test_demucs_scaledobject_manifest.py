"""Structural guardrails for Demucs's durable-work local KEDA policy.

These tests read committed YAML only. They do not contact KEDA or RabbitMQ,
read a Secret, create an HPA, change a Deployment replica count, or start a
CPU-heavy Demucs Pod. Live verification separately confirms the task metric.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_KUBERNETES_DIRECTORY = Path(__file__).resolve().parents[2]
_DEMUCS_DIRECTORY = _KUBERNETES_DIRECTORY / "services" / "demucs"
_SCALED_OBJECT = _DEMUCS_DIRECTORY / "demucs-scaledobject.yaml"
_DEPLOYMENT = _DEMUCS_DIRECTORY / "demucs-deployment.yaml"
_POSTGRESQL_BOOTSTRAP = (
    _KUBERNETES_DIRECTORY
    / "services"
    / "postgresql"
    / "postgresql-keda-demucs-bootstrap-job.yaml"
)


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
        """One Pod protects the laptop even if several tasks are due."""

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

    def test_database_trigger_keeps_acknowledged_work_visible(self) -> None:
        """The queue can be empty while a durable lease still owns CPU work."""

        manifest = _SCALED_OBJECT.read_text(encoding="utf-8")

        for required in (
            "type: postgresql",
            "host: clouddsp-postgresql.clouddsp-data.svc",
            "dbName: clouddsp_job_api",
            "userName: clouddsp-keda-demucs",
            "FROM public.processing_tasks",
            "stage = 'demucs'",
            "status IN ('leased', 'running')",
            "status = 'retry_scheduled' AND available_at <= CURRENT_TIMESTAMP",
            'targetQueryValue: "1"',
            'activationTargetQueryValue: "0"',
            "name: clouddsp-demucs-postgresql-scaler-authentication",
        ):
            self.assertIn(required, manifest)

        # A future retry must not keep the Pod warm until it actually becomes
        # due, and a terminal task must not hold an idle Pod forever.
        self.assertNotIn("status = 'completed'", manifest)
        self.assertNotIn("status = 'failed'", manifest)

    def test_database_observer_cannot_modify_or_read_private_jobs(self) -> None:
        """The scale metric requires three task columns, not worker rights."""

        bootstrap = _POSTGRESQL_BOOTSTRAP.read_text(encoding="utf-8")

        self.assertIn(
            "GRANT SELECT (stage, status, available_at)", bootstrap
        )
        self.assertIn("NOT has_column_privilege('clouddsp-keda-demucs', 'public.processing_tasks', 'job_id', 'SELECT')", bootstrap)
        self.assertIn("NOT has_table_privilege('clouddsp-keda-demucs', 'public.jobs', 'SELECT')", bootstrap)
        self.assertIn("NOT has_table_privilege('clouddsp-keda-demucs', 'public.processing_tasks', 'UPDATE')", bootstrap)


if __name__ == "__main__":
    unittest.main()
