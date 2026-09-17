"""Unit tests for source-only ADTOF smoke runtime assembly.

All external factories are patched. Tests build no SDK driver/client, open no
socket, invoke no workflow, and create no object/database/broker/Kubernetes
artifact.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from adtof_worker_smoke_composition import ADTOFWorkerSmokeCompositionFacade
from adtof_worker_smoke_contract import ADTOFWorkerSmokeSettings
from adtof_worker_smoke_orchestration import (
    ADTOFWorkerSmokeAction,
    ADTOFWorkerSmokeOrchestrationError,
    ADTOFWorkerSmokePhase,
)
from adtof_worker_smoke_runtime import build_adtof_worker_smoke_composition


class FakeS3Error(Exception):
    """Minimal S3-shaped not-found response for a no-network facade preflight."""

    def __init__(self) -> None:
        self.response = {"Error": {"Code": "NotFound"}}
        super().__init__("private fake S3 detail")


class FakeMinIOClient:
    """Expose all wrapper-required methods and record calls without any transport."""

    def __init__(self) -> None:
        self.head_calls: list[dict[str, object]] = []

    def head_object(self, **kwargs: object) -> object:
        self.head_calls.append(dict(kwargs))
        raise FakeS3Error()

    def get_object(self, **kwargs: object) -> object:
        raise AssertionError("output reads are outside runtime assembly")

    def put_object(self, **kwargs: object) -> object:
        raise AssertionError("uploads are outside runtime assembly")

    def delete_object(self, **kwargs: object) -> object:
        raise AssertionError("cleanup is outside runtime assembly")


def _settings() -> ADTOFWorkerSmokeSettings:
    """Return valid direct settings without reading a mounted Kubernetes Secret."""

    return ADTOFWorkerSmokeSettings(
        database_host="clouddsp-postgresql.clouddsp-data.svc",
        database_port=5432,
        database_name="clouddsp_job_api",
        database_username="clouddsp-adtof-worker-smoke",
        database_password="test-database-password",
        minio_access_key="clouddsp-adtof-worker-smoke",
        minio_secret_key="test-minio-secret",
    )


class ADTOFWorkerSmokeRuntimeAssemblyTests(unittest.TestCase):
    """Prove assembly composes existing wrappers but starts no external workflow."""

    @patch("adtof_worker_smoke_runtime.create_boto3_adtof_worker_smoke_minio_client")
    @patch("adtof_worker_smoke_runtime.create_psycopg_adtof_worker_smoke_connection_factory")
    def test_assembly_wires_one_client_and_lazy_database_factory_without_executing(self, database_factory, minio_factory) -> None:
        """The returned facade starts at preflight; no PostgreSQL connection is opened."""

        connection_factory = MagicMock()
        database_factory.return_value = connection_factory
        minio = FakeMinIOClient()
        minio_factory.return_value = minio

        facade = build_adtof_worker_smoke_composition(_settings(), observation_timeout_seconds=60)

        self.assertIsInstance(facade, ADTOFWorkerSmokeCompositionFacade)
        self.assertEqual(facade.state.phase, ADTOFWorkerSmokePhase.PREFLIGHT)
        self.assertEqual(facade.state.next_action, ADTOFWorkerSmokeAction.ASSERT_FIXED_OBJECTS_ABSENT)
        database_factory.assert_called_once_with(_settings())
        minio_factory.assert_called_once_with(_settings())
        connection_factory.assert_not_called()
        self.assertEqual(minio.head_calls, [])

        # Running precisely the preflight action demonstrates that the returned
        # wrappers share this one injected client without an assembly-time S3 call.
        self.assertEqual(facade.advance_one().action, ADTOFWorkerSmokeAction.ASSERT_FIXED_OBJECTS_ABSENT)
        self.assertEqual(facade.state.phase, ADTOFWorkerSmokePhase.UPLOAD_INPUT)
        self.assertEqual(len(minio.head_calls), 3)
        connection_factory.assert_not_called()

    @patch("adtof_worker_smoke_runtime.create_boto3_adtof_worker_smoke_minio_client")
    @patch("adtof_worker_smoke_runtime.create_psycopg_adtof_worker_smoke_connection_factory")
    def test_invalid_timeout_stops_before_any_factory_or_sdk_configuration(self, database_factory, minio_factory) -> None:
        """Pure state validation prevents a bad runtime argument from reaching either client path."""

        with self.assertRaises(ADTOFWorkerSmokeOrchestrationError):
            build_adtof_worker_smoke_composition(_settings(), observation_timeout_seconds=59)
        database_factory.assert_not_called()
        minio_factory.assert_not_called()

    def test_module_has_no_driver_sdk_clock_loop_or_entrypoint_capability(self) -> None:
        """This remains assembly only; a later task owns execution and process behavior."""

        source = (Path(__file__).parent / "adtof_worker_smoke_runtime.py").read_text(
            encoding="utf-8"
        )
        for forbidden_import in (
            "import boto3",
            "import psycopg",
            "import pika",
            "import requests",
            "import time",
            "import asyncio",
        ):
            self.assertNotIn(forbidden_import, source)
        for forbidden_capability in (
            "def main(",
            "def sleep(",
            "def run_smoke(",
            "def execute(",
            "def publish(",
        ):
            self.assertNotIn(forbidden_capability, source)


if __name__ == "__main__":
    unittest.main()
