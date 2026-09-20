"""Unit tests for private Demucs object plans within an open hashed workspace.

The fake runner writes small local placeholders only. These tests do not run
Demucs/Torch, call MinIO, upload a stem, alter PostgreSQL, contact RabbitMQ,
build an image, or interact with Kubernetes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.demucs_artifacts import DEMUCS_STEMS_BY_MODE
from app.demucs_command import DemucsSeparationCommand
from app.demucs_output_object import DemucsOutputObjectContractError
from app.executed_separation_workspace import opened_executed_demucs_separation_workspace
from app.hashed_stem_inventory_workspace import opened_hashed_demucs_stem_inventory_workspace
from app.preflight_task_start import DemucsRunningSource
from app.running_source_workspace import DemucsRunningSourceWorkspace
from app.source_preflight import ValidatedDemucsSource
from app.stem_output_plan_workspace import (
    DemucsStemOutputPlanWorkspace,
    DemucsStemOutputPlanWorkspaceProtocolError,
    opened_demucs_stem_output_plan_workspace,
)
from app.task_lease import DemucsTaskLease
from app.validated_stem_inventory_workspace import opened_validated_demucs_stem_inventory_workspace


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"


class WritingStemRunner:
    """Create controlled local WAV names instead of starting the Demucs binary."""

    def run(self, *, separation: DemucsSeparationCommand, timeout_seconds: int) -> None:
        """Write every reviewed four-stem artifact into the deterministic tree."""

        separation.expected_output_directory.mkdir(parents=True)
        for stem_name in DEMUCS_STEMS_BY_MODE[separation.stem_mode][1]:
            (separation.expected_output_directory / f"{stem_name}.wav").write_bytes(
                f"private-{stem_name}-wav-bytes".encode("ascii")
            )


def running_workspace(scratch: Path) -> DemucsRunningSourceWorkspace:
    """Create one temporary source plus its committed running lease identity."""

    source_path = scratch / "demucs-source-random" / "source.media"
    source_path.parent.mkdir()
    source_path.write_bytes(b"earlier-preflight-validated-source-bytes")
    lease = DemucsTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        request_event_id="93b31df9-ea8c-46bb-b2c0-19e9db5365d5",
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
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


class StemOutputPlanWorkspaceTests(unittest.TestCase):
    """Prove only current in-scope hash evidence can name private object plans."""

    def test_builds_complete_stable_private_plans_before_outer_cleanup(self) -> None:
        """Every plan keeps its corresponding local digest evidence and stable key."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    with opened_hashed_demucs_stem_inventory_workspace(validated) as hashed:
                        with opened_demucs_stem_output_plan_workspace(hashed) as plans:
                            self.assertIsInstance(plans, DemucsStemOutputPlanWorkspace)
                            self.assertIs(plans.hashed_workspace, hashed)
                            self.assertIs(plans.running, executed.running)
                            self.assertEqual(
                                tuple(item.object_key for item in plans.output_objects),
                                tuple(
                                    f"stems/{JOB_ID}/{stem_name}.wav"
                                    for stem_name in DEMUCS_STEMS_BY_MODE["4-stems"][1]
                                ),
                            )
                            for output_object, artifact in zip(
                                plans.output_objects,
                                hashed.hashed_inventory.artifacts,
                                strict=True,
                            ):
                                self.assertEqual(output_object.local_path, artifact.path)
                                self.assertEqual(output_object.content_length, artifact.size_bytes)
                                self.assertIn(("sha256", artifact.sha256), output_object.s3_metadata)
                            output_directory = executed.separation.output_directory

                        self.assertTrue(output_directory.is_dir())

            self.assertFalse(output_directory.exists())

    def test_changed_file_after_hashing_is_rejected_before_plans_are_yielded(self) -> None:
        """The object planner repeats the local hash proof before naming a key."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                output_directory = executed.separation.output_directory
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    with opened_hashed_demucs_stem_inventory_workspace(validated) as hashed:
                        first = hashed.hashed_inventory.artifacts[0]
                        first.path.write_bytes(b"x" * first.size_bytes)
                        with self.assertRaises(DemucsOutputObjectContractError):
                            with opened_demucs_stem_output_plan_workspace(hashed):
                                self.fail("Changed local bytes must not obtain an upload plan.")

            self.assertFalse(output_directory.exists())

    def test_hand_built_workspace_never_reaches_object_plan_construction(self) -> None:
        """A caller cannot name an object without prior current hash evidence."""

        with self.assertRaises(TypeError):
            with opened_demucs_stem_output_plan_workspace(object()):  # type: ignore[arg-type]
                self.fail("An arbitrary object must not become a MinIO plan workspace.")

    def test_substituted_plan_result_is_rejected(self) -> None:
        """A replacement planner cannot weaken the exact plan/evidence pairing."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    with opened_hashed_demucs_stem_inventory_workspace(validated) as hashed:
                        with patch(
                            "app.stem_output_plan_workspace.build_demucs_stem_output_objects",
                            return_value=(),
                        ):
                            with self.assertRaises(DemucsStemOutputPlanWorkspaceProtocolError):
                                with opened_demucs_stem_output_plan_workspace(hashed):
                                    self.fail("A partial planner result must not become object-plan evidence.")


if __name__ == "__main__":
    unittest.main()
