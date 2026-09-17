"""Unit tests for lazy fixed-setting Boto3 MinIO client construction.

Boto3/Botocore factories are patched in memory. Tests import no installed SDK,
make no MinIO request, and do not open a PostgreSQL/RabbitMQ/model/Kubernetes
connection or resource.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from adtof_worker_smoke_contract import ADTOFWorkerSmokeSettings
from adtof_worker_smoke_minio import (
    ADTOFWorkerSmokeMinIOConfigurationError,
    ADTOFWorkerSmokeMinIODependencyError,
    ADTOFWorkerSmokeMinIOInfrastructureError,
    create_boto3_adtof_worker_smoke_minio_client,
)


def _settings() -> ADTOFWorkerSmokeSettings:
    """Return valid fixed settings without reading a mounted Secret or environment."""

    return ADTOFWorkerSmokeSettings(
        database_host="clouddsp-postgresql.clouddsp-data.svc",
        database_port=5432,
        database_name="clouddsp_job_api",
        database_username="clouddsp-adtof-worker-smoke",
        database_password="unneeded-by-this-factory",
        minio_access_key="clouddsp-adtof-worker-smoke",
        minio_secret_key="test-minio-secret",
    )


class ADTOFWorkerSmokeMinIOFactoryTests(unittest.TestCase):
    """Keep the future S3 construction lazy, fixed-route, bounded, and path-style."""

    @patch("adtof_worker_smoke_minio._load_boto3_client_factories")
    def test_client_factory_is_lazy_then_builds_one_explicit_private_path_style_client(self, loader) -> None:
        """Client construction gets no ambient AWS credential/endpoint discovery input."""

        boto3_client = MagicMock()
        client = object()
        boto3_client.return_value = client
        config_factory = MagicMock()
        config = object()
        config_factory.return_value = config
        loader.return_value = (boto3_client, config_factory)

        create = create_boto3_adtof_worker_smoke_minio_client
        loader.assert_not_called()

        self.assertIs(create(_settings()), client)
        loader.assert_called_once_with()
        config_factory.assert_called_once_with(
            signature_version="s3v4",
            connect_timeout=3,
            read_timeout=10,
            retries={"mode": "standard", "max_attempts": 2},
            s3={"addressing_style": "path"},
        )
        boto3_client.assert_called_once_with(
            "s3",
            endpoint_url="http://clouddsp-minio.clouddsp-data.svc:9000",
            region_name="us-east-1",
            aws_access_key_id="clouddsp-adtof-worker-smoke",
            aws_secret_access_key="test-minio-secret",
            config=config,
        )

    @patch("adtof_worker_smoke_minio._load_boto3_client_factories")
    def test_forged_endpoint_region_identity_or_secret_is_rejected_before_sdk_import(self, loader) -> None:
        """Direct settings construction cannot repoint the fixed smoke S3 identity."""

        for forged in (
            replace(_settings(), minio_endpoint_url="http://localhost:9000"),
            replace(_settings(), minio_region="another-region"),
            replace(_settings(), minio_access_key="clouddsp-job-api"),
            replace(_settings(), minio_secret_key=""),
        ):
            with self.subTest(forged=forged):
                with self.assertRaises(ADTOFWorkerSmokeMinIOConfigurationError):
                    create_boto3_adtof_worker_smoke_minio_client(forged)
        loader.assert_not_called()

    @patch("adtof_worker_smoke_minio._load_boto3_client_factories")
    def test_missing_sdk_and_client_construction_error_use_redacted_categories(self, loader) -> None:
        """A future Job log receives no SDK endpoint/credential diagnostic from this factory."""

        loader.side_effect = ADTOFWorkerSmokeMinIODependencyError(
            "Pinned ADTOF smoke S3 dependency is unavailable."
        )
        with self.assertRaises(ADTOFWorkerSmokeMinIODependencyError):
            create_boto3_adtof_worker_smoke_minio_client(_settings())

        boto3_client = MagicMock(side_effect=RuntimeError("private MinIO endpoint diagnostic"))
        loader.side_effect = None
        loader.return_value = (boto3_client, MagicMock(return_value=object()))
        with self.assertRaises(ADTOFWorkerSmokeMinIOInfrastructureError) as raised:
            create_boto3_adtof_worker_smoke_minio_client(_settings())
        self.assertEqual(str(raised.exception), "ADTOF smoke MinIO client is unavailable.")

    def test_module_keeps_boto3_lazy_and_has_no_database_broker_or_workflow_capability(self) -> None:
        """This focused task builds only an injected S3 client, not the smoke runner."""

        source = (Path(__file__).parent / "adtof_worker_smoke_minio.py").read_text(
            encoding="utf-8"
        )
        self.assertLess(source.index("def _load_boto3_client_factories"), source.index("import boto3"))
        for forbidden_import in ("import psycopg", "import pika", "import requests", "import time"):
            self.assertNotIn(forbidden_import, source)
        for forbidden_capability in (
            "def execute(",
            "def publish(",
            "def sleep(",
            "def run_smoke(",
        ):
            self.assertNotIn(forbidden_capability, source)


if __name__ == "__main__":
    unittest.main()
