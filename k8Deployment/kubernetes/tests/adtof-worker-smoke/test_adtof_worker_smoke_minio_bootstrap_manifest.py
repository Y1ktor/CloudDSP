"""Structural guardrails for the fixed-key ADTOF smoke MinIO identity manifest.

These tests inspect version-controlled source only. They do not contact MinIO,
Kubernetes, PostgreSQL, RabbitMQ, or the ADTOF model; they create neither an
identity nor an object. A later explicit operator action applies the reviewed
ConfigMap, local Secrets, and bootstrap Job.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest


_DIRECTORY = Path(__file__).resolve().parent
_POLICY_MANIFEST = _DIRECTORY / "adtof-worker-smoke-minio-policy-v001-configmap.yaml"
_BOOTSTRAP_JOB = _DIRECTORY / "minio-adtof-worker-smoke-objects-bootstrap-job.yaml"
_BOOTSTRAP_TEMPLATE = _DIRECTORY / "adtof-worker-smoke-minio-bootstrap-credentials.secret.example.yaml"
_RUNTIME_TEMPLATE = _DIRECTORY / "adtof-worker-smoke-minio-credentials.secret.example.yaml"


def _policy_document() -> dict[str, object]:
    """Extract the literal JSON body without adding a YAML-parser dependency."""

    source = _POLICY_MANIFEST.read_text(encoding="utf-8")
    marker = "  adtof-worker-smoke-objects-policy.json: |\n"
    return json.loads(source.split(marker, maxsplit=1)[1])


class ADTOFWorkerSmokeMinIOBootstrapManifestTests(unittest.TestCase):
    """Keep the future smoke client limited to its three reserved object keys."""

    def test_policy_is_exactly_scoped_to_one_input_and_two_read_only_outputs(self) -> None:
        """The client must not list, write outputs, or access any sibling path."""

        document = _policy_document()
        self.assertEqual(document["Version"], "2012-10-17")
        statements = document["Statement"]
        self.assertIsInstance(statements, list)
        self.assertEqual(len(statements), 2)

        input_statement, output_statement = statements
        self.assertEqual(
            input_statement,
            {
                "Sid": "WriteReadDeleteOnlyTheFixedControlledDrumsInput",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": "arn:aws:s3:::clouddsp-uploads/stems/8d94ca4d-8f27-4d78-a01f-73ee0fb8c6bc/drums.wav",
            },
        )
        self.assertEqual(
            output_statement,
            {
                "Sid": "ReadDeleteOnlyTheFixedADTOFOutputs",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:DeleteObject"],
                "Resource": [
                    "arn:aws:s3:::clouddsp-uploads/midi/8d94ca4d-8f27-4d78-a01f-73ee0fb8c6bc/drums.mid",
                    "arn:aws:s3:::clouddsp-uploads/midi/8d94ca4d-8f27-4d78-a01f-73ee0fb8c6bc/drums_bpm.json",
                ],
            },
        )
        rendered = json.dumps(document, sort_keys=True)
        self.assertNotIn("s3:ListBucket", rendered)
        self.assertNotIn("s3:PutObject", json.dumps(output_statement, sort_keys=True))
        self.assertNotIn("*", rendered)

    def test_secret_templates_are_namespace_scoped_and_contain_only_placeholders(self) -> None:
        """Root administration and the future restricted client require separate Secrets."""

        bootstrap = _BOOTSTRAP_TEMPLATE.read_text(encoding="utf-8")
        runtime = _RUNTIME_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("namespace: clouddsp-data", bootstrap)
        self.assertIn("namespace: clouddsp-app", runtime)
        for source in (bootstrap, runtime):
            self.assertIn(
                "ADTOF_WORKER_SMOKE_S3_ACCESS_KEY: clouddsp-adtof-worker-smoke",
                source,
            )
            self.assertIn("REPLACE_WITH_A_LOCAL_TEST_SECRET_KEY", source)
            self.assertNotIn("MINIO_ROOT_PASSWORD:", source)

    def test_bootstrap_job_has_only_minio_administration_inputs(self) -> None:
        """The root-key Job cannot grow into a database, broker, or API client."""

        source = _BOOTSTRAP_JOB.read_text(encoding="utf-8")
        self.assertIn("apiVersion: batch/v1\nkind: Job", source)
        self.assertIn("name: minio-adtof-worker-smoke-objects-bootstrap", source)
        self.assertIn("namespace: clouddsp-data", source)
        self.assertIn("automountServiceAccountToken: false", source)
        self.assertIn("restartPolicy: Never", source)
        self.assertIn("backoffLimit: 0", source)
        self.assertIn("clouddsp-minio-root-credentials", source)
        self.assertIn("clouddsp-adtof-worker-smoke-minio-bootstrap-credentials", source)
        self.assertIn("clouddsp-adtof-worker-smoke-objects-policy-v001", source)
        self.assertIn(
            "clouddsp-registry.localhost:5001/minio-mc@sha256:37d109dddbbb2c95873f5fc81ac93f37023264770fc580a7564148892087b1b7",
            source,
        )
        self.assertIn("http://clouddsp-minio:9000", source)
        self.assertIn("clouddsp-adtof-worker-smoke-objects-v001", source)
        self.assertIn("remove-root-alias-from-temporary-config", source)
        self.assertNotIn("POSTGRES_", source)
        self.assertNotIn("RABBITMQ_", source)
        self.assertNotIn("kind: Deployment", source)
        self.assertNotIn("\n              kubectl", source)


if __name__ == "__main__":
    unittest.main()
