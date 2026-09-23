"""Offline tests for the PostgreSQL observer-to-broker report handoff."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from postgresql_durable_state_observer import (
    PostgreSQLDurableStateSnapshot,
    snapshot_from_aggregate_row,
)
from test_postgresql_durable_state_observer import aggregate_row
from postgresql_observer_report import (
    POSTGRESQL_OBSERVER_REPORT_FILE_NAME,
    PostgreSQLObserverReport,
    PostgreSQLObserverReportError,
    prepare_empty_postgresql_observer_report_directory,
    read_postgresql_observer_report,
    wait_for_postgresql_observer_report,
    write_postgresql_observer_report,
)


_RUN_MARKER = "loadrun01"


def snapshot() -> PostgreSQLDurableStateSnapshot:
    """Create one typed aggregate including its private exact artifact manifest."""

    return snapshot_from_aggregate_row(aggregate_row(), run_marker=_RUN_MARKER)


class PostgreSQLObserverReportTests(unittest.TestCase):
    """The broker receives bounded aggregates, never raw work coordinates."""

    def test_round_trips_one_snapshot_through_a_private_atomic_file(self) -> None:
        """A private report carries counters and the exact object manifest."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            prepare_empty_postgresql_observer_report_directory(directory)
            report = PostgreSQLObserverReport(
                run_marker=_RUN_MARKER,
                status="observed",
                snapshot=snapshot(),
            )

            path = write_postgresql_observer_report(directory, report=report)
            received = wait_for_postgresql_observer_report(
                directory,
                expected_run_marker=_RUN_MARKER,
                timeout_seconds=0.1,
            )

            self.assertEqual(path.name, POSTGRESQL_OBSERVER_REPORT_FILE_NAME)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(received, report)
            encoded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(encoded["status"], "observed")
            self.assertEqual(encoded["snapshot"]["observed_job_count"], 3)
            self.assertIn("job_id", path.read_text(encoding="utf-8"))
            self.assertNotIn("owner_subject", path.read_text(encoding="utf-8"))
            self.assertNotIn("password", path.read_text(encoding="utf-8").lower())

    def test_round_trips_only_a_fixed_failed_observation_code(self) -> None:
        """A failure report cannot leak driver, SQL, network, or credential text."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            report = PostgreSQLObserverReport(
                run_marker=_RUN_MARKER,
                status="failed",
                snapshot=None,
                failure_code="postgresql_observation_failed",
            )
            write_postgresql_observer_report(directory, report=report)

            received = read_postgresql_observer_report(
                directory,
                expected_run_marker=_RUN_MARKER,
            )

            self.assertEqual(received, report)
            self.assertNotIn("password", repr(received).lower())
            self.assertNotIn("exception", repr(received).lower())

    def test_rejects_a_report_from_another_run(self) -> None:
        """A stale sidecar report cannot satisfy the current broker run."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            write_postgresql_observer_report(
                directory,
                report=PostgreSQLObserverReport(
                    run_marker=_RUN_MARKER,
                    status="observed",
                    snapshot=snapshot(),
                ),
            )

            with self.assertRaisesRegex(PostgreSQLObserverReportError, "another run"):
                read_postgresql_observer_report(
                    directory,
                    expected_run_marker="otherrun01",
                )

    def test_rejects_bad_snapshot_values_and_extra_schema_fields(self) -> None:
        """The report reader revalidates JSON rather than trusting the writer."""

        for invalid_snapshot in (
            {
                "observed_job_count": True,
                "source_uploaded_count": 3,
                "job_status_counts": {},
                "demucs_succeeded_count": 0,
                "basic_pitch_succeeded_count": 0,
                "adtof_succeeded_count": 0,
                "task_failure_count": 0,
                "active_task_lease_count": 0,
                "task_status_counts": {},
                "outbox_delivery_counts": {},
            },
            {
                "observed_job_count": 3,
                "source_uploaded_count": 3,
                "job_status_counts": {},
                "demucs_succeeded_count": 0,
                "basic_pitch_succeeded_count": 0,
                "adtof_succeeded_count": 0,
                "task_failure_count": 0,
                "active_task_lease_count": 0,
                "task_status_counts": {},
                "outbox_delivery_counts": {},
                "unexpected": "not permitted",
            },
        ):
            with self.subTest(snapshot_fields=set(invalid_snapshot)):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    directory = Path(temporary_directory)
                    report_path = directory / POSTGRESQL_OBSERVER_REPORT_FILE_NAME
                    payload = {
                        "schema_version": 1,
                        "run_marker": _RUN_MARKER,
                        "status": "observed",
                        "failure_code": None,
                        "snapshot": invalid_snapshot,
                    }
                    report_path.write_text(json.dumps(payload), encoding="utf-8")
                    report_path.chmod(0o600)

                    with self.assertRaises(PostgreSQLObserverReportError):
                        read_postgresql_observer_report(
                            directory,
                            expected_run_marker=_RUN_MARKER,
                        )

    def test_writer_refuses_a_second_report_instead_of_overwriting_evidence(self) -> None:
        """One observer attempt has one atomic terminal report file."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            report = PostgreSQLObserverReport(
                run_marker=_RUN_MARKER,
                status="observed",
                snapshot=snapshot(),
            )
            path = write_postgresql_observer_report(directory, report=report)

            with self.assertRaisesRegex(PostgreSQLObserverReportError, "already existed"):
                write_postgresql_observer_report(directory, report=report)
            self.assertTrue(path.exists())

    def test_rejects_an_unsafe_or_stale_report_directory(self) -> None:
        """The preflight requires a private emptyDir with no prior report."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            prepare_empty_postgresql_observer_report_directory(directory)
            report_path = directory / POSTGRESQL_OBSERVER_REPORT_FILE_NAME
            report_path.write_text("stale", encoding="utf-8")
            report_path.chmod(0o600)

            with self.assertRaisesRegex(PostgreSQLObserverReportError, "stale data"):
                prepare_empty_postgresql_observer_report_directory(directory)


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
