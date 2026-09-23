"""Offline tests for the complete-load outcome handoff, independent of PostgreSQL snapshots."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from load_test_terminal_outcome import (
    LOAD_TEST_TERMINAL_OUTCOME_FILE_NAME,
    LoadTestTerminalOutcome,
    LoadTestTerminalOutcomeError,
    prepare_empty_load_test_terminal_outcome_directory,
    read_load_test_terminal_outcome,
    wait_for_load_test_terminal_outcome,
    write_load_test_terminal_outcome,
)
from postgresql_durable_state_observer import PostgreSQLDurableStateSnapshot
from postgresql_observer_report import (
    PostgreSQLObserverReport,
    write_postgresql_observer_report,
)


_RUN_MARKER = "loadrun01"


def _snapshot() -> PostgreSQLDurableStateSnapshot:
    """Represent a valid point-in-time snapshot that is not end-to-end proof."""

    return PostgreSQLDurableStateSnapshot(
        observed_job_count=3,
        source_uploaded_count=3,
        job_status_counts=(("source_uploaded", 3),),
        demucs_succeeded_count=0,
        basic_pitch_succeeded_count=0,
        adtof_succeeded_count=0,
        task_failure_count=0,
        active_task_lease_count=0,
        task_status_counts=(("demucs:queued", 3),),
        outbox_delivery_counts=(("demucs:requested:published", 3),),
    )


class LoadTestTerminalOutcomeTests(unittest.TestCase):
    """Prove only a separate, run-matched final report can satisfy the broker."""

    def test_successful_terminal_outcome_round_trips_privately(self) -> None:
        """A terminal success has an exact schema and a private atomic file."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            prepare_empty_load_test_terminal_outcome_directory(directory)
            outcome = LoadTestTerminalOutcome(_RUN_MARKER, "succeeded")

            path = write_load_test_terminal_outcome(directory, outcome=outcome)
            received = wait_for_load_test_terminal_outcome(
                directory,
                expected_run_marker=_RUN_MARKER,
                timeout_seconds=0.1,
            )

            self.assertEqual(path.name, LOAD_TEST_TERMINAL_OUTCOME_FILE_NAME)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(received, outcome)
            self.assertEqual(set(json.loads(path.read_text(encoding="utf-8"))), {
                "schema_version", "run_marker", "status", "failure_code"
            })

    def test_failed_outcome_has_one_fixed_non_sensitive_failure_category(self) -> None:
        """Failure reports cannot smuggle service response or object details."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            outcome = LoadTestTerminalOutcome(
                _RUN_MARKER,
                "failed",
                "load_test_validation_failed",
            )
            write_load_test_terminal_outcome(directory, outcome=outcome)

            self.assertEqual(
                read_load_test_terminal_outcome(directory, expected_run_marker=_RUN_MARKER),
                outcome,
            )
            with self.assertRaises(LoadTestTerminalOutcomeError):
                write_load_test_terminal_outcome(
                    directory,
                    outcome=LoadTestTerminalOutcome(
                        _RUN_MARKER,
                        "failed",
                        "postgres password or diagnostic text",
                    ),
                )

    def test_postgresql_snapshot_does_not_satisfy_terminal_waiter(self) -> None:
        """Intermediate DB evidence alone cannot return early and trigger cleanup."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            write_postgresql_observer_report(
                directory,
                report=PostgreSQLObserverReport(
                    run_marker=_RUN_MARKER,
                    status="observed",
                    snapshot=_snapshot(),
                ),
            )

            with self.assertRaisesRegex(
                LoadTestTerminalOutcomeError,
                "did not publish a terminal outcome",
            ):
                wait_for_load_test_terminal_outcome(
                    directory,
                    expected_run_marker=_RUN_MARKER,
                    timeout_seconds=0.01,
                )

    def test_rejects_an_outcome_bound_to_another_run_or_with_extra_fields(self) -> None:
        """Stale reports and open-ended JSON are not accepted as final status."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            write_load_test_terminal_outcome(
                directory,
                outcome=LoadTestTerminalOutcome(_RUN_MARKER, "succeeded"),
            )
            with self.assertRaisesRegex(LoadTestTerminalOutcomeError, "another run"):
                read_load_test_terminal_outcome(
                    directory,
                    expected_run_marker="otherrun01",
                )

            payload = json.loads(
                (directory / LOAD_TEST_TERMINAL_OUTCOME_FILE_NAME).read_text(encoding="utf-8")
            )
            payload["debug"] = "must not cross the report boundary"
            (directory / LOAD_TEST_TERMINAL_OUTCOME_FILE_NAME).write_text(
                json.dumps(payload), encoding="utf-8"
            )
            (directory / LOAD_TEST_TERMINAL_OUTCOME_FILE_NAME).chmod(0o600)
            with self.assertRaises(LoadTestTerminalOutcomeError):
                read_load_test_terminal_outcome(
                    directory,
                    expected_run_marker=_RUN_MARKER,
                )


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
