"""Unit tests for the fixed-function ADTOF worker-smoke PostgreSQL adapter.

The fake cursor/connection runs entirely in memory. Tests import no Psycopg,
open no socket, and create no durable PostgreSQL Job/outbox/task or MinIO/Rabbit
artifact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from uuid import UUID, uuid4

from adtof_worker_smoke_contract import ADTOFWorkerSmokeContractError
from adtof_worker_smoke_database import (
    CLEANUP_FIXED_SUCCESS_SQL,
    OBSERVE_FIXED_EVENT_SQL,
    PREPARE_FIXED_EVENT_SQL,
    ADTOFWorkerSmokeDatabaseInfrastructureError,
    ADTOFWorkerSmokeDatabaseProtocolError,
    FixedFunctionADTOFWorkerSmokeDatabaseAdapter,
)
from adtof_worker_smoke_fixture import SMOKE_EVENT_ID, SMOKE_JOB_ID


class FakeCursor:
    """Record fixed SQL/parameter calls and return one configured mapping row."""

    def __init__(self, row: object, *, execute_error: BaseException | None = None) -> None:
        self.row = row
        self.execute_error = execute_error
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *arguments: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.calls.append((query, params))
        if self.execute_error is not None:
            raise self.execute_error

    def fetchone(self) -> object:
        return self.row


class FakeConnection:
    """Expose transaction/close counters without any database transport."""

    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.close_count += 1


def _adapter(connection: FakeConnection) -> FixedFunctionADTOFWorkerSmokeDatabaseAdapter:
    """Inject exactly one in-memory connection into an adapter under test."""

    return FixedFunctionADTOFWorkerSmokeDatabaseAdapter(lambda: connection)


def _observation_row(**overrides: object) -> dict[str, object]:
    """Return the exact safe `observe()` projection for one completed task."""

    now = datetime(2026, 9, 14, tzinfo=UTC)
    row: dict[str, object] = {
        "publication_status": "published",
        "published_at": now,
        "task_id": str(uuid4()),
        "task_status": "succeeded",
        "task_attempt_count": 1,
        "task_lease_is_clear": True,
        "task_completed_at": now,
        "job_status": "failed_incomplete_stem_fixture",
    }
    row.update(overrides)
    return row


class FixedFunctionADTOFWorkerSmokeDatabaseAdapterTests(unittest.TestCase):
    """Prove this adapter remains a narrow short-transaction function caller."""

    def test_prepare_binds_only_evidence_and_commits_the_exact_fixed_pair(self) -> None:
        """The function, not client code, owns the atomic Job/outbox insertion."""

        cursor = FakeCursor({"smoke_job_id": SMOKE_JOB_ID, "smoke_event_id": SMOKE_EVENT_ID})
        connection = FakeConnection(cursor)

        prepared = _adapter(connection).prepare_fixed_event(
            stem_size_bytes=705_644,
            stem_sha256="a" * 64,
        )

        self.assertEqual(prepared.job_id, SMOKE_JOB_ID)
        self.assertEqual(prepared.event_id, SMOKE_EVENT_ID)
        self.assertEqual(cursor.calls, [(PREPARE_FIXED_EVENT_SQL, (705_644, "a" * 64))])
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertEqual(connection.close_count, 1)

    def test_prepare_rejects_forged_evidence_or_row_and_rolls_back(self) -> None:
        """No bad evidence/result can produce a committed durable client outcome."""

        invalid_input = FakeConnection(FakeCursor({"smoke_job_id": SMOKE_JOB_ID, "smoke_event_id": SMOKE_EVENT_ID}))
        with self.assertRaises(ADTOFWorkerSmokeContractError):
            _adapter(invalid_input).prepare_fixed_event(stem_size_bytes=0, stem_sha256="a" * 64)
        self.assertEqual(invalid_input._cursor.calls, [])
        self.assertEqual(invalid_input.close_count, 0)

        invalid_row = FakeConnection(FakeCursor({"smoke_job_id": str(uuid4()), "smoke_event_id": SMOKE_EVENT_ID}))
        with self.assertRaises(ADTOFWorkerSmokeContractError):
            _adapter(invalid_row).prepare_fixed_event(stem_size_bytes=1, stem_sha256="a" * 64)
        self.assertEqual(invalid_row.commit_count, 0)
        self.assertEqual(invalid_row.rollback_count, 1)
        self.assertEqual(invalid_row.close_count, 1)

    def test_observe_maps_only_the_safe_projection_then_ends_its_read_transaction(self) -> None:
        """Polling never commits and cannot fetch a raw Job/outbox payload."""

        cursor = FakeCursor(_observation_row())
        connection = FakeConnection(cursor)

        observation = _adapter(connection).observe_fixed_event()

        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertTrue(observation.is_successful_first_attempt)
        self.assertEqual(cursor.calls, [(OBSERVE_FIXED_EVENT_SQL, ())])
        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(connection.close_count, 1)

    def test_observe_normalizes_psycopg_native_uuid_before_contract_validation(self) -> None:
        """The live driver decodes PostgreSQL UUID columns instead of returning text."""

        native_task_id = uuid4()
        connection = FakeConnection(FakeCursor(_observation_row(task_id=native_task_id)))

        observation = _adapter(connection).observe_fixed_event()

        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(observation.task_id, str(native_task_id))
        self.assertIsInstance(UUID(observation.task_id), UUID)
        self.assertTrue(observation.is_successful_first_attempt)
        self.assertEqual(connection.rollback_count, 1)

    def test_observe_accepts_no_row_but_rejects_extra_or_malformed_database_fields(self) -> None:
        """Absence is normal before prepare; arbitrary data never crosses the boundary."""

        empty_connection = FakeConnection(FakeCursor(None))
        self.assertIsNone(_adapter(empty_connection).observe_fixed_event())
        self.assertEqual(empty_connection.rollback_count, 1)

        bad_connection = FakeConnection(FakeCursor({**_observation_row(), "payload": {}}))
        with self.assertRaises(ADTOFWorkerSmokeDatabaseProtocolError):
            _adapter(bad_connection).observe_fixed_event()
        self.assertEqual(bad_connection.commit_count, 0)
        self.assertEqual(bad_connection.rollback_count, 1)

    def test_cleanup_commits_only_a_true_successful_deletion_and_rolls_back_false(self) -> None:
        """The client cannot pretend a false guarded cleanup deleted test evidence."""

        deleted_cursor = FakeCursor({"cleaned": True})
        deleted_connection = FakeConnection(deleted_cursor)
        self.assertTrue(_adapter(deleted_connection).cleanup_fixed_success())
        self.assertEqual(deleted_cursor.calls, [(CLEANUP_FIXED_SUCCESS_SQL, ())])
        self.assertEqual(deleted_connection.commit_count, 1)
        self.assertEqual(deleted_connection.rollback_count, 0)

        retained_connection = FakeConnection(FakeCursor({"cleaned": False}))
        self.assertFalse(_adapter(retained_connection).cleanup_fixed_success())
        self.assertEqual(retained_connection.commit_count, 0)
        self.assertEqual(retained_connection.rollback_count, 1)

    def test_driver_failures_are_redacted_and_this_module_never_uses_table_sql(self) -> None:
        """Only infrastructure categories leave this adapter; no raw driver string leaks."""

        unavailable_connection = FakeConnection(FakeCursor(None, execute_error=RuntimeError("private host")))
        with self.assertRaises(ADTOFWorkerSmokeDatabaseInfrastructureError) as raised:
            _adapter(unavailable_connection).observe_fixed_event()
        self.assertEqual(str(raised.exception), "Smoke database observation is unavailable.")
        self.assertEqual(unavailable_connection.rollback_count, 1)
        self.assertEqual(unavailable_connection.close_count, 1)

        source = (Path(__file__).parent / "adtof_worker_smoke_database.py").read_text(
            encoding="utf-8"
        )
        for forbidden_fragment in (
            "import psycopg",
            "public.jobs",
            "public.outbox_events",
            "public.processing_tasks",
            "SELECT * FROM public.",
        ):
            self.assertNotIn(forbidden_fragment, source)


if __name__ == "__main__":
    unittest.main()
