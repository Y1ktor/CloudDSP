"""Unit tests for the one-action-at-a-time ADTOF smoke composition facade.

Every boundary here is an in-memory fake. Tests construct no driver/SDK client,
open no socket, sleep, create a Kubernetes resource, or mutate MinIO/PostgreSQL.
"""

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
from adtof_worker_smoke_fixture import MIDI_KEY, SMOKE_EVENT_ID, SMOKE_JOB_ID, STEM_KEY, TEMPO_KEY, ControlledDrumWav
from adtof_worker_smoke_composition import (
    ADTOFWorkerSmokeCompositionError,
    ADTOFWorkerSmokeCompositionFacade,
)
from adtof_worker_smoke_orchestration import ADTOFWorkerSmokeAction, ADTOFWorkerSmokeOutcome


def _input_evidence() -> FixedObjectEvidence:
    """Return the earlier input adapter's one fixed bounded WAV proof."""

    return FixedObjectEvidence(
        key=STEM_KEY,
        content_type="audio/wav",
        size_bytes=44,
        sha256="a" * 64,
    )


def _outputs() -> VerifiedADTOFWorkerSmokeOutputs:
    """Return the earlier output reader's two fixed bounded result proofs."""

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


def _success_observation() -> ADTOFWorkerSmokeObservation:
    """Return the sole durable result that can let this facade reach cleanup."""

    now = datetime(2026, 9, 15, tzinfo=UTC)
    return ADTOFWorkerSmokeObservation(
        publication_status="published",
        published_at=now,
        task_id=str(uuid4()),
        task_status="succeeded",
        task_attempt_count=1,
        task_lease_is_clear=True,
        task_completed_at=now,
        job_status="midi_processing",
    )


class FakeInputBoundary:
    """Record the fixed preflight/upload calls without accessing object storage."""

    def __init__(self, *, upload_error: BaseException | None = None) -> None:
        self.upload_error = upload_error
        self.calls: list[str] = []
        self.fixture: ControlledDrumWav | None = None

    def assert_fixed_objects_absent(self) -> None:
        self.calls.append("preflight")

    def upload_controlled_drum_wav(self, fixture: ControlledDrumWav) -> FixedObjectEvidence:
        self.calls.append("upload")
        self.fixture = fixture
        if self.upload_error is not None:
            raise self.upload_error
        return _input_evidence()


class FakeDatabase:
    """Supply typed fixed-function outcomes without a database connection."""

    def __init__(
        self,
        *,
        observations: list[ADTOFWorkerSmokeObservation | None],
        cleanup_result: bool = True,
    ) -> None:
        self.observations = observations
        self.cleanup_result = cleanup_result
        self.calls: list[tuple[str, object]] = []

    def prepare_fixed_event(self, *, stem_size_bytes: int, stem_sha256: str) -> PreparedADTOFWorkerSmoke:
        self.calls.append(("prepare", (stem_size_bytes, stem_sha256)))
        return PreparedADTOFWorkerSmoke(job_id=SMOKE_JOB_ID, event_id=SMOKE_EVENT_ID)

    def observe_fixed_event(self) -> ADTOFWorkerSmokeObservation | None:
        self.calls.append(("observe", None))
        return self.observations.pop(0)

    def cleanup_fixed_success(self) -> bool:
        self.calls.append(("cleanup", None))
        return self.cleanup_result


class FakeOutputBoundary:
    """Record the exact task/input proof given to the read-only output adapter."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, FixedObjectEvidence]] = []

    def read_verified_adtof_outputs(
        self, *, task_id: str, input_stem: FixedObjectEvidence
    ) -> VerifiedADTOFWorkerSmokeOutputs:
        self.calls.append((task_id, input_stem))
        if self.error is not None:
            raise self.error
        return _outputs()


class FakeObjectCleanup:
    """Record cleanup proof without deleting an object."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple[ADTOFWorkerSmokeObservation, FixedObjectEvidence, VerifiedADTOFWorkerSmokeOutputs]] = []

    def delete_fixed_objects_after_success(
        self,
        *,
        observation: ADTOFWorkerSmokeObservation,
        input_stem: FixedObjectEvidence,
        outputs: VerifiedADTOFWorkerSmokeOutputs,
    ) -> None:
        self.calls.append((observation, input_stem, outputs))
        if self.error is not None:
            raise self.error


