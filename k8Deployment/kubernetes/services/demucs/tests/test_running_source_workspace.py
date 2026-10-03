"""Unit tests for the guarded Demucs running-source workspace handoff.

The PostgreSQL start composition is replaced at its public boundary. These
tests create no database transaction, RabbitMQ connection, MinIO request,
FFprobe process, Demucs child, artifact, container, or Kubernetes resource.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.runtime.acknowledged_lease_preflight import DemucsAcknowledgedLeaseSourceWorkspace
from app.db.preflight_task_start import DemucsRunningSource
from app.processing.running_source_workspace import (
    DemucsRunningSourceWorkspace,
    DemucsRunningSourceWorkspaceProtocolError,
    opened_running_demucs_source_workspace,
)
from app.processing.source_preflight import ValidatedDemucsSource
from app.db.task_lease import DemucsTaskLease


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


class RecordingDatabase:
    """Expose only the transaction-shape method required by the handoff guard."""

    def write_cursor(self) -> object:
        """Never execute: the lower start composition is patched in these tests."""

        raise AssertionError("The patched start composition must own database interaction.")


def acknowledged_source_workspace() -> DemucsAcknowledgedLeaseSourceWorkspace:
    """Return one valid acknowledged source/file pairing for composition tests."""

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
    source = ValidatedDemucsSource(
        source_object=MagicMock(),
        audio_probe=MagicMock(),
    )
    return DemucsAcknowledgedLeaseSourceWorkspace(
        lease=lease,
        source=source,
        source_path=Path("/worker-scratch/demucs-source-random/source.media"),
    )


class RunningSourceWorkspaceTests(unittest.TestCase):
    """Prove no model-eligible path appears before durable lease ownership."""

    @patch("app.processing.running_source_workspace.start_preflight_validated_demucs_task")
    def test_committed_start_yields_the_same_lease_source_and_temporary_path(self, start) -> None:
        """The local bytes cannot be rebound to another durable task after start."""

        database = RecordingDatabase()
        source_workspace = acknowledged_source_workspace()
        started_at = datetime(2026, 9, 8, 12, 20, tzinfo=UTC)
        running = DemucsRunningSource(
            lease=source_workspace.lease,
            source=source_workspace.source,
            started_at=started_at,
        )
        start.return_value = running

        with opened_running_demucs_source_workspace(
            database=database,  # type: ignore[arg-type]
            workspace=source_workspace,
        ) as model_workspace:
            self.assertIsInstance(model_workspace, DemucsRunningSourceWorkspace)
            assert model_workspace is not None
            self.assertIs(model_workspace.running, running)
            self.assertEqual(model_workspace.source_path, source_workspace.source_path)
            self.assertIs(model_workspace.running.lease, source_workspace.lease)
            self.assertIs(model_workspace.running.source, source_workspace.source)

        start.assert_called_once_with(
            database=database,
            preflight=source_workspace.preflight,
        )

    @patch("app.processing.running_source_workspace.start_preflight_validated_demucs_task")
    def test_ownership_loss_yields_none_and_never_creates_model_eligibility(self, start) -> None:
        """An expired/recovered lease must leave the source scope without CPU work."""

        database = RecordingDatabase()
        source_workspace = acknowledged_source_workspace()
        start.return_value = None

        with opened_running_demucs_source_workspace(
            database=database,  # type: ignore[arg-type]
            workspace=source_workspace,
        ) as model_workspace:
            self.assertIsNone(model_workspace)

        start.assert_called_once_with(
            database=database,
            preflight=source_workspace.preflight,
        )

    @patch("app.processing.running_source_workspace.start_preflight_validated_demucs_task")
    def test_mismatched_database_handoff_is_rejected_before_a_model_path_is_yielded(self, start) -> None:
        """A mocked/alternate adapter cannot authorize another task's source bytes."""

        database = RecordingDatabase()
        source_workspace = acknowledged_source_workspace()
        different_lease = DemucsTaskLease(
            task_id="b21a7035-6ee2-495c-8f77-a2d2b8d33a8d",
            job_id=source_workspace.lease.job_id,
            request_event_id=source_workspace.lease.request_event_id,
            input_bucket=source_workspace.lease.input_bucket,
            input_object_key=source_workspace.lease.input_object_key,
            stem_mode=source_workspace.lease.stem_mode,
            attempt_count=source_workspace.lease.attempt_count,
            lease_token=source_workspace.lease.lease_token,
            lease_expires_at=source_workspace.lease.lease_expires_at,
        )
        start.return_value = DemucsRunningSource(
            lease=different_lease,
            source=source_workspace.source,
            started_at=datetime(2026, 9, 8, 12, 20, tzinfo=UTC),
        )

        with self.assertRaises(DemucsRunningSourceWorkspaceProtocolError):
            with opened_running_demucs_source_workspace(
                database=database,  # type: ignore[arg-type]
                workspace=source_workspace,
            ):
                self.fail("A mismatched running result must not yield a local source path.")


if __name__ == "__main__":
    unittest.main()
