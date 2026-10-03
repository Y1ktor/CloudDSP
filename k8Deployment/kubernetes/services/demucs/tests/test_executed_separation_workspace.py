"""Unit tests for the committed-running-workspace Demucs process handoff.

All tests inject a small process runner. They never execute Demucs/Torch,
inspect or upload stems, contact PostgreSQL/MinIO/RabbitMQ, build an image, or
use a Kubernetes resource.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.processing.demucs_command import DemucsCommandPathError, DemucsSeparationCommand
from app.processing.demucs_process import DemucsLeaseRenewalOwnershipLost, DemucsProcessFailed
from app.processing.executed_separation_workspace import (
    DemucsExecutedSeparationWorkspace,
    opened_executed_demucs_separation_workspace,
)
from app.db.preflight_task_start import DemucsRunningSource
from app.db.running_lease_renewal import (
    DemucsRunningLeaseRenewal,
    DemucsRunningLeaseRenewalOutcome,
)
from app.processing.running_source_workspace import DemucsRunningSourceWorkspace
from app.processing.source_preflight import ValidatedDemucsSource
from app.db.task_lease import DemucsTaskLease


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


class RecordingRunner:
    """Record an approved command without starting a child process."""

    def __init__(self, failure: BaseException | None = None) -> None:
        """Optionally reproduce one safe process-adapter failure."""

        self.failure = failure
        self.calls: list[tuple[DemucsSeparationCommand, int]] = []

    def run(self, *, separation: DemucsSeparationCommand, timeout_seconds: int) -> None:
        """Keep only local test evidence and never create output stems."""

        self.calls.append((separation, timeout_seconds))
        if self.failure is not None:
            raise self.failure


class RenewalAwareRecordingRunner:
    """Model a cancellable runner that reaches one renewal checkpoint safely."""

    def __init__(self) -> None:
        """Record the child request and whether its checkpoint allowed it onward."""

        self.calls: list[tuple[DemucsSeparationCommand, int, int, bool]] = []

    def run_with_lease_renewal(
        self,
        *,
        separation: DemucsSeparationCommand,
        timeout_seconds: int,
        renewal_interval_seconds: int,
        renewal_checkpoint,
    ) -> None:  # type: ignore[no-untyped-def]
        """Invoke one callback as the production poller would at its first tick."""

        keep_running = renewal_checkpoint()
        self.calls.append(
            (
                separation,
                timeout_seconds,
                renewal_interval_seconds,
                keep_running,
            )
        )
        if not keep_running:
            # The production runner first stops the real process group; this
            # fake has no child, so it exposes the same post-stop signal only.
            raise DemucsLeaseRenewalOwnershipLost("Demucs task lease ownership was lost.")


def running_workspace(scratch: Path, *, stem_mode: str = "4-stems") -> DemucsRunningSourceWorkspace:
    """Create one real generic source path paired with committed fake evidence."""

    source_path = scratch / "demucs-source-random" / "source.media"
    source_path.parent.mkdir()
    source_path.write_bytes(b"the-earlier-preflight-boundaries-validated-these-bytes")
    lease = DemucsTaskLease(
        task_id="c21a7035-6ee2-495c-8f77-a2d2b8d33a8d",
        job_id=JOB_ID,
        request_event_id="93b31df9-ea8c-46bb-b2c0-19e9db5365d5",
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode=stem_mode,
        attempt_count=1,
        lease_token="49d78c86-9591-4fcb-85d8-694c15808a65",
        lease_expires_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    return DemucsRunningSourceWorkspace(
        running=DemucsRunningSource(
            lease=lease,
            source=ValidatedDemucsSource(
                source_object=MagicMock(),
                audio_probe=MagicMock(),
            ),
            started_at=datetime(2026, 9, 8, 12, 20, tzinfo=UTC),
        ),
        source_path=source_path,
    )


class ExecutedSeparationWorkspaceTests(unittest.TestCase):
    """Prove only a current running source can reach one bounded CPU runner."""

    def test_running_workspace_builds_and_runs_one_fixed_command_then_cleans_output(self) -> None:
        """The output remains available solely for the next in-scope verifier."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            workspace = running_workspace(scratch)
            runner = RecordingRunner()

            with opened_executed_demucs_separation_workspace(
                workspace,
                work_directory=scratch,
                timeout_seconds=60,
                runner=runner,
            ) as executed:
                self.assertIsInstance(executed, DemucsExecutedSeparationWorkspace)
                self.assertIs(executed.running_workspace, workspace)
                self.assertIs(executed.running, workspace.running)
                self.assertEqual(executed.separation.stem_mode, "4-stems")
                self.assertEqual(executed.separation.model_name, "htdemucs")
                self.assertEqual(executed.separation.source_path, workspace.source_path.resolve())
                self.assertTrue(executed.separation.output_directory.is_dir())
                self.assertEqual(runner.calls, [(executed.separation, 60)])
                output_directory = executed.separation.output_directory

            # The following artifact-validation/upload task must run inside the
            # context above. It cannot accidentally reuse another task's output.
            self.assertFalse(output_directory.exists())
            self.assertEqual(sorted(path.name for path in scratch.iterdir()), ["demucs-source-random"])

    def test_lease_stem_mode_selects_the_fixed_model_without_caller_model_input(self) -> None:
        """Only durable task mode, not a browser/model argument, selects six stems."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            workspace = running_workspace(scratch, stem_mode="6-stems")
            runner = RecordingRunner()

            with opened_executed_demucs_separation_workspace(
                workspace,
                work_directory=scratch,
                runner=runner,
            ) as executed:
                self.assertEqual(executed.separation.model_name, "htdemucs_6s")
                self.assertIn("--device", executed.separation.command)
                self.assertIn("cpu", executed.separation.command)
                self.assertEqual(len(runner.calls), 1)

    def test_missing_source_fails_before_runner_and_cleans_the_new_output_directory(self) -> None:
        """A source that left its outer context cannot cause a model invocation."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            workspace = running_workspace(scratch)
            workspace.source_path.unlink()
            runner = RecordingRunner()

            with self.assertRaises(DemucsCommandPathError):
                with opened_executed_demucs_separation_workspace(
                    workspace,
                    work_directory=scratch,
                    runner=runner,
                ):
                    self.fail("A missing temporary source must not yield an executed separation.")

            self.assertEqual(runner.calls, [])
            self.assertEqual(sorted(path.name for path in scratch.iterdir()), ["demucs-source-random"])

    def test_process_failure_propagates_and_cleans_private_output_without_uploading(self) -> None:
        """A failed child produces no usable workspace and no later artifact action."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            workspace = running_workspace(scratch)
            runner = RecordingRunner(DemucsProcessFailed("Demucs process failed."))

            with self.assertRaises(DemucsProcessFailed):
                with opened_executed_demucs_separation_workspace(
                    workspace,
                    work_directory=scratch,
                    runner=runner,
                ):
                    self.fail("A failed process must not yield an executed separation.")

            self.assertEqual(len(runner.calls), 1)
            self.assertEqual(sorted(path.name for path in scratch.iterdir()), ["demucs-source-random"])

    @patch("app.processing.executed_separation_workspace.renew_running_demucs_lease")
    def test_renewal_checkpoint_refreshes_the_running_lease_used_downstream(self, renew) -> None:
        """A live child carries only PostgreSQL's refreshed expiry past this scope."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            workspace = running_workspace(scratch)
            renewed_expiry = datetime(2026, 9, 8, 12, 30, tzinfo=UTC)
            renewed_running = replace(
                workspace.running,
                lease=replace(workspace.running.lease, lease_expires_at=renewed_expiry),
            )
            renew.return_value = DemucsRunningLeaseRenewal(
                outcome=DemucsRunningLeaseRenewalOutcome.RENEWED,
                running=renewed_running,
            )
            runner = RenewalAwareRecordingRunner()
            database = MagicMock()

            with opened_executed_demucs_separation_workspace(
                workspace,
                work_directory=scratch,
                timeout_seconds=60,
                runner=runner,  # type: ignore[arg-type]
                renewal_database=database,  # type: ignore[arg-type]
            ) as executed:
                self.assertEqual(executed.running.lease.lease_expires_at, renewed_expiry)
                self.assertEqual(executed.running.lease.task_id, workspace.running.lease.task_id)
                self.assertIs(executed.running.source, workspace.running.source)

            self.assertEqual(runner.calls[0][1:], (60, 60, True))
            renew.assert_called_once_with(database=database, running=workspace.running)

    @patch("app.processing.executed_separation_workspace.renew_running_demucs_lease")
    def test_renewal_ownership_loss_yields_no_workspace_and_cleans_output(self, renew) -> None:
        """The child-stop signal cannot reach local stem validation or uploads."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            workspace = running_workspace(scratch)
            renew.return_value = DemucsRunningLeaseRenewal(
                outcome=DemucsRunningLeaseRenewalOutcome.OWNERSHIP_LOST,
            )
            runner = RenewalAwareRecordingRunner()

            with self.assertRaises(DemucsLeaseRenewalOwnershipLost):
                with opened_executed_demucs_separation_workspace(
                    workspace,
                    work_directory=scratch,
                    runner=runner,  # type: ignore[arg-type]
                    renewal_database=MagicMock(),  # type: ignore[arg-type]
                ):
                    self.fail("A lost lease must not yield executed output evidence.")

            self.assertEqual(runner.calls[0][3], False)
            self.assertEqual(sorted(path.name for path in scratch.iterdir()), ["demucs-source-random"])


if __name__ == "__main__":
    unittest.main()
