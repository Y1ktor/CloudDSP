"""Unit tests for the real-client factory boundary and bounded entrypoint.

All third-party modules are inserted as in-memory fakes before a factory
imports them. These tests do not install/use Boto3/Psycopg, load a Secret,
contact a Service, create a container, or alter Kubernetes.
"""

from __future__ import annotations

import io
import sys
import types
import unittest
from unittest.mock import Mock, patch

from basic_pitch_keda_burst_smoke import BurstSettings
from burst_entrypoint import (
    BasicPitchKedaBurstContractError,
    BasicPitchKedaBurstMinioInfrastructureError,
    BasicPitchKedaBurstPostgresqlInfrastructureError,
    main,
    open_database_connection,
    open_minio_client,
)


def settings() -> BurstSettings:
    """Return non-secret-shaped test settings without reading environment state."""

    return BurstSettings(
        database_host="clouddsp-postgresql.clouddsp-data.svc",
        database_port=5432,
        database_name="clouddsp_job_api",
        database_username="clouddsp-basic-pitch-keda-burst-smoke",
        database_password="test-password-not-logged",
        minio_access_key="clouddsp-basic-pitch-keda-burst-smoke",
        minio_secret_key="test-secret-not-logged",
    )


class FactoryTests(unittest.TestCase):
    """Prove factories use explicit settings and bounded safe SDK configuration."""

    def test_database_factory_uses_autocommit_dict_rows_and_bounded_statement(self) -> None:
        """The function-only adapter needs mapping rows, not a raw tuple cursor."""

        connection = Mock()
        connect = Mock(return_value=connection)
        dict_row = object()
        psycopg_module = types.ModuleType("psycopg")
        psycopg_module.connect = connect
        rows_module = types.ModuleType("psycopg.rows")
        rows_module.dict_row = dict_row

        with patch.dict(sys.modules, {"psycopg": psycopg_module, "psycopg.rows": rows_module}):
            self.assertIs(open_database_connection(settings()), connection)

        kwargs = connect.call_args.kwargs
        self.assertEqual(kwargs["host"], "clouddsp-postgresql.clouddsp-data.svc")
        self.assertEqual(kwargs["port"], 5432)
        self.assertTrue(kwargs["autocommit"])
        self.assertIs(kwargs["row_factory"], dict_row)
        self.assertEqual(kwargs["options"], "-c statement_timeout=5000")
        self.assertEqual(kwargs["password"], "test-password-not-logged")

    def test_minio_factory_uses_explicit_path_style_credentials(self) -> None:
        """No ambient AWS chain or virtual bucket hostname is permitted."""

        client = Mock()
        boto_client = Mock(return_value=client)
        boto3_module = types.ModuleType("boto3")
        boto3_module.client = boto_client
        configurations: list[dict[str, object]] = []

        class Config:
            def __init__(self, **kwargs: object) -> None:
                configurations.append(kwargs)

        config_module = types.ModuleType("botocore.config")
        config_module.Config = Config

        with patch.dict(sys.modules, {"boto3": boto3_module, "botocore.config": config_module}):
            self.assertIs(open_minio_client(settings()), client)

        kwargs = boto_client.call_args.kwargs
        self.assertEqual(kwargs["endpoint_url"], "http://clouddsp-minio.clouddsp-data.svc:9000")
        self.assertEqual(kwargs["aws_access_key_id"], "clouddsp-basic-pitch-keda-burst-smoke")
        self.assertEqual(kwargs["aws_secret_access_key"], "test-secret-not-logged")
        self.assertEqual(kwargs["region_name"], "us-east-1")
        self.assertEqual(configurations, [{"connect_timeout": 5, "read_timeout": 15, "retries": {"max_attempts": 2, "mode": "standard"}, "s3": {"addressing_style": "path"}}])

    def test_missing_or_failing_sdk_is_exposed_as_a_safe_infrastructure_category(self) -> None:
        """Job output must not reveal a package traceback or connection details."""

        with patch.dict(sys.modules, {"psycopg": None, "psycopg.rows": None}):
            with self.assertRaises(BasicPitchKedaBurstPostgresqlInfrastructureError):
                open_database_connection(settings())
        with patch.dict(sys.modules, {"boto3": None, "botocore.config": None}):
            with self.assertRaises(BasicPitchKedaBurstMinioInfrastructureError):
                open_minio_client(settings())


class EntrypointTests(unittest.TestCase):
    """Prove PID 1 returns an ordinary status and closes its short-lived connection."""

    def test_success_constructs_both_adapters_runs_once_and_closes_connection(self) -> None:
        """No persistent background process is created by a finite smoke Job."""

        connection = Mock()
        minio_client = Mock()
        with (
            patch("burst_entrypoint.BurstSettings.from_environment", return_value=settings()),
            patch("burst_entrypoint.open_database_connection", return_value=connection),
            patch("burst_entrypoint.open_minio_client", return_value=minio_client),
            patch("burst_entrypoint.run_burst") as run,
        ):
            self.assertEqual(main(), 0)
        run.assert_called_once()
        connection.close.assert_called_once()

    def test_expected_failure_returns_one_with_safe_message_and_closes_connection(self) -> None:
        """A known contract error is inspectable without exposing a credential."""

        connection = Mock()
        output = io.StringIO()
        with (
            patch("burst_entrypoint.BurstSettings.from_environment", return_value=settings()),
            patch("burst_entrypoint.open_database_connection", return_value=connection),
            patch("burst_entrypoint.open_minio_client", return_value=Mock()),
            patch("burst_entrypoint.run_burst", side_effect=BasicPitchKedaBurstContractError("safe failure")),
            patch("sys.stderr", output),
        ):
            self.assertEqual(main(), 1)
        self.assertEqual(output.getvalue(), "Basic Pitch KEDA burst smoke failed: safe failure\n")
        self.assertNotIn("test-password-not-logged", output.getvalue())
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
