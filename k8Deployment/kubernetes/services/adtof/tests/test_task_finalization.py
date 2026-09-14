"""Unit tests for ADTOF's bounded post-inference finalization composition.

All lower I/O and database boundaries are patched. Tests create only temporary
fake local artifacts; they never contact MinIO/PostgreSQL/RabbitMQ or Kubernetes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.local_task_execution import execute_running_adtof_local_task
from app.minio_upload import ADTOFUploadUnavailable
from app.task_completion import ADTOFTaskCompletion
from app.task_finalization import finalize_running_adtof_task
from test_upload_object import FakeRunner, running_stem


def local_outputs(work_directory: Path):
    """Create one complete local result inside a temporary running-stem scope."""

    stem_directory = work_directory / "adtof-stem-example"
    stem_directory.mkdir()
    source_path = stem_directory / "stem.wav"
    source_path.write_bytes(b"input belongs to the earlier verified boundary")
    return execute_running_adtof_local_task(
        running=running_stem(source_path),
        work_directory=work_directory,
        process_runner=FakeRunner(),  # type: ignore[arg-type]
    )


class ADTOFTaskFinalizationTests(unittest.TestCase):
    """Prove post-inference work reaches commit only after both storage proofs."""

    @patch("app.task_finalization.commit_verified_adtof_task")
    @patch("app.task_finalization.verify_uploaded_adtof_output_head_objects")
    @patch("app.task_finalization.upload_adtof_objects")
    @patch("app.task_finalization.build_adtof_upload_objects")
    def test_runs_upload_then_headobject_then_committed_completion(
        self,
        build_plans,
        upload,
        verify,
        commit,
    ) -> None:
        """A success value cannot bypass either upload or stored-object evidence."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            outputs = local_outputs(Path(temporary_directory))
            database = MagicMock()
            storage_client = MagicMock()
            plans = object()
            receipts = object()
            stored = object()
            completion = ADTOFTaskCompletion(
                task_id=outputs.running.lease.task_id,
                job_id=outputs.running.lease.job_id,
                completed_at=datetime(2026, 9, 13, 12, 30, tzinfo=UTC),
                resulting_revision=8,
            )
            build_plans.return_value = plans
            upload.return_value = receipts
            verify.return_value = stored
            commit.return_value = completion

            returned = finalize_running_adtof_task(
                database=database,
                storage_client=storage_client,
                local_outputs=outputs,
            )

            self.assertIs(returned, completion)
            build_plans.assert_called_once_with(local_outputs=outputs)
            upload.assert_called_once_with(client=storage_client, upload_objects=plans)
            verify.assert_called_once_with(
                storage_client,
                upload_objects=plans,
                upload_receipts=receipts,
            )
            commit.assert_called_once_with(
                database=database,
                lease=outputs.running.lease,
                stored_outputs=stored,
                tempo_candidate=outputs.tempo_candidate,
            )

    @patch("app.task_finalization.commit_verified_adtof_task")
    @patch("app.task_finalization.verify_uploaded_adtof_output_head_objects")
    @patch("app.task_finalization.upload_adtof_objects")
    @patch("app.task_finalization.build_adtof_upload_objects")
    def test_upload_failure_stops_before_storage_proof_or_completion(
        self,
        build_plans,
        upload,
        verify,
        commit,
    ) -> None:
        """The later worker supervisor receives the real failure unmodified."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            outputs = local_outputs(Path(temporary_directory))
            build_plans.return_value = object()
            upload.side_effect = ADTOFUploadUnavailable("ADTOF MinIO upload is unavailable.")

            with self.assertRaises(ADTOFUploadUnavailable):
                finalize_running_adtof_task(
                    database=MagicMock(),
                    storage_client=MagicMock(),
                    local_outputs=outputs,
                )
            verify.assert_not_called()
            commit.assert_not_called()

    @patch("app.task_finalization.commit_verified_adtof_task")
    @patch("app.task_finalization.verify_uploaded_adtof_output_head_objects")
    @patch("app.task_finalization.upload_adtof_objects")
    @patch("app.task_finalization.build_adtof_upload_objects")
    def test_final_ownership_loss_returns_none_after_both_storage_boundaries(
        self,
        build_plans,
        upload,
        verify,
        commit,
    ) -> None:
        """Stale ownership never becomes success or a broker acknowledgement hint."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            outputs = local_outputs(Path(temporary_directory))
            plans = object()
            receipts = object()
            stored = object()
            build_plans.return_value = plans
            upload.return_value = receipts
            verify.return_value = stored
            commit.return_value = None
            database = MagicMock()
            storage_client = MagicMock()

            self.assertIsNone(
                finalize_running_adtof_task(
                    database=database,
                    storage_client=storage_client,
                    local_outputs=outputs,
                )
            )
            verify.assert_called_once_with(
                storage_client,
                upload_objects=plans,
                upload_receipts=receipts,
            )
            commit.assert_called_once_with(
                database=database,
                lease=outputs.running.lease,
                stored_outputs=stored,
                tempo_candidate=outputs.tempo_candidate,
            )


if __name__ == "__main__":
    unittest.main()
