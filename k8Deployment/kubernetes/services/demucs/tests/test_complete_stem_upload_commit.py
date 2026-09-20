"""Unit tests for complete Demucs uploads followed by guarded DB completion.

All MinIO and PostgreSQL boundaries are faked or patched. Tests do not execute
Demucs/Torch, open a socket, build an image, touch RabbitMQ, or use Kubernetes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.complete_stem_upload_commit import upload_and_commit_demucs_stem_set
from app.demucs_artifacts import DEMUCS_STEMS_BY_MODE
from app.demucs_command import DemucsSeparationCommand
from app.demucs_task_completion import CommittedDemucsStemSet
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
    """Write complete fake Demucs output rather than launching a CPU model."""

    def run(self, *, separation: DemucsSeparationCommand, timeout_seconds: int) -> None:
        """Create every expected private placeholder WAV file."""

        separation.expected_output_directory.mkdir(parents=True)
        for stem_name in DEMUCS_STEMS_BY_MODE[separation.stem_mode][1]:
            (separation.expected_output_directory / f"{stem_name}.wav").write_bytes(
                f"private-{stem_name}-wav-bytes".encode("ascii")
            )


class OrderedPutObjectClient:
    """Drain request bodies and record upload-before-transaction ordering."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> object:
        """Consume the complete fake body before recording one private upload."""

        body = kwargs["Body"]
        while body.read(11):
            pass
        self.calls.append(dict(kwargs))
        self.events.append("upload")
        return {}


class RecordingDatabase:
    """Expose the required transaction capability without opening PostgreSQL."""

    def write_cursor(self) -> object:
        """Never execute because the completion bridge is patched in this module test."""

        raise AssertionError("The patched completion bridge must own the transaction.")


def running_workspace(scratch: Path) -> DemucsRunningSourceWorkspace:
    """Create one generic source and committed running lease for nested helpers."""

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


class CompleteStemUploadCommitTests(unittest.TestCase):
    """Prove all private writes finish before one guarded completion attempt."""

    @patch("app.complete_stem_upload_commit.commit_published_demucs_stem_set")
    def test_passes_only_complete_receipts_to_completion_after_all_uploads(self, commit) -> None:
        """The database bridge receives no partial set and opens after network work."""

        events: list[str] = []
        database = RecordingDatabase()
        committed = MagicMock(spec=CommittedDemucsStemSet)

        def record_commit(**kwargs: object) -> object:
            """Record the post-upload completion call without simulating SQL internals."""

            events.append("commit")
            return committed

        commit.side_effect = record_commit
        uuid_factory = lambda: UUID("00000000-0000-4000-8000-000000000001")

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            client = OrderedPutObjectClient(events)

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    with opened_hashed_demucs_stem_inventory_workspace(validated) as hashed:
                        with opened_demucs_stem_output_plan_workspace(hashed) as plans:
                            returned = upload_and_commit_demucs_stem_set(
                                workspace=plans,
                                client=client,
                                database=database,  # type: ignore[arg-type]
                                event_id_factory=uuid_factory,
                            )

                            self.assertIs(returned, committed)
                            self.assertEqual(events, ["upload", "upload", "upload", "upload", "commit"])
                            commit.assert_called_once()
                            arguments = commit.call_args.kwargs
                            published = arguments["published"]
                            self.assertEqual(
                                tuple(receipt.object_key for receipt in published.uploads),
                                tuple(
                                    f"stems/{JOB_ID}/{stem_name}.wav"
                                    for stem_name in DEMUCS_STEMS_BY_MODE["4-stems"][1]
                                ),
                            )
                            self.assertIs(arguments["database"], database)
                            self.assertIs(arguments["event_id_factory"], uuid_factory)

    @patch("app.complete_stem_upload_commit.commit_published_demucs_stem_set")
    def test_committed_ownership_loss_returns_none_after_complete_upload(self, commit) -> None:
        """No downstream success is exposed when the current lease no longer owns the task."""

        commit.return_value = None
        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            client = OrderedPutObjectClient([])

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    with opened_hashed_demucs_stem_inventory_workspace(validated) as hashed:
                        with opened_demucs_stem_output_plan_workspace(hashed) as plans:
                            self.assertIsNone(
                                upload_and_commit_demucs_stem_set(
                                    workspace=plans,
                                    client=client,
                                    database=RecordingDatabase(),  # type: ignore[arg-type]
                                    event_id_factory=lambda: UUID("00000000-0000-4000-8000-000000000001"),
                                )
                            )
                            self.assertEqual(len(client.calls), 4)
                            commit.assert_called_once()

    def test_missing_database_capability_stops_before_any_private_upload(self) -> None:
        """A misconfigured worker cannot write objects with no completion path."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            events: list[str] = []
            client = OrderedPutObjectClient(events)

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    with opened_hashed_demucs_stem_inventory_workspace(validated) as hashed:
                        with opened_demucs_stem_output_plan_workspace(hashed) as plans:
                            with self.assertRaises(TypeError):
                                upload_and_commit_demucs_stem_set(
                                    workspace=plans,
                                    client=client,
                                    database=object(),  # type: ignore[arg-type]
                                    event_id_factory=lambda: UUID("00000000-0000-4000-8000-000000000001"),
                                )

            self.assertEqual(client.calls, [])
            self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
