"""Pure orchestration tests for the Basic Pitch KEDA burst runner.

The fakes below record only adapter method calls. No SDK, database, MinIO,
RabbitMQ, KEDA controller, worker, container, or Kubernetes API is involved.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock

from basic_pitch_keda_burst_smoke import BURST_COORDINATES, BasicPitchKedaBurstContractError
from burst_runner import run_burst
from minio_adapter import VerifiedMidi
from postgresql_adapter import BurstTaskObservation


_TASK_IDS = (
    "2be65ef6-71c0-4cb0-a21d-723d1b39d5ff",
    "08510e59-da5a-4a4e-b256-670175432baf",
    "0b3839ef-d9cf-43e7-b7b4-44c540785d4b",
)


def observations(*, completed: bool) -> tuple[BurstTaskObservation, ...]:
    """Return the exact restricted observe() projection for three test requests."""

    now = datetime.now(UTC)
    return tuple(
        BurstTaskObservation(
            request_label=coordinate.label,
            publication_status="published" if completed else "pending",
            published_at=now if completed else None,
            task_id=_TASK_IDS[index] if completed else None,
            task_status="succeeded" if completed else None,
            task_attempt_count=1 if completed else None,
            task_lease_is_clear=completed,
            task_completed_at=now if completed else None,
            job_status="midi_processing",
        )
        for index, coordinate in enumerate(BURST_COORDINATES)
    )


class Database:
    """Record calls and release controlled observation snapshots in sequence."""

    def __init__(self, snapshots: list[tuple[BurstTaskObservation, ...]]) -> None:
        self.snapshots = snapshots
        self.calls: list[str] = []
        self.prepared_wav_count = 0

    def assert_coordinates_clean(self) -> None:
        self.calls.append("clean")

    def prepare_durable_burst(self, wavs: tuple[object, ...]) -> None:
        self.calls.append("prepare")
        self.prepared_wav_count = len(wavs)

    def read_observations(self) -> tuple[BurstTaskObservation, ...]:
        self.calls.append("observe")
        return self.snapshots.pop(0)

    def cleanup_successful_burst(self) -> None:
        self.calls.append("cleanup")


class Storage:
    """Record exact storage phases without modeling an S3 implementation."""

    def __init__(self, *, upload_error: Exception | None = None) -> None:
        self.upload_error = upload_error
        self.calls: list[str] = []

    def assert_all_coordinates_absent(self) -> None:
        self.calls.append("clean")

    def upload_controlled_wavs(self, _wavs: tuple[object, ...]) -> None:
        self.calls.append("upload")
        if self.upload_error is not None:
            raise self.upload_error

    def verify_midi(self, *, coordinate: object, task_id: str, input_sha256: str) -> VerifiedMidi:
        self.calls.append(f"verify:{getattr(coordinate, 'label')}:{task_id}")
        return VerifiedMidi(coordinate=coordinate, size_bytes=32, sha256=input_sha256)

    def delete_all_fixed_objects(self) -> None:
        self.calls.append("delete")


class BurstRunnerTests(unittest.TestCase):
    """Prove normal ordering and evidence retention on the two failure phases."""

    def test_success_runs_normal_path_then_success_only_cleanup(self) -> None:
        """Three durable task completions, not queue state, unlock cleanup."""

        database = Database([observations(completed=False), observations(completed=True)])
        storage = Storage()
        report: list[str] = []
        clock = iter((0.0, 0.0, 1.0))

        verified = run_burst(
            database,
            storage,
            report=report.append,
            sleep_function=MagicMock(),
            monotonic=lambda: next(clock),
        )

        self.assertEqual(database.calls, ["clean", "prepare", "observe", "observe", "cleanup"])
        self.assertEqual(database.prepared_wav_count, 3)
        self.assertEqual(storage.calls[0:2], ["clean", "upload"])
        self.assertEqual(storage.calls[-1], "delete")
        self.assertEqual(len(verified), 3)
        self.assertEqual(report[-1], "Basic Pitch KEDA burst smoke passed")

    def test_timeout_after_prepare_retains_database_and_object_evidence(self) -> None:
        """A durable request must survive a worker/KEDA timeout for diagnosis."""

        database = Database([observations(completed=False), observations(completed=False)])
        storage = Storage()
        clock = iter((0.0, 0.0, 300.0))

        with self.assertRaises(BasicPitchKedaBurstContractError):
            run_burst(
                database,
                storage,
                report=lambda _message: None,
                sleep_function=MagicMock(),
                monotonic=lambda: next(clock),
            )

        self.assertEqual(database.calls, ["clean", "prepare", "observe", "observe"])
        self.assertNotIn("delete", storage.calls)
        self.assertNotIn("cleanup", database.calls)

    def test_pre_durable_upload_failure_attempts_exact_best_effort_cleanup(self) -> None:
        """There is no database evidence to preserve until prepare starts."""

        database = Database([])
        storage = Storage(upload_error=RuntimeError("simulated non-secret upload failure"))

        with self.assertRaises(RuntimeError):
            run_burst(database, storage, report=lambda _message: None)

        self.assertEqual(database.calls, ["clean"])
        self.assertEqual(storage.calls, ["clean", "upload", "delete"])

    def test_incomplete_observation_fails_without_cleanup(self) -> None:
        """A missing label cannot be mistaken for a completed three-request burst."""

        database = Database([observations(completed=True)[:2]])
        storage = Storage()

        with self.assertRaises(BasicPitchKedaBurstContractError):
            run_burst(database, storage, report=lambda _message: None)

        self.assertEqual(database.calls, ["clean", "prepare", "observe"])
        self.assertNotIn("delete", storage.calls)
        self.assertNotIn("cleanup", database.calls)


if __name__ == "__main__":
    unittest.main()
