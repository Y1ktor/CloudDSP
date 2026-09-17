"""Unit tests for bounded ADTOF smoke runtime control and safe process reporting.

The facade, clock, sleeper, settings loader, and runtime assembly are faked or
patched. Tests make no driver/SDK call, network connection, object/database
mutation, broker operation, image build, or Kubernetes resource.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unittest
from unittest.mock import patch

from adtof_worker_smoke_composition import ADTOFWorkerSmokeCompositionError
from adtof_worker_smoke_contract import ADTOFWorkerSmokeConfigurationError, ADTOFWorkerSmokeSettings
from adtof_worker_smoke_entrypoint import run_adtof_worker_smoke, run_adtof_worker_smoke_entrypoint
from adtof_worker_smoke_orchestration import ADTOFWorkerSmokeAction, ADTOFWorkerSmokeOutcome


@dataclass(frozen=True)
class FakeState:
    """Expose the small immutable state surface the entrypoint is allowed to read."""

    next_action: ADTOFWorkerSmokeAction | None
    outcome: ADTOFWorkerSmokeOutcome | None = None
    observation_timeout_seconds: int = 60


class FakeFacade:
    """Advance through supplied in-memory state snapshots without calling a real adapter."""

    def __init__(
        self,
        initial: FakeState,
        transitions: list[FakeState],
        *,
        fail_on_action: ADTOFWorkerSmokeAction | None = None,
    ) -> None:
        self.state = initial
        self._transitions = transitions
        self._fail_on_action = fail_on_action
        self.calls: list[int | None] = []

    def advance_one(self, *, elapsed_observation_seconds: int | None = None) -> object:
        self.calls.append(elapsed_observation_seconds)
        if self.state.next_action is self._fail_on_action:
            self.state = FakeState(
                next_action=None,
                outcome=ADTOFWorkerSmokeOutcome.FAILED_PRESERVING_EVIDENCE,
            )
            raise ADTOFWorkerSmokeCompositionError("ADTOF smoke action failed.")
        self.state = self._transitions.pop(0)
        return object()


def _settings() -> ADTOFWorkerSmokeSettings:
    """Return valid fixed settings without retrieving a Secret from a real environment."""

    return ADTOFWorkerSmokeSettings(
        database_host="clouddsp-postgresql.clouddsp-data.svc",
        database_port=5432,
        database_name="clouddsp_job_api",
        database_username="clouddsp-adtof-worker-smoke",
        database_password="test-database-password",
        minio_access_key="clouddsp-adtof-worker-smoke",
        minio_secret_key="test-minio-secret",
        observation_timeout_seconds=60,
    )


class ADTOFWorkerSmokeEntrypointTests(unittest.TestCase):
    """Prove the entrypoint polls only observation and returns safe terminal outcomes."""

    def test_happy_path_uses_monotonic_observation_only_and_reports_each_safe_milestone(self) -> None:
        """No action retry/sleep occurs before or after the dispatcher/worker observation phase."""

        facade = FakeFacade(
            FakeState(ADTOFWorkerSmokeAction.ASSERT_FIXED_OBJECTS_ABSENT),
            [
                FakeState(ADTOFWorkerSmokeAction.UPLOAD_CONTROLLED_WAV),
                FakeState(ADTOFWorkerSmokeAction.PREPARE_FIXED_EVENT),
                FakeState(ADTOFWorkerSmokeAction.OBSERVE_FIXED_EVENT),
                FakeState(ADTOFWorkerSmokeAction.OBSERVE_FIXED_EVENT),
                FakeState(ADTOFWorkerSmokeAction.READ_VERIFY_OUTPUTS),
                FakeState(ADTOFWorkerSmokeAction.DELETE_FIXED_OBJECTS),
                FakeState(ADTOFWorkerSmokeAction.CLEANUP_FIXED_DATABASE_EVENT),
                FakeState(next_action=None, outcome=ADTOFWorkerSmokeOutcome.PASSED),
            ],
        )
        monotonic_values = iter((100.0, 100.0, 102.0))
        sleeps: list[float] = []
        messages: list[str] = []

        outcome = run_adtof_worker_smoke(
            facade,  # type: ignore[arg-type]
            monotonic=lambda: next(monotonic_values),
            sleep=sleeps.append,
            report=messages.append,
        )

        self.assertEqual(outcome, ADTOFWorkerSmokeOutcome.PASSED)
        self.assertEqual(facade.calls, [None, None, None, 0, 2, None, None, None])
        self.assertEqual(sleeps, [2.0])
        self.assertEqual(
            messages,
            [
                "ADTOF worker smoke: checking fixed object preflight",
                "ADTOF worker smoke: uploading controlled drums WAV",
                "ADTOF worker smoke: creating durable Job and outbox event",
                "ADTOF worker smoke: waiting for deployed dispatcher and ADTOF worker",
                "ADTOF worker smoke: verifying worker-produced MIDI and tempo outputs",
                "ADTOF worker smoke: cleaning verified fixed MinIO objects",
                "ADTOF worker smoke: cleaning guarded PostgreSQL evidence",
                "ADTOF worker smoke: passed",
            ],
        )

    def test_terminal_action_failure_reports_preserving_evidence_without_retry_or_sleep(self) -> None:
        """A failed upload/output action becomes the facade's terminal fact immediately."""

        facade = FakeFacade(
            FakeState(ADTOFWorkerSmokeAction.UPLOAD_CONTROLLED_WAV),
            [],
            fail_on_action=ADTOFWorkerSmokeAction.UPLOAD_CONTROLLED_WAV,
        )
        messages: list[str] = []
        sleeps: list[float] = []

        outcome = run_adtof_worker_smoke(
            facade,  # type: ignore[arg-type]
            monotonic=lambda: 0.0,
            sleep=sleeps.append,
            report=messages.append,
        )

        self.assertEqual(outcome, ADTOFWorkerSmokeOutcome.FAILED_PRESERVING_EVIDENCE)
        self.assertEqual(facade.calls, [None])
        self.assertEqual(sleeps, [])
        self.assertEqual(
            messages,
            [
                "ADTOF worker smoke: uploading controlled drums WAV",
                "ADTOF worker smoke: failed; preserving evidence",
            ],
        )

    @patch("adtof_worker_smoke_entrypoint.build_adtof_worker_smoke_composition")
    @patch("adtof_worker_smoke_entrypoint.ADTOFWorkerSmokeSettings.from_environment")
    def test_entrypoint_loads_settings_builds_once_and_maps_terminal_outcome_to_exit_status(self, load_settings, build) -> None:
        """The future PID-1 process succeeds only for the state-machine `passed` outcome."""

        facade = FakeFacade(
            FakeState(next_action=None, outcome=ADTOFWorkerSmokeOutcome.PASSED),
            [],
        )
        load_settings.return_value = _settings()
        build.return_value = facade
        messages: list[str] = []

        exit_code = run_adtof_worker_smoke_entrypoint({}, report=messages.append)

        self.assertEqual(exit_code, 0)
        load_settings.assert_called_once_with({})
        build.assert_called_once_with(_settings(), observation_timeout_seconds=60)
        self.assertEqual(messages, ["ADTOF worker smoke: passed"])

    @patch("adtof_worker_smoke_entrypoint.ADTOFWorkerSmokeSettings.from_environment")
    def test_configuration_and_unexpected_setup_failures_are_redacted_and_nonzero(self, load_settings) -> None:
        """A process log does not echo a Secret or concrete driver/SDK exception at startup."""

        load_settings.side_effect = ADTOFWorkerSmokeConfigurationError("ADTOF_WORKER_SMOKE_DB_PASSWORD missing")
        messages: list[str] = []
        self.assertEqual(run_adtof_worker_smoke_entrypoint({}, report=messages.append), 1)
        self.assertEqual(messages, ["ADTOF worker smoke: runtime configuration is invalid"])

        load_settings.side_effect = RuntimeError("private endpoint and password")
        messages.clear()
        self.assertEqual(run_adtof_worker_smoke_entrypoint({}, report=messages.append), 1)
        self.assertEqual(messages, ["ADTOF worker smoke: runtime setup is unavailable"])

    def test_module_owns_only_runtime_control_not_sql_s3_amqp_model_or_kubernetes_capability(self) -> None:
        """This final control layer delegates all external authority to reviewed lower boundaries."""

        source = Path(__file__).with_name("adtof_worker_smoke_entrypoint.py").read_text(
            encoding="utf-8"
        )
        for forbidden_import in ("import boto3", "import psycopg", "import pika", "import requests"):
            self.assertNotIn(forbidden_import, source)
        for forbidden_capability in (
            "def execute(",
            "def put_object(",
            "def get_object(",
            "def delete_object(",
            "def publish(",
            "apiVersion:",
        ):
            self.assertNotIn(forbidden_capability, source)


if __name__ == "__main__":
    unittest.main()
