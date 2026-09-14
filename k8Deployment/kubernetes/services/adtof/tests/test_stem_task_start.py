"""Unit tests for ADTOF's verified-stem-to-running composition boundary.

The MinIO downloader and PostgreSQL task-start SQL are patched at their public
interfaces. These ordering/cleanup tests contact no MinIO, PostgreSQL,
RabbitMQ, ADTOF model, Docker image, or Kubernetes resource.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import unittest
from unittest.mock import patch

from app.postgresql import ADTOFDatabaseUnavailable
from app.stem_download import DownloadedADTOFStem
from app.stem_object import VerifiedADTOFStemObject
from app.stem_task_start import ADTOFTaskStartCompositionError, started_verified_adtof_stem
from app.task_claim import ADTOFTaskClaimProtocolError, ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
STEM_KEY = f"stems/{JOB_ID}/drums.wav"
STEM_SHA256 = "a" * 64


class RecordingDatabase:
    """Expose one fake transaction while recording commit/rollback order."""

    def __init__(self, events: list[str]) -> None:
        self.cursor = object()
        self.events = events

    @contextmanager
    def write_cursor(self) -> Iterator[object]:
        """Mirror the real adapter's normal-commit and exception-rollback scope."""

        self.events.append("transaction-open")
        try:
            yield self.cursor
        except Exception:
            self.events.append("transaction-rollback")
            raise
        else:
            self.events.append("transaction-commit")


class UnavailableDatabase:
    """Fail before a cursor exists, like an unavailable PostgreSQL Service."""

    @contextmanager
    def write_cursor(self) -> Iterator[object]:
        """Expose the safe availability category without opening a database."""

        raise ADTOFDatabaseUnavailable("PostgreSQL ADTOF task access is unavailable.")
        yield object()  # pragma: no cover - preserves generator type only.


class RecordingDownload:
    """A fake temporary-WAV scope whose lifetime is observable by tests."""

    def __init__(self, events: list[str], downloaded: DownloadedADTOFStem) -> None:
        self.events = events
        self.downloaded = downloaded

    def __enter__(self) -> DownloadedADTOFStem:
        self.events.append("download-open")
        return self.downloaded

    def __exit__(self, exception_type, exception, traceback) -> bool:
        self.events.append("download-cleanup")
        return False


def lease() -> ADTOFTaskLease:
    """Return a current task lease correlated with the private drums WAV."""

    return ADTOFTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        stem_name="drums",
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=STEM_KEY,
        stem_mode="4-stems",
        attempt_count=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2026, 9, 13, 12, 15, tzinfo=UTC),
    )


def source(*, object_key: str = STEM_KEY) -> VerifiedADTOFStemObject:
    """Return already-verified MinIO evidence for that exact drums object."""

    return VerifiedADTOFStemObject(
        bucket_name="clouddsp-uploads",
        object_key=object_key,
        content_type="audio/wav",
        size_bytes=101,
        sha256=STEM_SHA256,
    )


def downloaded() -> DownloadedADTOFStem:
    """Return a generic temporary path without creating a filesystem object."""

    return DownloadedADTOFStem(
        stem_path=Path("/pod-scratch/adtof-stem-random/stem.wav"),
        size_bytes=101,
        sha256=STEM_SHA256,
    )


