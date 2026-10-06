"""Unit tests for the short transaction that completes a published Demucs set.

Every database interaction is a tiny in-memory context manager plus a patched
pure SQL function.  No test opens PostgreSQL, MinIO, RabbitMQ, Demucs, Docker,
or Kubernetes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from app.artifacts.demucs_artifact_upload import UploadedDemucsStemObject
from app.artifacts.demucs_artifacts import DEMUCS_STEMS_BY_MODE
from app.processing.demucs_stem_set_publish import PublishedDemucsStemSet
from app.db.demucs_task_completion import (
    DemucsTaskCompletionContractError,
    commit_published_demucs_stem_set,
)
from app.db.task_lease import DemucsTaskCompletion, DemucsTaskLease, DemucsTaskLeaseProtocolError


JOB_ID = "11111111-1111-4111-8111-111111111111"
TASK_ID = "22222222-2222-4222-8222-222222222222"
EVENT_ID = "33333333-3333-4333-8333-333333333333"
LEASE_TOKEN = "44444444-4444-4444-8444-444444444444"
DOWNSTREAM_EVENT_IDS = (
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003",
    "00000000-0000-4000-8000-000000000004",
)


class FixedUuidFactory:
    """Yield deterministic UUID objects while testing immutable outbox identities."""

    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> UUID:
        return UUID(next(self._values))


class RecordingDatabase:
    """Expose transaction commit/rollback ordering without a PostgreSQL server."""

    def __init__(self) -> None:
        self.cursor = object()
        self.events: list[str] = []

    @contextmanager
    def write_cursor(self) -> Iterator[object]:
        """Mirror normal short-transaction behavior for the composition tests."""

        self.events.append("transaction-open")
        try:
            yield self.cursor
        except Exception:
            self.events.append("transaction-rollback")
            raise
        else:
            self.events.append("transaction-commit")


def lease(*, stem_mode: str = "4-stems") -> DemucsTaskLease:
    """Return an already-running lease for one reviewed Demucs output mode."""

    return DemucsTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/source.mp3",
        stem_mode=stem_mode,
        attempt_count=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2026, 9, 8, 12, 30, tzinfo=UTC),
    )


def published(*, stem_mode: str = "4-stems") -> PublishedDemucsStemSet:
    """Return exact complete upload evidence in the canonical mode-specific order."""

    return PublishedDemucsStemSet(
        lease=lease(stem_mode=stem_mode),
        uploads=tuple(
            UploadedDemucsStemObject(
                bucket="clouddsp-uploads",
                object_key=f"stems/{JOB_ID}/{stem_name}.wav",
                content_length=index + 100,
                sha256=f"{index + 1:064x}",
            )
            for index, stem_name in enumerate(DEMUCS_STEMS_BY_MODE[stem_mode][1])
        ),
    )


class DemucsTaskCompletionCompositionTests(unittest.TestCase):
    """Prove private receipt evidence becomes durable only after a committed guard."""

    @patch("app.db.demucs_task_completion.complete_running_demucs_task")
    def test_commits_cloud_compatible_stems_with_extra_integrity_evidence(self, complete) -> None:
        """The worker preserves `status`/`s3_key` while recording bytes and SHA-256."""

        database = RecordingDatabase()
        completion_time = datetime(2026, 9, 8, 12, 35, tzinfo=UTC)
        complete.return_value = DemucsTaskCompletion(
            task_id=TASK_ID,
            job_id=JOB_ID,
            completed_at=completion_time,
            job_revision=8,
            outbox_event_count=4,
        )

        committed = commit_published_demucs_stem_set(
            database=database,  # type: ignore[arg-type]
            published=published(),
            event_id_factory=FixedUuidFactory(*DOWNSTREAM_EVENT_IDS),
        )

        self.assertIsNotNone(committed)
        assert committed is not None
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        self.assertEqual(committed.completed_at, completion_time)
        self.assertEqual(committed.job_revision, 8)
        complete.assert_called_once()
        positional_arguments, keyword_arguments = complete.call_args
        self.assertIs(positional_arguments[0], database.cursor)
        document = json.loads(keyword_arguments["stems_document"])
        self.assertEqual(set(document), set(DEMUCS_STEMS_BY_MODE["4-stems"][1]))
        for stem_name, record in document.items():
            self.assertEqual(record["status"], "ready")
            self.assertEqual(record["s3_key"], f"stems/{JOB_ID}/{stem_name}.wav")
            self.assertEqual(record["bucket"], "clouddsp-uploads")
            self.assertEqual(record["content_type"], "audio/wav")
            self.assertIsInstance(record["size_bytes"], int)
            self.assertRegex(record["sha256"], r"^[0-9a-f]{64}$")
        downstream_document = json.loads(keyword_arguments["downstream_events_document"])
        self.assertEqual(len(downstream_document), 4)
        self.assertEqual(
            [(event["stage"], event["stem_name"], event["event_type"]) for event in downstream_document],
            [
                ("adtof", "drums", "adtof.requested"),
                ("basic-pitch", "bass", "basic-pitch.requested"),
                ("basic-pitch", "other", "basic-pitch.requested"),
                ("basic-pitch", "vocals", "basic-pitch.requested"),
            ],
        )
        self.assertEqual([event["event_id"] for event in downstream_document], list(DOWNSTREAM_EVENT_IDS))
        for event in downstream_document:
            self.assertEqual(event["payload"]["job_id"], JOB_ID)
            self.assertEqual(event["payload"]["stem_name"], event["stem_name"])
            self.assertEqual(event["payload"]["stem"]["bucket"], "clouddsp-uploads")
            self.assertEqual(event["payload"]["stem"]["content_type"], "audio/wav")
        self.assertEqual(len(committed.downstream_events), 4)

    @patch("app.db.demucs_task_completion.complete_running_demucs_task")
    def test_ownership_loss_commits_without_exposing_durable_success(self, complete) -> None:
        """A stale worker must not emit downstream work after a no-row SQL result."""

        database = RecordingDatabase()
        complete.return_value = None

        self.assertIsNone(
            commit_published_demucs_stem_set(
            database=database,  # type: ignore[arg-type]
            published=published(),
            event_id_factory=FixedUuidFactory(*DOWNSTREAM_EVENT_IDS),
            )
        )
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])

    @patch("app.db.demucs_task_completion.complete_running_demucs_task")
    def test_every_reviewed_mode_routes_drums_only_to_adtof(self, complete) -> None:
        """The 2/4/6-stem contract cannot lose no-vocals, piano, or guitar fan-out."""

        database = RecordingDatabase()
        # The list has the largest mode's six UUIDs; each subtest starts a new
        # factory so the IDs remain valid and deterministic but irrelevant to
        # the routing assertion itself.
        event_ids = (
            "00000000-0000-4000-8000-000000000011",
            "00000000-0000-4000-8000-000000000012",
            "00000000-0000-4000-8000-000000000013",
            "00000000-0000-4000-8000-000000000014",
            "00000000-0000-4000-8000-000000000015",
            "00000000-0000-4000-8000-000000000016",
        )
        for stem_mode, expected_stems in (
            ("2-stems", ("vocals", "no_vocals")),
            ("4-stems", ("drums", "bass", "other", "vocals")),
            ("6-stems", ("drums", "bass", "other", "vocals", "guitar", "piano")),
        ):
            with self.subTest(stem_mode=stem_mode):
                completion_time = datetime(2026, 9, 8, 12, 35, tzinfo=UTC)
                complete.return_value = DemucsTaskCompletion(
                    task_id=TASK_ID,
                    job_id=JOB_ID,
                    completed_at=completion_time,
                    job_revision=8,
                    outbox_event_count=len(expected_stems),
                )
                committed = commit_published_demucs_stem_set(
                    database=database,  # type: ignore[arg-type]
                    published=published(stem_mode=stem_mode),
                    event_id_factory=FixedUuidFactory(*event_ids),
                )
                self.assertIsNotNone(committed)
                assert committed is not None
                self.assertEqual(
                    [(event.stem_name, event.stage) for event in committed.downstream_events],
                    [
                        (stem_name, "adtof" if stem_name == "drums" else "basic-pitch")
                        for stem_name in expected_stems
                    ],
                )

    @patch("app.db.demucs_task_completion.complete_running_demucs_task")
    def test_invalid_receipt_stops_before_transaction_and_sql_failure_rolls_back(self, complete) -> None:
        """Neither forged storage evidence nor driver protocol faults can partially commit."""

        database = RecordingDatabase()
        incomplete = PublishedDemucsStemSet(lease=lease(), uploads=())
        with self.assertRaises(DemucsTaskCompletionContractError):
            commit_published_demucs_stem_set(
                database=database,  # type: ignore[arg-type]
                published=incomplete,
                event_id_factory=FixedUuidFactory(*DOWNSTREAM_EVENT_IDS),
            )
        self.assertEqual(database.events, [])
        complete.assert_not_called()

        complete.side_effect = DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
        with self.assertRaises(DemucsTaskLeaseProtocolError):
            commit_published_demucs_stem_set(
                database=database,  # type: ignore[arg-type]
                published=published(),
                event_id_factory=FixedUuidFactory(*DOWNSTREAM_EVENT_IDS),
            )
        self.assertEqual(
            database.events,
            ["transaction-open", "transaction-rollback"],
        )


if __name__ == "__main__":
    unittest.main()
