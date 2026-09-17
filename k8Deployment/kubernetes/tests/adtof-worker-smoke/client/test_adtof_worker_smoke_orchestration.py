"""Unit tests for the pure, no-I/O ADTOF worker-smoke orchestration state machine."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from uuid import uuid4

from adtof_worker_smoke_contract import (
    ADTOFWorkerSmokeObservation,
    FixedObjectEvidence,
    PreparedADTOFWorkerSmoke,
    VerifiedADTOFWorkerSmokeOutputs,
)
from adtof_worker_smoke_fixture import MIDI_KEY, SMOKE_EVENT_ID, SMOKE_JOB_ID, STEM_KEY, TEMPO_KEY
from adtof_worker_smoke_orchestration import (
    ADTOFWorkerSmokeAction,
    ADTOFWorkerSmokeOrchestrationError,
    ADTOFWorkerSmokeOutcome,
    ADTOFWorkerSmokePhase,
    ADTOFWorkerSmokeStateMachine,
)


def _input_evidence() -> FixedObjectEvidence:
    """Return the narrow fixed input proof that follows the WAV upload boundary."""

    return FixedObjectEvidence(
        key=STEM_KEY,
        content_type="audio/wav",
        size_bytes=44,
        sha256="a" * 64,
    )


def _outputs() -> VerifiedADTOFWorkerSmokeOutputs:
    """Return the narrow fixed output pair that follows MinIO verification."""

    return VerifiedADTOFWorkerSmokeOutputs(
        midi=FixedObjectEvidence(
            key=MIDI_KEY,
            content_type="audio/midi",
            size_bytes=26,
            sha256="b" * 64,
        ),
        tempo=FixedObjectEvidence(
            key=TEMPO_KEY,
            content_type="application/json",
            size_bytes=200,
            sha256="c" * 64,
        ),
    )


def _success_observation(**overrides: object) -> ADTOFWorkerSmokeObservation:
    """Return the only durable worker result that can authorize output cleanup."""

    now = datetime(2026, 9, 15, tzinfo=UTC)
    values: dict[str, object] = {
        "publication_status": "published",
        "published_at": now,
        "task_id": str(uuid4()),
        "task_status": "succeeded",
        "task_attempt_count": 1,
        "task_lease_is_clear": True,
        "task_completed_at": now,
        "job_status": "midi_processing",
    }
    values.update(overrides)
    return ADTOFWorkerSmokeObservation(**values)  # type: ignore[arg-type]


def _observing_machine(*, timeout: int = 60) -> ADTOFWorkerSmokeStateMachine:
    """Advance a pure machine to its one repeated durable-observation phase."""

    return (
        ADTOFWorkerSmokeStateMachine.start(observation_timeout_seconds=timeout)
        .preflight_succeeded()
        .input_uploaded(_input_evidence())
        .event_prepared(PreparedADTOFWorkerSmoke(job_id=SMOKE_JOB_ID, event_id=SMOKE_EVENT_ID))
    )


class ADTOFWorkerSmokeStateMachineTests(unittest.TestCase):
    """Prove source-only workflow order and truthful terminal categories."""

    def test_happy_path_requires_every_safety_boundary_in_order(self) -> None:
        """Only a full verified first-attempt workflow can produce `passed`."""

        machine = ADTOFWorkerSmokeStateMachine.start(observation_timeout_seconds=60)
        self.assertEqual(machine.next_action, ADTOFWorkerSmokeAction.ASSERT_FIXED_OBJECTS_ABSENT)
        machine = machine.preflight_succeeded()
        self.assertEqual(machine.next_action, ADTOFWorkerSmokeAction.UPLOAD_CONTROLLED_WAV)
        machine = machine.input_uploaded(_input_evidence())
        self.assertEqual(machine.next_action, ADTOFWorkerSmokeAction.PREPARE_FIXED_EVENT)
        machine = machine.event_prepared(
            PreparedADTOFWorkerSmoke(job_id=SMOKE_JOB_ID, event_id=SMOKE_EVENT_ID)
        )
        self.assertEqual(machine.next_action, ADTOFWorkerSmokeAction.OBSERVE_FIXED_EVENT)

        # A published event without a claimed task is expected dispatcher/worker progress.
        machine = machine.observation_received(None, elapsed_seconds=10)
        self.assertEqual(machine.phase, ADTOFWorkerSmokePhase.OBSERVE_WORKER)
        machine = machine.observation_received(_success_observation(), elapsed_seconds=60)
        self.assertEqual(machine.next_action, ADTOFWorkerSmokeAction.READ_VERIFY_OUTPUTS)
        machine = machine.outputs_verified(_outputs())
        self.assertEqual(machine.next_action, ADTOFWorkerSmokeAction.DELETE_FIXED_OBJECTS)
        machine = machine.objects_cleaned()
        self.assertEqual(machine.next_action, ADTOFWorkerSmokeAction.CLEANUP_FIXED_DATABASE_EVENT)
        machine = machine.database_cleanup_finished(deleted=True)

        self.assertEqual(machine.phase, ADTOFWorkerSmokePhase.PASSED)
        self.assertEqual(machine.outcome, ADTOFWorkerSmokeOutcome.PASSED)
        self.assertIsNone(machine.next_action)

    def test_timeout_and_terminal_worker_facts_preserve_evidence(self) -> None:
        """Waiting longer cannot turn a retry/dead-letter into first-attempt success."""

        timeout = _observing_machine(timeout=60).observation_received(None, elapsed_seconds=60)
        self.assertEqual(timeout.phase, ADTOFWorkerSmokePhase.TIMED_OUT_PRESERVING_EVIDENCE)
        self.assertEqual(timeout.outcome, ADTOFWorkerSmokeOutcome.TIMED_OUT_PRESERVING_EVIDENCE)

        retry = _observing_machine().observation_received(
            _success_observation(task_status="retry_scheduled", task_attempt_count=1),
            elapsed_seconds=1,
        )
        self.assertEqual(retry.phase, ADTOFWorkerSmokePhase.FAILED_PRESERVING_EVIDENCE)

        second_attempt = _observing_machine().observation_received(
            _success_observation(task_status="running", task_attempt_count=2, task_completed_at=None),
            elapsed_seconds=1,
        )
        self.assertEqual(second_attempt.phase, ADTOFWorkerSmokePhase.FAILED_PRESERVING_EVIDENCE)

    def test_cleanup_categories_do_not_falsely_claim_all_evidence_was_preserved(self) -> None:
        """S3 and PostgreSQL cleanup failures are distinct from pre-cleanup failures."""

        before_cleanup = _observing_machine().current_action_failed()
        self.assertEqual(before_cleanup.outcome, ADTOFWorkerSmokeOutcome.FAILED_PRESERVING_EVIDENCE)

        verified = _observing_machine().observation_received(_success_observation(), elapsed_seconds=1).outputs_verified(
            _outputs()
        )
        object_cleanup_failure = verified.current_action_failed()
        self.assertEqual(
            object_cleanup_failure.outcome,
            ADTOFWorkerSmokeOutcome.OBJECT_CLEANUP_INCOMPLETE,
        )

        database_cleanup_failure = verified.objects_cleaned().database_cleanup_finished(deleted=False)
        self.assertEqual(
            database_cleanup_failure.outcome,
            ADTOFWorkerSmokeOutcome.DATABASE_CLEANUP_INCOMPLETE,
        )

    def test_rejects_skipped_transitions_malformed_evidence_and_invalid_elapsed_time(self) -> None:
        """No later runtime can bypass preflight/proof or make the wait unbounded."""

        machine = ADTOFWorkerSmokeStateMachine.start()
        with self.assertRaises(ADTOFWorkerSmokeOrchestrationError):
            machine.objects_cleaned()
        with self.assertRaises(ADTOFWorkerSmokeOrchestrationError):
            machine.input_uploaded(_input_evidence())
        with self.assertRaises(ADTOFWorkerSmokeOrchestrationError):
            ADTOFWorkerSmokeStateMachine.start(observation_timeout_seconds=59)

        observing = _observing_machine()
        with self.assertRaises(ADTOFWorkerSmokeOrchestrationError):
            observing.observation_received(None, elapsed_seconds=-1)
        with self.assertRaises(ADTOFWorkerSmokeOrchestrationError):
            observing.observation_received(None, elapsed_seconds=1).observation_received(
                None,
                elapsed_seconds=0,
            )

    def test_module_has_no_clock_sleep_network_or_runtime_client_capability(self) -> None:
        """This task remains a pure transition model, not an accidental smoke runner."""

        source = (Path(__file__).parent / "adtof_worker_smoke_orchestration.py").read_text(
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
            "def sleep(",
            "def put_object(",
            "def get_object(",
            "def delete_object(",
            "def execute(",
            "def publish(",
        ):
            self.assertNotIn(forbidden_capability, source)


if __name__ == "__main__":
    unittest.main()
