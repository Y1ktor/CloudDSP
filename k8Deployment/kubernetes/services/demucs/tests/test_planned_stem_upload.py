"""Unit tests for one exact Demucs plan-to-MinIO upload handoff.

Fake clients drain in-memory request bodies only. These tests do not execute
Demucs/Torch, contact MinIO/PostgreSQL/RabbitMQ, build an image, or interact
with Kubernetes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import tempfile
import unittest

from unittest.mock import MagicMock

from app.demucs_artifact_upload import DemucsArtifactUploadConsistencyError, UploadedDemucsStemObject
from app.demucs_artifacts import DEMUCS_STEMS_BY_MODE
from app.demucs_command import DemucsSeparationCommand
from app.executed_separation_workspace import opened_executed_demucs_separation_workspace
from app.hashed_stem_inventory_workspace import opened_hashed_demucs_stem_inventory_workspace
from app.planned_stem_upload import (
    DemucsPlannedStemUploadWorkspaceProtocolError,
    upload_demucs_stem_from_plan_workspace,
)
from app.preflight_task_start import DemucsRunningSource
from app.running_source_workspace import DemucsRunningSourceWorkspace
from app.source_preflight import ValidatedDemucsSource
from app.stem_output_plan_workspace import opened_demucs_stem_output_plan_workspace
from app.task_lease import DemucsTaskLease
from app.validated_stem_inventory_workspace import opened_validated_demucs_stem_inventory_workspace


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


class WritingStemRunner:
    """Write controlled WAV placeholders rather than invoking the Demucs CLI."""

    def run(self, *, separation: DemucsSeparationCommand, timeout_seconds: int) -> None:
        """Create the fixed expected stems under the approved output directory."""

        separation.expected_output_directory.mkdir(parents=True)
        for stem_name in DEMUCS_STEMS_BY_MODE[separation.stem_mode][1]:
            (separation.expected_output_directory / f"{stem_name}.wav").write_bytes(
                f"private-{stem_name}-wav-bytes".encode("ascii")
            )


class CapturingPutObjectClient:
    """Drain one fake S3 body and retain only test-local request evidence."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> object:
        """Simulate MinIO consuming every byte of the bounded request stream."""

        body = kwargs["Body"]
        received = bytearray()
        while chunk := body.read(7):
            received.extend(chunk)
        self.calls.append({**kwargs, "received": bytes(received)})
        return {}


def running_workspace(scratch: Path) -> DemucsRunningSourceWorkspace:
    """Create one generic source file paired with a committed current lease."""

    source_path = scratch / "demucs-source-random" / "source.media"
    source_path.parent.mkdir()
    source_path.write_bytes(b"earlier-preflight-validated-source-bytes")
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


class PlannedStemUploadTests(unittest.TestCase):
    """Prove one exact current plan can reach only its matching upload receipt."""

    def test_uploads_one_selected_plan_with_full_stream_evidence(self) -> None:
        """No sibling plan is sent and the returned receipt matches exactly."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            client = CapturingPutObjectClient()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    with opened_hashed_demucs_stem_inventory_workspace(validated) as hashed:
                        with opened_demucs_stem_output_plan_workspace(hashed) as plans:
                            selected = plans.output_objects[0]
                            receipt = upload_demucs_stem_from_plan_workspace(
                                workspace=plans,
                                output_object=selected,
                                client=client,
                            )

                            self.assertEqual(len(client.calls), 1)
                            self.assertEqual(client.calls[0]["Key"], selected.object_key)
                            self.assertEqual(client.calls[0]["received"], selected.local_path.read_bytes())
                            self.assertEqual(
                                receipt,
                                UploadedDemucsStemObject(
                                    bucket=selected.bucket,
                                    object_key=selected.object_key,
                                    content_length=selected.content_length,
                                    sha256=dict(selected.s3_metadata)["sha256"],
                                ),
                            )

    def test_cloned_plan_or_changed_bytes_cannot_be_uploaded(self) -> None:
        """The selector uses plan identity and the adapter repeats byte proof."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            client = CapturingPutObjectClient()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    with opened_hashed_demucs_stem_inventory_workspace(validated) as hashed:
                        with opened_demucs_stem_output_plan_workspace(hashed) as plans:
                            selected = plans.output_objects[0]
                            with self.assertRaises(DemucsPlannedStemUploadWorkspaceProtocolError):
                                upload_demucs_stem_from_plan_workspace(
                                    workspace=plans,
                                    output_object=replace(selected),
                                    client=client,
                                )
                            self.assertEqual(client.calls, [])

                            selected.local_path.write_bytes(b"x" * selected.content_length)
                            with self.assertRaises(DemucsArtifactUploadConsistencyError):
                                upload_demucs_stem_from_plan_workspace(
                                    workspace=plans,
                                    output_object=selected,
                                    client=client,
                                )
                            self.assertEqual(client.calls, [])

    def test_injected_receipt_must_match_the_selected_plan(self) -> None:
        """A future uploader cannot claim success for another private coordinate."""

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
                            selected = plans.output_objects[0]

                            def wrong_uploader(**_kwargs: object) -> UploadedDemucsStemObject:
                                """Return a non-matching receipt without performing I/O."""

                                return UploadedDemucsStemObject(
                                    bucket=selected.bucket,
                                    object_key="stems/not-the-reviewed-job/drums.wav",
                                    content_length=selected.content_length,
                                    sha256=hashlib.sha256(b"wrong").hexdigest(),
                                )

                            with self.assertRaises(DemucsPlannedStemUploadWorkspaceProtocolError):
                                upload_demucs_stem_from_plan_workspace(
                                    workspace=plans,
                                    output_object=selected,
                                    client=CapturingPutObjectClient(),
                                    uploader=wrong_uploader,
                                )

    def test_hand_built_workspace_never_reaches_a_storage_adapter(self) -> None:
        """No arbitrary object can choose a MinIO write coordinate."""

        with self.assertRaises(TypeError):
            upload_demucs_stem_from_plan_workspace(
                workspace=object(),  # type: ignore[arg-type]
                output_object=object(),  # type: ignore[arg-type]
                client=CapturingPutObjectClient(),
            )


if __name__ == "__main__":
    unittest.main()
