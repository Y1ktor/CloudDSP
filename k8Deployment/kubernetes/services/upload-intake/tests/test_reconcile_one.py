"""Pure tests for a one-job, least-privilege source-upload repair.

No test connects to PostgreSQL, MinIO, RabbitMQ, or Kubernetes. The fake
cursor proves the repair obtains its object key from the pending database row
and routes it through the same message handler used by normal notifications.
"""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock

from app.message_handler import (
    SourceIntakeCandidateOutcome,
    SourceIntakeCandidateResult,
    SourceIntakeEnvelopeOutcome,
    SourceIntakeMessageResult,
)
from app.reconcile_one import (
    FIND_ONE_PENDING_UPLOAD_SQL,
    ReconcileOutcome,
    reconcile_one_pending_upload,
)


JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
OBJECT_KEY = f"uploads/{JOB_ID}/爷爷泡的茶 - Jay+Chou.wav"


class FakeDatabase:
    """Return one fake read cursor and fail if the repair opens a write scope."""

    def __init__(self, row: dict[str, object] | None) -> None:
        self.cursor = MagicMock()
        self.cursor.fetchone.return_value = row

    @contextmanager
    def read_cursor(self):  # type: ignore[no-untyped-def]
        yield self.cursor

    def write_cursor(self):  # type: ignore[no-untyped-def]
        raise AssertionError("The repair delegates writes to the normal handler.")


def handled(outcome: SourceIntakeCandidateOutcome) -> SourceIntakeMessageResult:
    """Return one acknowledgement-safe, non-sensitive handler result."""

    return SourceIntakeMessageResult(
        envelope_outcome=SourceIntakeEnvelopeOutcome.HANDLED,
        candidate_results=(SourceIntakeCandidateResult(record_index=0, outcome=outcome),),
        ignored_records=(),
    )


class ReconcileOneTests(unittest.TestCase):
    """Keep the operational repair narrow and safe to retry."""

    def test_repairs_exact_row_with_normal_form_encoded_event(self) -> None:
        """The caller supplies only a UUID; the DB chooses the private key."""

        database = FakeDatabase({"input_bucket": "clouddsp-uploads", "input_object_key": OBJECT_KEY})
        handler = MagicMock(return_value=handled(SourceIntakeCandidateOutcome.SOURCE_UPLOADED))

        outcome = reconcile_one_pending_upload(JOB_ID, database=database, handle_message=handler)

        self.assertEqual(outcome, ReconcileOutcome.REPAIRED)
        database.cursor.execute.assert_called_once_with(FIND_ONE_PENDING_UPLOAD_SQL, (JOB_ID,))
        body = json.loads(handler.call_args.args[0])
        self.assertEqual(
            body["Records"][0]["s3"]["object"]["key"],
            f"uploads/{JOB_ID}/%E7%88%B7%E7%88%B7%E6%B3%A1%E7%9A%84%E8%8C%B6+-+Jay%2BChou.wav",
        )

    def test_missing_or_advanced_job_never_calls_storage_or_handler(self) -> None:
        """An already-repaired job is an idempotent no-op."""

        database = FakeDatabase(None)
        handler = MagicMock()

        self.assertEqual(
            reconcile_one_pending_upload(JOB_ID, database=database, handle_message=handler),
            ReconcileOutcome.NOT_PENDING,
        )
        handler.assert_not_called()

    def test_rejects_noncanonical_id_before_reading_any_job(self) -> None:
        """No arbitrary object key, uppercase UUID, or SQL text can be input."""

        database = FakeDatabase(None)
        handler = MagicMock()

        for invalid in (JOB_ID.upper(), "not-a-uuid", f"{JOB_ID}' OR TRUE"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                reconcile_one_pending_upload(invalid, database=database, handle_message=handler)
        database.cursor.execute.assert_not_called()
        handler.assert_not_called()

    def test_reports_a_race_without_reopening_work(self) -> None:
        """The ordinary handler may find that a concurrent event already won."""

        database = FakeDatabase({"input_bucket": "clouddsp-uploads", "input_object_key": OBJECT_KEY})
        handler = MagicMock(return_value=handled(SourceIntakeCandidateOutcome.NO_LONGER_PENDING))

        self.assertEqual(
            reconcile_one_pending_upload(JOB_ID, database=database, handle_message=handler),
            ReconcileOutcome.LOST_RACE,
        )


if __name__ == "__main__":
    unittest.main()