class VerifiedStemTaskStartTests(unittest.TestCase):
    """Prove local ADTOF input exists only after a committed current lease."""

    @patch("app.stem_task_start.start_leased_adtof_task")
    @patch("app.stem_task_start.downloaded_verified_adtof_stem")
    def test_committed_start_yields_exact_temp_stem_after_transaction(self, download, start) -> None:
        """A future model receives its local path only after durable admission."""

        events: list[str] = []
        database = RecordingDatabase(events)
        transient = downloaded()
        download.return_value = RecordingDownload(events, transient)
        started_at = datetime(2026, 9, 13, 12, 20, tzinfo=UTC)
        start.return_value = started_at
        client = object()
        verified_source = source()

        with started_verified_adtof_stem(
            database=database,  # type: ignore[arg-type]
            client=client,  # type: ignore[arg-type]
            lease=lease(),
            source=verified_source,
            work_directory=Path("/pod-scratch"),
        ) as running:
            self.assertIsNotNone(running)
            assert running is not None
            self.assertEqual(running.lease, lease())
            self.assertIs(running.stem, transient)
            self.assertEqual(running.started_at, started_at)
            self.assertEqual(events, ["download-open", "transaction-open", "transaction-commit"])

        self.assertEqual(events, ["download-open", "transaction-open", "transaction-commit", "download-cleanup"])
        download.assert_called_once_with(
            client,
            source=verified_source,
            work_directory=Path("/pod-scratch"),
        )
        start.assert_called_once_with(database.cursor, lease=lease())

    @patch("app.stem_task_start.start_leased_adtof_task")
    @patch("app.stem_task_start.downloaded_verified_adtof_stem")
    def test_ownership_loss_cleans_stem_before_yielding_none(self, download, start) -> None:
        """A stale replica cannot see temporary input after it loses its lease."""

        events: list[str] = []
        database = RecordingDatabase(events)
        download.return_value = RecordingDownload(events, downloaded())
        start.return_value = None

        with started_verified_adtof_stem(
            database=database,  # type: ignore[arg-type]
            client=object(),  # type: ignore[arg-type]
            lease=lease(),
            source=source(),
            work_directory=Path("/pod-scratch"),
        ) as running:
            self.assertIsNone(running)
            self.assertEqual(
                events,
                ["download-open", "transaction-open", "transaction-commit", "download-cleanup"],
            )

    @patch("app.stem_task_start.start_leased_adtof_task")
    @patch("app.stem_task_start.downloaded_verified_adtof_stem")
    def test_invalid_start_rolls_back_and_cleans_temporary_input(self, download, start) -> None:
        """A malformed start outcome cannot retain model input or partial state."""

        events: list[str] = []
        database = RecordingDatabase(events)
        download.return_value = RecordingDownload(events, downloaded())
        start.side_effect = ADTOFTaskClaimProtocolError("ADTOF task database state is invalid.")

        with self.assertRaises(ADTOFTaskClaimProtocolError):
            with started_verified_adtof_stem(
                database=database,  # type: ignore[arg-type]
                client=object(),  # type: ignore[arg-type]
                lease=lease(),
                source=source(),
                work_directory=Path("/pod-scratch"),
            ):
                self.fail("A failed task-start must not yield model input.")

        self.assertEqual(events, ["download-open", "transaction-open", "transaction-rollback", "download-cleanup"])

    @patch("app.stem_task_start.start_leased_adtof_task")
    @patch("app.stem_task_start.downloaded_verified_adtof_stem")
    def test_database_outage_cleans_input_without_executing_start_sql(self, download, start) -> None:
        """An unavailable Service cannot promote downloaded evidence to model work."""

        events: list[str] = []
        download.return_value = RecordingDownload(events, downloaded())

        with self.assertRaises(ADTOFDatabaseUnavailable):
            with started_verified_adtof_stem(
                database=UnavailableDatabase(),  # type: ignore[arg-type]
                client=object(),  # type: ignore[arg-type]
                lease=lease(),
                source=source(),
                work_directory=Path("/pod-scratch"),
            ):
                self.fail("Database outage must not yield model input.")

        self.assertEqual(events, ["download-open", "download-cleanup"])
        start.assert_not_called()

    def test_source_from_another_task_is_rejected_before_download(self) -> None:
        """A valid private drums object for another task cannot cross ownership."""

        with self.assertRaises(ADTOFTaskStartCompositionError):
            with started_verified_adtof_stem(
                database=RecordingDatabase([]),  # type: ignore[arg-type]
                client=object(),  # type: ignore[arg-type]
                lease=lease(),
                source=source(object_key=f"stems/{JOB_ID}/other-drums.wav"),
                work_directory=Path("/pod-scratch"),
            ):
                self.fail("A mismatched source must not create model input.")


if __name__ == "__main__":
    unittest.main()
