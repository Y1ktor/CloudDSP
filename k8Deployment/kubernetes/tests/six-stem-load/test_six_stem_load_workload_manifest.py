"""Source-only guardrails for the suspended, fully wired six-stem load Job."""

from __future__ import annotations

from pathlib import Path
import unittest


_DIRECTORY = Path(__file__).resolve().parent
_JOB = _DIRECTORY / "six-stem-load-job.yaml"
_SECRET_TEMPLATE = _DIRECTORY / "six-stem-load-keycloak-bootstrap-credentials.secret.example.yaml"
_MINIO_SECRET_TEMPLATE = _DIRECTORY / "six-stem-load-minio-admin-credentials.secret.example.yaml"
_RBAC = _DIRECTORY / "six-stem-load-observer-rbac.yaml"
_NETWORK_POLICY = _DIRECTORY.parents[1] / "services/rabbitmq/rabbitmq-ingress-network-policy.yaml"
_IMAGE_REFERENCE = (
    "clouddsp-registry.localhost:5001/six-stem-load-client@"
    "sha256:96b002198c81cf3f9c436a0849e05d2df0336fc647196a7a4d7a83f6a60b9edf"
)


class SixStemLoadWorkloadManifestTests(unittest.TestCase):
    """Keep the completed workload suspended, isolated, and least-privileged."""

    def test_complete_job_is_suspended_and_wires_all_three_processes(self) -> None:
        """The Job cannot run until explicitly resumed, but its runtime is complete."""

        source = _JOB.read_text(encoding="utf-8")
        for required in (
            "apiVersion: batch/v1\nkind: Job",
            "name: clouddsp-six-stem-load",
            "namespace: clouddsp-app",
            "suspend: true",
            "backoffLimit: 0",
            "activeDeadlineSeconds: 3600",
            "automountServiceAccountToken: false",
            "enableServiceLinks: false",
            "serviceAccountName: clouddsp-six-stem-load-observer",
            "runAsNonRoot: true",
            "runAsUser: 10012",
            "initContainers:",
            "name: secure-private-volumes",
            "/app/private_volume_setup.py",
            "readOnlyRootFilesystem: true",
            "name: keycloak-lifecycle-broker",
            "name: authenticated-load-client",
            "name: six-stem-load-observer",
            "command:\n            - python\n            - /app/authenticated_load_client.py",
            "command:\n            - python\n            - /app/six_stem_load_observer.py",
            "JOB_API_INTERNAL_BASE_URL",
            "http://clouddsp-job-api.clouddsp-app.svc:80",
            "SIX_STEM_LOAD_OBSERVER_REPORT_DIR",
            "/var/run/clouddsp-six-stem-observer-reports",
            "name: SIX_STEM_POSTGRESQL_ADMIN_DATABASE",
            "value: clouddsp_job_api",
            "name: load-test-observer-reports",
            "name: observer-kubernetes-api-credentials",
            "clouddsp-six-stem-load-minio-admin-credentials",
            "clouddsp-keda-rabbitmq-scaler-credentials",
        ):
            self.assertIn(required, source)
        # The same reviewed bytes run one credential-free init container and
        # the three ordinary containers. No mutable tag is used in the Pod.
        self.assertEqual(source.count(_IMAGE_REFERENCE), 4)
        init_source = source.split("initContainers:", 1)[1].split("\n      containers:", 1)[0]
        self.assertIn("runAsUser: 0", init_source)
        self.assertIn("runAsNonRoot: false", init_source)
        self.assertEqual(init_source.count("mountPath: /var/run/clouddsp-six-stem-"), 5)
        self.assertNotIn("secretKeyRef:", init_source)
        self.assertNotIn("observer-kubernetes-api-credentials", init_source)
        self.assertNotIn("kind: Deployment", source)
        self.assertNotIn("hostPath:", source)
        self.assertNotIn("ports:", source)

    def test_admin_secrets_are_broker_only_and_data_credentials_are_read_only(self) -> None:
        """Only the broker receives Keycloak/PostgreSQL/MinIO administrator values."""

        source = _JOB.read_text(encoding="utf-8")
        self.assertEqual(source.count("clouddsp-six-stem-load-keycloak-bootstrap-credentials"), 2)
        self.assertIn("name: keycloak-lifecycle-broker", source)
        # Administrator environment entries occur only in the first container.
        client_source = source.split("- name: authenticated-load-client", 1)[1].split(
            "- name: six-stem-load-observer", 1
        )[0]
        observer_source = source.split("- name: six-stem-load-observer", 1)[1]
        for broker_only in ("KC_BOOTSTRAP_ADMIN_", "SIX_STEM_POSTGRESQL_ADMIN_", "MINIO_ROOT_"):
            self.assertNotIn(broker_only, client_source)
            self.assertNotIn(broker_only, observer_source)
        self.assertIn("name: SIX_STEM_POSTGRESQL_ADMIN_USERNAME", source)
        self.assertIn("name: SIX_STEM_POSTGRESQL_ADMIN_PASSWORD", source)
        self.assertEqual(
            source.count("clouddsp-six-stem-load-postgresql-bootstrap-credentials"),
            2,
        )
        self.assertEqual(source.count("clouddsp-six-stem-load-minio-admin-credentials"), 2)
        self.assertIn("mountPath: /var/run/clouddsp-six-stem-minio-observer\n              readOnly: true", source)
        self.assertIn("mountPath: /var/run/clouddsp-six-stem-postgresql-observer\n              readOnly: true", source)
        self.assertIn("name: temporary-load-identity-handoff", source)
        self.assertIn("medium: Memory", source)
        self.assertIn("sizeLimit: 1Mi", source)
        self.assertNotIn("AWS_", source)
        self.assertNotIn("KUBECONFIG", source)

    def test_secret_template_is_local_only_and_contains_only_admin_api_values(self) -> None:
        """The committed example documents keys but includes no usable credential."""

        source = _SECRET_TEMPLATE.read_text(encoding="utf-8")
        for required in (
            "name: clouddsp-six-stem-load-keycloak-bootstrap-credentials",
            "namespace: clouddsp-app",
            "KC_BOOTSTRAP_ADMIN_USERNAME: REPLACE_WITH_",
            "KC_BOOTSTRAP_ADMIN_PASSWORD: REPLACE_WITH_",
            "k8Deployment/.local/six-stem-load-keycloak-bootstrap-credentials.secret.yaml",
        ):
            self.assertIn(required, source)
        for forbidden in ("POSTGRES", "MINIO", "RABBITMQ", "AWS_ACCESS_KEY", "clientSecret"):
            self.assertNotIn(forbidden, source)

    def test_minio_root_secret_example_contains_only_placeholders(self) -> None:
        """The broker's namespace-local MinIO credential copy is not committed live data."""

        source = _MINIO_SECRET_TEMPLATE.read_text(encoding="utf-8")
        for required in (
            "name: clouddsp-six-stem-load-minio-admin-credentials",
            "namespace: clouddsp-app",
            "MINIO_ROOT_USER: REPLACE_WITH_",
            "MINIO_ROOT_PASSWORD: REPLACE_WITH_",
            "k8Deployment/.local/six-stem-load-minio-admin-credentials.secret.yaml",
        ):
            self.assertIn(required, source)

    def test_observer_rbac_is_namespaced_get_only_and_resource_name_bounded(self) -> None:
        """The projected token cannot list, watch, mutate, or scale workloads."""

        source = _RBAC.read_text(encoding="utf-8")
        self.assertEqual(source.count("verbs:\n      - get"), 3)
        self.assertEqual(source.count("resourceNames:"), 3)
        for worker in ("clouddsp-demucs", "clouddsp-basic-pitch", "clouddsp-adtof"):
            self.assertIn(worker, source)
        for forbidden in (
            "      - list",
            "      - watch",
            "      - patch",
            "      - update",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("kind: Role\nmetadata:", source)
        self.assertNotIn("kind: ClusterRole", source)

    def test_rabbitmq_management_exception_selects_only_this_test_pod(self) -> None:
        """The queue observer gets only the management listener needed for metrics."""

        source = _NETWORK_POLICY.read_text(encoding="utf-8")
        exception = source.split("The finite six-stem integration Job", 1)[1].split(
            "# AMQP is the data-plane listener", 1
        )[0]
        self.assertIn("port: 15672", exception)
        self.assertIn("kubernetes.io/metadata.name: clouddsp-app", exception)
        self.assertIn("app.kubernetes.io/name: six-stem-load", exception)
        self.assertIn("app.kubernetes.io/component: integration-test", exception)


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
