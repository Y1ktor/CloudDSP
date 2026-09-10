"""Unit tests for the dispatcher smoke normal-path orchestration helpers.

The tests do not create a Keycloak identity, call the Job API, upload an
object, connect to PostgreSQL/RabbitMQ/MinIO, or use Kubernetes.  They prove
the local code constrains its eventual integration test to one generated job
and does not quietly broaden into a direct outbox/message writer.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from dispatcher_smoke_client import ExpectedDemucsRequested
from dispatcher_smoke_orchestrator import (
    CONTROLLED_STEM_MODE,
    ControlledEventNotReady,
    ControlledUpload,
    DispatcherSmokeOrchestrationError,
    DispatcherSmokeOrchestratorSettings,
    expected_event_from_outbox_rows,
    private_minio_upload_url,
    wait_for_controlled_event,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


def settings(*, timeout_seconds: int = 5) -> DispatcherSmokeOrchestratorSettings:
    """Build non-secret fake settings without consulting the process environment."""

    return DispatcherSmokeOrchestratorSettings(
        keycloak_internal_base_url="http://keycloak.test:8080",
        job_api_internal_base_url="http://job-api.test:80",
        minio_internal_base_url="http://minio.test:9000",
        keycloak_admin_realm="master",
        clouddsp_realm="clouddsp",
        job_api_client_id="clouddsp-job-api",
        pod_name="dispatcher-smoke-test-abc123",
        keycloak_admin_username="test-admin",
        keycloak_admin_password="test-password",
        postgresql_admin_username="test-postgres",
        postgresql_admin_password="test-password",
        minio_root_username="test-minio",
        minio_root_password="test-password",
        timeout_seconds=timeout_seconds,
    )


def upload() -> ControlledUpload:
    """Return one generated-job scope suitable for pure contract checks."""

    return ControlledUpload(
        job_id=JOB_ID,
        owner_sub="opaque-temporary-keycloak-subject",
        object_key=f"uploads/{JOB_ID}/dispatcher-smoke.wav",
    )


class PrivateUploadRouteTests(unittest.TestCase):
    """Prove a Pod preserves the API-issued S3 path while changing only origin."""

    def test_replaces_only_the_browser_minio_origin(self) -> None:
        """The private DNS route cannot add/remove a signed S3 path segment."""

        self.assertEqual(
            private_minio_upload_url(
                "http://minio.localhost:8080/clouddsp-uploads/uploads/test/source.wav",
                "http://clouddsp-minio:9000",
            ),
            "http://clouddsp-minio:9000/clouddsp-uploads/uploads/test/source.wav",
        )

    def test_rejects_a_url_with_query_parameters(self) -> None:
        """This test is for presigned POST, not a different presigned-URL flow."""

        with self.assertRaises(DispatcherSmokeOrchestrationError):
            private_minio_upload_url(
                "http://minio.localhost:8080/clouddsp-uploads/uploads/test/source.wav?signature=value",
                "http://clouddsp-minio:9000",
            )

    def test_rejects_an_unexpected_browser_host(self) -> None:
        """A changed browser contract must be consciously reviewed, not routed blind."""

        with self.assertRaises(DispatcherSmokeOrchestrationError):
            private_minio_upload_url(
                "http://other.localhost:8080/clouddsp-uploads/uploads/test/source.wav",
                "http://clouddsp-minio:9000",
            )


class ControlledEventTests(unittest.TestCase):
    """Prove only the generated job's single version-1 Demucs event is accepted."""

    def test_accepts_a_pending_event_before_concurrent_dispatch(self) -> None:
        """The separate verifier—not setup—waits for final publication."""

        expected = expected_event_from_outbox_rows([(EVENT_ID, "pending")], upload())

        self.assertIsInstance(expected, ExpectedDemucsRequested)
        self.assertEqual(expected.event_id, EVENT_ID)
        self.assertEqual(expected.job_id, JOB_ID)
        self.assertEqual(expected.stem_mode, CONTROLLED_STEM_MODE)

    def test_accepts_a_published_event_when_dispatcher_wins_the_race(self) -> None:
        """A quick dispatcher must not make the integration test flaky."""

        expected = expected_event_from_outbox_rows([(EVENT_ID, "published")], upload())

        self.assertEqual(expected.event_id, EVENT_ID)

    def test_rejects_more_than_one_durable_event(self) -> None:
        """A duplicate outbox row would invalidate the required idempotency proof."""

        with self.assertRaises(DispatcherSmokeOrchestrationError):
            expected_event_from_outbox_rows(
                [(EVENT_ID, "pending"), ("11111111-1111-4111-8111-111111111111", "pending")],
                upload(),
            )

    def test_rejects_an_outbox_state_outside_dispatcher_lifecycle(self) -> None:
        """Setup must not hand a dead-lettered or unknown event to the reader."""

        with self.assertRaises(DispatcherSmokeOrchestrationError):
            expected_event_from_outbox_rows([(EVENT_ID, "dead_lettered")], upload())


class EventWaitTests(unittest.TestCase):
    """Prove the bounded poll tolerates ordinary asynchronous intake timing."""

    def test_waits_for_missing_event_then_returns_the_single_contract(self) -> None:
        """The source upload may arrive before intake's transaction is visible."""

        calls: list[bool] = []
        clock = iter((0.0, 0.0, 1.0, 1.0))

        def read_event(_settings, _upload):
            calls.append(True)
            if len(calls) == 1:
                raise ControlledEventNotReady("not ready")
            return expected_event_from_outbox_rows([(EVENT_ID, "leased")], upload())

        wait_for_controlled_event(
            settings(timeout_seconds=5),
            upload(),
            read_event=read_event,
            sleep_function=MagicMock(),
            monotonic=lambda: next(clock),
        )

        self.assertEqual(len(calls), 2)

    def test_times_out_without_returning_a_partially_created_contract(self) -> None:
        """A permanently absent row produces a safe bounded failure category."""

        clock = iter((0.0, 0.0, 1.0, 1.0, 2.0))

        with self.assertRaises(DispatcherSmokeOrchestrationError):
            wait_for_controlled_event(
                settings(timeout_seconds=1),
                upload(),
                read_event=lambda _settings, _upload: (_ for _ in ()).throw(
                    ControlledEventNotReady("not ready")
                ),
                sleep_function=MagicMock(),
                monotonic=lambda: next(clock),
            )


if __name__ == "__main__":
    unittest.main()
