"""Unit tests for lazy fixed-setting Psycopg smoke-connection construction.

Psycopg is patched at the module boundary. These tests import no installed
driver, open no PostgreSQL connection, and perform no MinIO/RabbitMQ/model/
Kubernetes action.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from adtof_worker_smoke_contract import ADTOFWorkerSmokeSettings
from adtof_worker_smoke_postgresql import (
    ADTOF_WORKER_SMOKE_POSTGRESQL_APPLICATION_NAME,
    ADTOFWorkerSmokePostgreSQLConfigurationError,
    ADTOFWorkerSmokePostgreSQLDependencyError,
    ADTOFWorkerSmokePostgreSQLInfrastructureError,
    create_psycopg_adtof_worker_smoke_connection_factory,
)


def _settings() -> ADTOFWorkerSmokeSettings:
    """Return valid fixed settings without retrieving a Secret from the environment."""

    return ADTOFWorkerSmokeSettings(
        database_host="clouddsp-postgresql.clouddsp-data.svc",
        database_port=5432,
        database_name="clouddsp_job_api",
        database_username="clouddsp-adtof-worker-smoke",
        database_password="test-database-password",
        minio_access_key="clouddsp-adtof-worker-smoke",
        minio_secret_key="unneeded-by-this-factory",
    )


class ADTOFWorkerSmokePostgreSQLFactoryTests(unittest.TestCase):
    """Keep the future driver boundary lazy, fixed-route, bounded, and transaction-aware."""

    @patch("adtof_worker_smoke_postgresql._load_psycopg")
    def test_factory_is_lazy_then_opens_exact_non_autocommit_dictionary_connection(self, loader) -> None:
        """The existing fixed-function adapter receives the transaction-owning connection."""

        psycopg = MagicMock()
        connection = MagicMock()
        psycopg.connect.return_value = connection
        dict_row = object()
        loader.return_value = (psycopg, dict_row)

        factory = create_psycopg_adtof_worker_smoke_connection_factory(_settings())
        loader.assert_not_called()

        self.assertIs(factory(), connection)
        loader.assert_called_once_with()
        psycopg.connect.assert_called_once_with(
            host="clouddsp-postgresql.clouddsp-data.svc",
            port=5432,
            dbname="clouddsp_job_api",
            user="clouddsp-adtof-worker-smoke",
            password="test-database-password",
            connect_timeout=3,
            options="-c statement_timeout=5000",
            application_name=ADTOF_WORKER_SMOKE_POSTGRESQL_APPLICATION_NAME,
            autocommit=False,
            row_factory=dict_row,
        )

    @patch("adtof_worker_smoke_postgresql._load_psycopg")
    def test_forged_route_role_or_password_is_rejected_before_driver_import(self, loader) -> None:
        """Directly constructed settings cannot redirect the restricted smoke identity."""

        for forged in (
            replace(_settings(), database_host="localhost"),
            replace(_settings(), database_port=5433),
            replace(_settings(), database_name="postgres"),
            replace(_settings(), database_username="clouddsp-job-api"),
            replace(_settings(), database_password=""),
        ):
            with self.subTest(forged=forged):
                with self.assertRaises(ADTOFWorkerSmokePostgreSQLConfigurationError):
                    create_psycopg_adtof_worker_smoke_connection_factory(forged)
        loader.assert_not_called()

    @patch("adtof_worker_smoke_postgresql._load_psycopg")
    def test_dependency_and_connection_failures_remain_bounded_categories(self, loader) -> None:
        """Private driver/Service details cannot escape this source-controlled factory."""

        factory = create_psycopg_adtof_worker_smoke_connection_factory(_settings())
        loader.side_effect = ADTOFWorkerSmokePostgreSQLDependencyError(
            "Pinned ADTOF smoke PostgreSQL dependency is unavailable."
        )
        with self.assertRaises(ADTOFWorkerSmokePostgreSQLDependencyError):
            factory()

        psycopg = MagicMock()
        psycopg.connect.side_effect = RuntimeError("private Service diagnostic")
        loader.side_effect = None
        loader.return_value = (psycopg, object())
        with self.assertRaises(ADTOFWorkerSmokePostgreSQLInfrastructureError) as raised:
            factory()
        self.assertEqual(str(raised.exception), "ADTOF smoke PostgreSQL connection is unavailable.")

    def test_module_keeps_psycopg_lazy_and_has_no_minio_broker_or_sql_capability(self) -> None:
        """This focused task constructs only a connection factory, not the smoke workflow."""

        source = (Path(__file__).parent / "adtof_worker_smoke_postgresql.py").read_text(
            encoding="utf-8"
        )
        self.assertLess(source.index("def _load_psycopg"), source.index("import psycopg"))
        for forbidden_import in ("import boto3", "import pika", "import requests", "import time"):
            self.assertNotIn(forbidden_import, source)
        for forbidden_capability in (
            "def execute(",
            "def put_object(",
            "def get_object(",
            "def delete_object(",
            "def publish(",
            "def sleep(",
        ):
            self.assertNotIn(forbidden_capability, source)


if __name__ == "__main__":
    unittest.main()
