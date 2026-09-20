"""Unit tests for sequential complete Demucs stem uploads from fixed plans.

Fake clients consume local request bodies only. Tests never run Demucs/Torch,
connect to MinIO/PostgreSQL/RabbitMQ, build an image, or use Kubernetes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from unittest.mock import MagicMock

from app.complete_stem_upload import upload_complete_demucs_stem_set_from_plan_workspace
from app.demucs_artifact_upload import DemucsArtifactUploadUnavailable
from app.demucs_artifacts import DEMUCS_STEMS_BY_MODE
from app.demucs_command import DemucsSeparationCommand
from app.executed_separation_workspace import opened_executed_demucs_separation_workspace
from app.hashed_stem_inventory_workspace import opened_hashed_demucs_stem_inventory_workspace
from app.preflight_task_start import DemucsRunningSource
from app.running_source_workspace import DemucsRunningSourceWorkspace
from app.source_preflight import ValidatedDemucsSource
from app.stem_output_plan_workspace import opened_demucs_stem_output_plan_workspace
from app.task_lease import DemucsTaskLease
from app.validated_stem_inventory_workspace import opened_validated_demucs_stem_inventory_workspace


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


class WritingStemRunner:
    """Create the fixed local WAV tree without launching a model process."""

    def run(self, *, separation: DemucsSeparationCommand, timeout_seconds: int) -> None:
        """Write all expected stem names below Demucs's deterministic output path."""

        separation.expected_output_directory.mkdir(parents=True)
        for stem_name in DEMUCS_STEMS_BY_MODE[separation.stem_mode][1]:
            (separation.expected_output_directory / f"{stem_name}.wav").write_bytes(
                f"private-{stem_name}-wav-bytes".encode("ascii")
            )


class RecordingPutObjectClient:
    """Drain complete fake S3 bodies and capture their test-only key ordering."""

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        """Optionally reproduce a transport failure on one one-based call number."""

        self.fail_on_call = fail_on_call
        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> object:
        """Read prior calls fully; fail the selected call without leaking diagnostics."""

        call_number = len(self.calls) + 1
        body = kwargs["Body"]
        if self.fail_on_call == call_number:
            self.calls.append({**kwargs, "failed": True})
            raise RuntimeError("private storage details must not escape")
        received = bytearray()
        while chunk := body.read(9):
            received.extend(chunk)
        self.calls.append({**kwargs, "received": bytes(received)})
        return {}


def running_workspace(scratch: Path) -> DemucsRunningSourceWorkspace:
    """Create a source and committed lease needed by the nested local handoffs."""

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


class CompleteStemUploadTests(unittest.TestCase):
    """Prove a full receipt set exists only after every ordered upload succeeds."""

    def test_uploads_every_fixed_plan_in_order_and_returns_complete_receipts(self) -> None:
        """A later database step receives all four receipts, never a partial list."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            client = RecordingPutObjectClient()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    with opened_hashed_demucs_stem_inventory_workspace(validated) as hashed:
                        with opened_demucs_stem_output_plan_workspace(hashed) as plans:
                            published = upload_complete_demucs_stem_set_from_plan_workspace(
                                workspace=plans,
                                client=client,
                            )

                            expected_keys = tuple(
                                f"stems/{JOB_ID}/{stem_name}.wav"
                                for stem_name in DEMUCS_STEMS_BY_MODE["4-stems"][1]
                            )
                            self.assertIs(published.lease, plans.running.lease)
                            self.assertEqual(
                                tuple(receipt.object_key for receipt in published.uploads),
                                expected_keys,
                            )
                            self.assertEqual(
                                tuple(call["Key"] for call in client.calls),
                                expected_keys,
                            )
                            self.assertEqual(len(client.calls), len(expected_keys))

    def test_later_upload_failure_returns_no_complete_receipt_set_or_durable_success(self) -> None:
        """Earlier private objects can exist, but later plans stop immediately."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            client = RecordingPutObjectClient(fail_on_call=2)

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    with opened_hashed_demucs_stem_inventory_workspace(validated) as hashed:
                        with opened_demucs_stem_output_plan_workspace(hashed) as plans:
                            with self.assertRaises(DemucsArtifactUploadUnavailable):
                                upload_complete_demucs_stem_set_from_plan_workspace(
                                    workspace=plans,
                                    client=client,
                                )

                            self.assertEqual(len(client.calls), 2)
                            self.assertEqual(client.calls[0]["Key"], plans.output_objects[0].object_key)
                            self.assertEqual(client.calls[1]["Key"], plans.output_objects[1].object_key)

    def test_hand_built_workspace_never_starts_an_upload_loop(self) -> None:
        """An arbitrary object cannot produce a partial or complete receipt set."""

        client = RecordingPutObjectClient()
        with self.assertRaises(TypeError):
            upload_complete_demucs_stem_set_from_plan_workspace(
                workspace=object(),  # type: ignore[arg-type]
                client=client,
            )
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
