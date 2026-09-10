"""Unit tests for complete in-memory Demucs process-to-MinIO publication.

The fake process writes tiny placeholder WAV files and the fake S3 client reads
their request bodies.  Tests do not execute Demucs/Torch, contact MinIO,
PostgreSQL, RabbitMQ, Docker, or a Kubernetes cluster.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from app.demucs_artifact_upload import UploadedDemucsStemObject
from app.demucs_artifacts import DEMUCS_STEMS_BY_MODE, DemucsArtifactInventoryMismatch
from app.demucs_command import DemucsSeparationCommand, build_demucs_separation_command
from app.demucs_process import DemucsProcessFailed
from app.demucs_stem_set_publish import (
    DemucsStemSetPublicationContractError,
    run_and_publish_demucs_stem_set,
)
from app.task_lease import DemucsTaskLease


JOB_ID = "11111111-1111-4111-8111-111111111111"
TASK_ID = "22222222-2222-4222-8222-222222222222"
EVENT_ID = "33333333-3333-4333-8333-333333333333"
LEASE_TOKEN = "44444444-4444-4444-8444-444444444444"


def separation_and_lease(root: Path) -> tuple[DemucsSeparationCommand, DemucsTaskLease]:
    """Build a fresh four-stem command plus its matching current task lease."""

    work_directory = root / "scratch"
    work_directory.mkdir(parents=True)
    source_path = work_directory / "input" / "source.media"
    source_path.parent.mkdir()
    source_path.write_bytes(b"preflighted-source-kept-private-for-this-runtime")
    output_directory = work_directory / "output"
    output_directory.mkdir()
    separation = build_demucs_separation_command(
        stem_mode="4-stems",
        source_path=source_path,
        output_directory=output_directory,
        work_directory=work_directory,
    )
    lease = DemucsTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/source.mp3",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    return separation, lease


class WritingDemucsRunner:
    """A fake zero-exit process runner that produces a complete expected tree."""

    def __init__(self, *, stem_count: int | None = None) -> None:
        self.stem_count = stem_count
        self.calls: list[DemucsSeparationCommand] = []

    def run(self, *, separation: DemucsSeparationCommand, timeout_seconds: int) -> None:
        """Create the exact accepted WAV names after the command is approved."""

        self.calls.append(separation)
        separation.expected_output_directory.mkdir(parents=True)
        stems = DEMUCS_STEMS_BY_MODE[separation.stem_mode][1]
        requested_stems = stems if self.stem_count is None else stems[:self.stem_count]
        for stem_name in requested_stems:
            (separation.expected_output_directory / f"{stem_name}.wav").write_bytes(
                f"private-{stem_name}-wav-data".encode("ascii")
            )


class FailingDemucsRunner:
    """A fake process runner that proves no upload follows a model failure."""

    def run(self, *, separation: DemucsSeparationCommand, timeout_seconds: int) -> None:
        raise DemucsProcessFailed("Demucs process failed.")


class CapturingPutObjectClient:
    """A fake S3 client that drains every request body and retains call evidence."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> object:
        body = kwargs["Body"]
        contents = bytearray()
        while chunk := body.read(7):
            contents.extend(chunk)
        self.calls.append({**kwargs, "contents": bytes(contents)})
        return {}


class DemucsStemSetPublishTests(unittest.TestCase):
    """Prove only one complete expected stem set becomes publication evidence."""

    def test_runs_then_uploads_every_expected_stem_in_deterministic_order(self) -> None:
        """No receipt exists until the process/output/hash/upload sequence completes."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            separation, lease = separation_and_lease(Path(temporary_directory))
            runner = WritingDemucsRunner()
            client = CapturingPutObjectClient()

            published = run_and_publish_demucs_stem_set(
                lease=lease,
                separation=separation,
                client=client,
                runner=runner,
            )

            expected_stems = DEMUCS_STEMS_BY_MODE["4-stems"][1]
            self.assertEqual(runner.calls, [separation])
            self.assertEqual(published.lease, lease)
            self.assertEqual(
                tuple(receipt.object_key for receipt in published.uploads),
                tuple(f"stems/{JOB_ID}/{stem_name}.wav" for stem_name in expected_stems),
            )
            self.assertEqual(len(client.calls), len(expected_stems))
            self.assertTrue(all(call["contents"] for call in client.calls))

    def test_model_or_inventory_failure_stops_before_any_put_object_call(self) -> None:
        """A zero exit without every expected output is not a partial success."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            separation, lease = separation_and_lease(Path(temporary_directory))
            client = CapturingPutObjectClient()
            with self.assertRaises(DemucsProcessFailed):
                run_and_publish_demucs_stem_set(
                    lease=lease,
                    separation=separation,
                    client=client,
                    runner=FailingDemucsRunner(),
                )
            self.assertEqual(client.calls, [])

            incomplete_separation, incomplete_lease = separation_and_lease(
                Path(temporary_directory) / "incomplete"
            )
            with self.assertRaises(DemucsArtifactInventoryMismatch):
                run_and_publish_demucs_stem_set(
                    lease=incomplete_lease,
                    separation=incomplete_separation,
                    client=client,
                    runner=WritingDemucsRunner(stem_count=1),
                )
            self.assertEqual(client.calls, [])

    def test_mode_mismatch_and_incorrect_injected_receipt_never_become_full_evidence(self) -> None:
        """The composition repeats task identity and receipt equality checks."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            separation, lease = separation_and_lease(Path(temporary_directory))
            client = CapturingPutObjectClient()
            with self.assertRaises(DemucsStemSetPublicationContractError):
                run_and_publish_demucs_stem_set(
                    lease=replace(lease, stem_mode="2-stems"),
                    separation=separation,
                    client=client,
                    runner=WritingDemucsRunner(),
                )
            self.assertEqual(client.calls, [])

            def wrong_receipt(
                *,
                client: object,
                output_object: object,
            ) -> UploadedDemucsStemObject:
                """Return a mismatched key without doing I/O to test the final gate."""

                return UploadedDemucsStemObject(
                    bucket="clouddsp-uploads",
                    object_key="stems/not-the-reviewed-job/drums.wav",
                    content_length=1,
                    sha256="0" * 64,
                )

            with self.assertRaises(DemucsStemSetPublicationContractError):
                run_and_publish_demucs_stem_set(
                    lease=lease,
                    separation=separation,
                    client=client,
                    runner=WritingDemucsRunner(),
                    uploader=wrong_receipt,
                )


if __name__ == "__main__":
    unittest.main()
