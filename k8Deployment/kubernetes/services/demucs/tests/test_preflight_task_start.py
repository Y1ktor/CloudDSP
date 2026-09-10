"""Unit tests for the committed preflight-to-running Demucs handoff.

The SQL decision is patched at its public boundary. These tests prove only the
surrounding transaction ordering; they do not connect to PostgreSQL, RabbitMQ,
MinIO, FFprobe, a Demucs model, Docker, or Kubernetes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import unittest
from unittest.mock import MagicMock, patch

from app.acknowledged_lease_preflight import DemucsAcknowledgedLeasePreflight
from app.postgresql import DemucsDatabaseUnavailable
from app.preflight_task_start import start_preflight_validated_demucs_task
from app.source_preflight import ValidatedDemucsSource
from app.task_lease import DemucsTaskLease, DemucsTaskLeaseProtocolError


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


class RecordingDatabase:
    """Expose one fake transaction and record when it commits or rolls back."""

    def __init__(self) -> None:
        self.cursor = object()
        self.events: list[str] = []

    @contextmanager
    def write_cursor(self) -> Iterator[object]:
        """Mirror the concrete adapter's normal commit/exception rollback scope."""

        self.events.append("transaction-open")
        try:
            yield self.cursor
        except Exception:
            self.events.append("transaction-rollback")
            raise
        else:
            self.events.append("transaction-commit")


class UnavailableDatabase:
    """Fail before yielding a cursor, as an unavailable PostgreSQL Service does."""

    @contextmanager
    def write_cursor(self) -> Iterator[object]:
        """Expose the normal safe outage category without a driver connection."""

        raise DemucsDatabaseUnavailable("PostgreSQL Demucs task access is unavailable.")
        yield object()  # pragma: no cover - satisfies generator typing only.


def preflight() -> DemucsAcknowledgedLeasePreflight:
    """Return acknowledged source evidence paired with one known task lease."""

    lease = DemucsTaskLease(
        task_id="c21a7035-6ee2-495c-8f77-a2d2b8d33a8d",
        job_id=JOB_ID,
        request_event_id="93b31df9-ea8c-46bb-b2c0-19e9db5365d5",
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token="49d78c86-9591-4fcb-85d8-694c15808a65",
        lease_expires_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    return DemucsAcknowledgedLeasePreflight(
        lease=lease,
        source=MagicMock(spec=ValidatedDemucsSource),
    )


class PreflightTaskStartCompositionTests(unittest.TestCase):
    """Prove model eligibility exists only after the task-start transaction ends."""

    @patch("app.preflight_task_start.start_leased_demucs_task")
    def test_committed_start_retains_exact_lease_and_source_evidence(self, start) -> None:
        """A model boundary receives durable ownership, not a live database cursor."""

        database = RecordingDatabase()
        validated = preflight()
        started_at = datetime(2026, 9, 8, 12, 20, tzinfo=UTC)
        start.return_value = started_at

        running = start_preflight_validated_demucs_task(
            database=database,  # type: ignore[arg-type]
            preflight=validated,
        )

        self.assertIsNotNone(running)
        assert running is not None
        self.assertIs(running.lease, validated.lease)
        self.assertIs(running.source, validated.source)
        self.assertEqual(running.started_at, started_at)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        start.assert_called_once_with(database.cursor, lease=validated.lease)

    @patch("app.preflight_task_start.start_leased_demucs_task")
    def test_ownership_loss_commits_without_exposing_model_eligible_evidence(self, start) -> None:
        """A recovered or expired task must stop without another side effect here."""

        database = RecordingDatabase()
        start.return_value = None

        running = start_preflight_validated_demucs_task(
            database=database,  # type: ignore[arg-type]
            preflight=preflight(),
        )

        self.assertIsNone(running)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])

    @patch("app.preflight_task_start.start_leased_demucs_task")
    def test_invalid_task_start_rolls_back_instead_of_permitting_model_work(self, start) -> None:
        """A protocol error cannot become a partial committed running transition."""

        database = RecordingDatabase()
        start.side_effect = DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")

        with self.assertRaises(DemucsTaskLeaseProtocolError):
            start_preflight_validated_demucs_task(
                database=database,  # type: ignore[arg-type]
                preflight=preflight(),
            )

        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])

    @patch("app.preflight_task_start.start_leased_demucs_task")
    def test_database_outage_prevents_the_pure_start_statement(self, start) -> None:
        """No source evidence is promoted when the transaction cannot open."""

        with self.assertRaises(DemucsDatabaseUnavailable):
            start_preflight_validated_demucs_task(
                database=UnavailableDatabase(),  # type: ignore[arg-type]
                preflight=preflight(),
            )

        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
