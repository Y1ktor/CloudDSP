"""Offline tests for the broker/observer readiness barrier."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from lifecycle_handoff import write_broker_failure_marker
from observer_start_gate import (
    ObserverStartGateError,
    prepare_observer_start_gate,
    wait_for_observer_ready,
    wait_for_observer_start_request,
    write_observer_ready,
    write_observer_start_request,
)


class ObserverStartGateTests(unittest.TestCase):
    """The broker cannot release a load client before a same-run baseline check."""

    def test_round_trips_marker_and_readiness_as_private_atomic_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            directory.chmod(0o700)
            prepare_observer_start_gate(directory)
            write_observer_start_request(directory, run_marker="loadrun01")
            self.assertEqual(
                wait_for_observer_start_request(directory, timeout_seconds=0.1),
                "loadrun01",
            )
            write_observer_ready(directory, run_marker="loadrun01", ready=True)
            self.assertTrue(wait_for_observer_ready(
                directory, run_marker="loadrun01", timeout_seconds=0.1
            ))
            self.assertEqual((directory / "six-stem-load-observer-ready.json").stat().st_mode & 0o777, 0o600)

    def test_rejects_stale_files_and_marker_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            directory.chmod(0o700)
            write_observer_start_request(directory, run_marker="loadrun01")
            with self.assertRaises(ObserverStartGateError):
                prepare_observer_start_gate(directory)
            write_observer_ready(directory, run_marker="loadrun01", ready=True)
            with self.assertRaises(ObserverStartGateError):
                wait_for_observer_ready(directory, run_marker="loadrun02", timeout_seconds=0.1)

    def test_broker_failure_wakes_observer_before_start_request(self) -> None:
        """The observer must exit quickly when the broker never starts it."""

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            directory.chmod(0o700)
            write_broker_failure_marker(directory)
            with self.assertRaisesRegex(ObserverStartGateError, "broker failed"):
                wait_for_observer_start_request(directory, timeout_seconds=1)


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