class ADTOFWorkerSmokeCompositionFacadeTests(unittest.TestCase):
    """Prove one facade calls only the expected narrow adapter method per phase."""

    def _facade(
        self,
        *,
        observations: list[ADTOFWorkerSmokeObservation | None],
        output_error: BaseException | None = None,
        cleanup_error: BaseException | None = None,
        database_cleanup_result: bool = True,
    ) -> tuple[ADTOFWorkerSmokeCompositionFacade, FakeInputBoundary, FakeDatabase, FakeOutputBoundary, FakeObjectCleanup]:
        """Build a facade from fakes so no real client can participate in a test."""

        input_boundary = FakeInputBoundary()
        database = FakeDatabase(
            observations=observations,
            cleanup_result=database_cleanup_result,
        )
        output_boundary = FakeOutputBoundary(error=output_error)
        object_cleanup = FakeObjectCleanup(error=cleanup_error)
        return (
            ADTOFWorkerSmokeCompositionFacade(
                input_boundary=input_boundary,
                database=database,
                output_boundary=output_boundary,
                object_cleanup=object_cleanup,
                observation_timeout_seconds=60,
            ),
            input_boundary,
            database,
            output_boundary,
            object_cleanup,
        )

    def test_happy_path_calls_each_existing_boundary_once_in_state_machine_order(self) -> None:
        """The facade cannot publish directly or skip its MinIO/PostgreSQL proof stages."""

        observation = _success_observation()
        facade, input_boundary, database, output_boundary, object_cleanup = self._facade(
            observations=[None, observation]
        )
        actions = [
            facade.advance_one().action,
            facade.advance_one().action,
            facade.advance_one().action,
            facade.advance_one(elapsed_observation_seconds=1).action,
            facade.advance_one(elapsed_observation_seconds=2).action,
            facade.advance_one().action,
            facade.advance_one().action,
            facade.advance_one().action,
        ]

        self.assertEqual(
            actions,
            [
                ADTOFWorkerSmokeAction.ASSERT_FIXED_OBJECTS_ABSENT,
                ADTOFWorkerSmokeAction.UPLOAD_CONTROLLED_WAV,
                ADTOFWorkerSmokeAction.PREPARE_FIXED_EVENT,
                ADTOFWorkerSmokeAction.OBSERVE_FIXED_EVENT,
                ADTOFWorkerSmokeAction.OBSERVE_FIXED_EVENT,
                ADTOFWorkerSmokeAction.READ_VERIFY_OUTPUTS,
                ADTOFWorkerSmokeAction.DELETE_FIXED_OBJECTS,
                ADTOFWorkerSmokeAction.CLEANUP_FIXED_DATABASE_EVENT,
            ],
        )
        self.assertEqual(input_boundary.calls, ["preflight", "upload"])
        self.assertIsNotNone(input_boundary.fixture)
        self.assertEqual(database.calls[0][0], "prepare")
        self.assertEqual(database.calls[1:], [("observe", None), ("observe", None), ("cleanup", None)])
        self.assertEqual(output_boundary.calls, [(observation.task_id, _input_evidence())])
        self.assertEqual(object_cleanup.calls, [(observation, _input_evidence(), _outputs())])
        self.assertEqual(facade.state.outcome, ADTOFWorkerSmokeOutcome.PASSED)

    def test_adapter_error_sets_truthful_terminal_state_and_redacts_its_message(self) -> None:
        """An output failure preserves evidence; no cleanup/database delete is attempted."""

        facade, _, database, output_boundary, object_cleanup = self._facade(
            observations=[_success_observation()],
            output_error=RuntimeError("private endpoint diagnostic"),
        )
        facade.advance_one()
        facade.advance_one()
        facade.advance_one()
        facade.advance_one(elapsed_observation_seconds=1)
        with self.assertRaises(ADTOFWorkerSmokeCompositionError) as raised:
            facade.advance_one()

        self.assertEqual(str(raised.exception), "ADTOF smoke action failed.")
        self.assertEqual(facade.state.outcome, ADTOFWorkerSmokeOutcome.FAILED_PRESERVING_EVIDENCE)
        self.assertEqual(len(output_boundary.calls), 1)
        self.assertEqual(object_cleanup.calls, [])
        self.assertNotIn(("cleanup", None), database.calls)

    def test_object_and_database_cleanup_outcomes_remain_distinct(self) -> None:
        """The facade does not label potentially partial deletion as full preservation."""

        object_failure, _, _, _, cleanup = self._facade(
            observations=[_success_observation()],
            cleanup_error=RuntimeError("private object endpoint"),
        )
        for elapsed in (None, None, None, 1, None):
            if elapsed is None:
                object_failure.advance_one()
            else:
                object_failure.advance_one(elapsed_observation_seconds=elapsed)
        with self.assertRaises(ADTOFWorkerSmokeCompositionError):
            object_failure.advance_one()
        self.assertEqual(object_failure.state.outcome, ADTOFWorkerSmokeOutcome.OBJECT_CLEANUP_INCOMPLETE)
        self.assertEqual(len(cleanup.calls), 1)

        database_refusal, _, database, _, _ = self._facade(
            observations=[_success_observation()],
            database_cleanup_result=False,
        )
        database_refusal.advance_one()
        database_refusal.advance_one()
        database_refusal.advance_one()
        database_refusal.advance_one(elapsed_observation_seconds=1)
        database_refusal.advance_one()
        database_refusal.advance_one()
        database_refusal.advance_one()
        self.assertEqual(
            database_refusal.state.outcome,
            ADTOFWorkerSmokeOutcome.DATABASE_CLEANUP_INCOMPLETE,
        )
        self.assertEqual(database.calls[-1], ("cleanup", None))

    def test_observation_time_is_required_only_for_observation_and_terminal_state_cannot_advance(self) -> None:
        """A future outer loop owns the clock and does not accidentally call an extra action."""

        facade, _, _, _, _ = self._facade(observations=[None])
        with self.assertRaises(ADTOFWorkerSmokeCompositionError):
            facade.advance_one(elapsed_observation_seconds=1)
        facade.advance_one()
        facade.advance_one()
        facade.advance_one()
        with self.assertRaises(ADTOFWorkerSmokeCompositionError):
            facade.advance_one()

        timeout, _, _, _, _ = self._facade(observations=[None])
        timeout.advance_one()
        timeout.advance_one()
        timeout.advance_one()
        timeout.advance_one(elapsed_observation_seconds=60)
        self.assertEqual(timeout.state.outcome, ADTOFWorkerSmokeOutcome.TIMED_OUT_PRESERVING_EVIDENCE)
        with self.assertRaises(ADTOFWorkerSmokeCompositionError):
            timeout.advance_one()

    def test_module_has_no_driver_construction_clock_sleep_or_generic_client_api(self) -> None:
        """Composition wires prebuilt narrow boundaries; it is not the runtime entrypoint."""

        source = (Path(__file__).parent / "adtof_worker_smoke_composition.py").read_text(
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
