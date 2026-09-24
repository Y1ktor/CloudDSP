"""Repair exactly one pending direct upload after its event was acknowledged.

This is an operator-invoked, one-shot entrypoint inside the existing intake
image. It begins with a PostgreSQL row, never a bucket listing or caller-
supplied object key. The row supplies the exact private object coordinate;
the ordinary event handler then repeats its database, MinIO HeadObject, and
atomic source-to-outbox checks. Repeating this command is idempotent because
the handler's conditional revision update can win only once.

It does not change the long-running consumer, create a Kubernetes Job, publish
an AMQP message, expose HTTP, or receive an administrator Secret. A periodic
bounded reconciler remains a separate reliability milestone.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from enum import StrEnum
from urllib.parse import quote_plus
from uuid import UUID

from app.consumer_runtime import build_source_intake_handler
from app.message_handler import (
    SourceIntakeCandidateOutcome,
    SourceIntakeDatabase,
    SourceIntakeEnvelopeOutcome,
    SourceIntakeMessageResult,
)
from app.minio_event import DIRECT_UPLOAD_EVENT_NAME, UPLOADS_BUCKET
from app.postgresql import PsycopgSourceIntakeDatabase


# The intake login already has SELECT on these columns. Restrict the lookup
# to a retained, unexpired direct-upload row; another account's object cannot
# be introduced through a CLI key, and a later-stage job cannot move backward.
FIND_ONE_PENDING_UPLOAD_SQL = """
    SELECT input_bucket, input_object_key
    FROM public.jobs
    WHERE job_id = %s::uuid
      AND source_type = 'direct_upload'
      AND status = 'upload_pending'
      AND source_uploaded = FALSE
      AND expires_at > CURRENT_TIMESTAMP
    LIMIT 1
"""


class ReconcileOutcome(StrEnum):
    """Fixed, non-sensitive operator results; never print object coordinates."""

    REPAIRED = "repaired"
    NOT_PENDING = "not_pending"
    LOST_RACE = "lost_race"
    FAILED_VERIFICATION = "failed_verification"


def _canonical_job_id(value: object) -> str:
    """Prevent alternate UUID spellings and SQL interpolation at the CLI edge."""

    if not isinstance(value, str):
        raise ValueError("A canonical job UUID is required.")
    try:
        canonical = str(UUID(value))
    except (ValueError, AttributeError) as error:
        raise ValueError("A canonical job UUID is required.") from error
    if canonical != value:
        raise ValueError("A canonical job UUID is required.")
    return canonical


def reconcile_one_pending_upload(
    job_id: str,
    *,
    database: SourceIntakeDatabase,
    handle_message: Callable[[bytes], SourceIntakeMessageResult],
) -> ReconcileOutcome:
    """Re-run the normal verification/transaction path for one retained row.

    The first read is short and read-only. The normal handler opens a separate
    read and conditional write scope, so a concurrent notification or deletion
    becomes ``lost_race`` rather than a duplicate Demucs outbox event.
    """

    canonical_id = _canonical_job_id(job_id)
    with database.read_cursor() as cursor:
        cursor.execute(FIND_ONE_PENDING_UPLOAD_SQL, (canonical_id,))
        row = cursor.fetchone()

    if row is None:
        return ReconcileOutcome.NOT_PENDING
    if not isinstance(row, Mapping):
        raise RuntimeError("Pending upload lookup returned an invalid row.")
    bucket = row.get("input_bucket")
    object_key = row.get("input_object_key")
    if bucket != UPLOADS_BUCKET or not isinstance(object_key, str) or not object_key:
        raise RuntimeError("Pending upload coordinates are invalid.")

    # S3 event keys use form-style URL encoding. `quote_plus` encodes spaces
    # as `+` and literal plus signs as `%2B`; the repaired parser will decode
    # this exactly once. Constructing a normal event here keeps one shared
    # verifier and one shared atomic PostgreSQL transition for both paths.
    body = json.dumps(
        {
            "Records": [
                {
                    "eventName": DIRECT_UPLOAD_EVENT_NAME,
                    "s3": {
                        "bucket": {"name": bucket},
                        "object": {"key": quote_plus(object_key, safe="/")},
                    },
                }
            ]
        },
        separators=(",", ":"),
    ).encode("utf-8")
    result = handle_message(body)
    if (
        result.envelope_outcome is not SourceIntakeEnvelopeOutcome.HANDLED
        or result.ignored_records
        or len(result.candidate_results) != 1
    ):
        raise RuntimeError("Pending upload reconciliation was not handled.")

    outcome = result.candidate_results[0].outcome
    if outcome is SourceIntakeCandidateOutcome.SOURCE_UPLOADED:
        return ReconcileOutcome.REPAIRED
    if outcome is SourceIntakeCandidateOutcome.NO_LONGER_PENDING:
        return ReconcileOutcome.LOST_RACE
    if outcome is SourceIntakeCandidateOutcome.FAILED:
        return ReconcileOutcome.FAILED_VERIFICATION
    raise RuntimeError("Pending upload reconciliation returned an unknown outcome.")


def main(arguments: list[str] | None = None) -> int:
    """Use only the existing intake Pod's restricted database/S3 identities."""

    values = sys.argv[1:] if arguments is None else arguments
    if len(values) != 1:
        print("Usage: python -m app.reconcile_one JOB_UUID", file=sys.stderr)
        return 2
    try:
        outcome = reconcile_one_pending_upload(
            values[0],
            database=PsycopgSourceIntakeDatabase(),
            handle_message=build_source_intake_handler(),
        )
    except Exception as error:
        # Driver/SDK exceptions can contain private keys, hosts, or Secret
        # data. An operator gets only a fixed category; the job is unchanged
        # for a retry unless the normal handler durably recorded a failure.
        print(f"Upload reconciliation failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(f"Upload reconciliation outcome={outcome.value}")
    return 0 if outcome is not ReconcileOutcome.FAILED_VERIFICATION else 1


if __name__ == "__main__":
    raise SystemExit(main())
