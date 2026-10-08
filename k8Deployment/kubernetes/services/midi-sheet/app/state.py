"""Revision and lease guarded score-job transitions.

The RabbitMQ delivery is a hint. The row's immutable input key, active lease
token, and terminal state determine whether this worker may process it.
"""

from dataclasses import dataclass
from uuid import uuid4

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Claim:
    outcome: str
    token: str | None = None
    bucket: str | None = None
    key: str | None = None
    content_type: str | None = None
    size: int | None = None


def read_source(conn, job_id: str, event_key: str):
    with conn.cursor() as cursor:
        cursor.execute("""SELECT input_bucket, input_object_key, source_content_type,
                         source_size_bytes, status FROM public.midi_sheet_jobs
                         WHERE job_id = %s AND expires_at > CURRENT_TIMESTAMP""", (job_id,))
        row = cursor.fetchone()
    if row is None or row[1] != event_key or row[0] != "clouddsp-uploads":
        return None
    return row


def mark_source_uploaded(conn, job_id: str, event_key: str) -> None:
    """Persist the MinIO-verified intake handoff before claiming inference."""

    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute("""UPDATE public.midi_sheet_jobs SET source_uploaded = TRUE,
                             status = 'source_uploaded', revision = revision + 1
                             WHERE job_id = %s AND input_object_key = %s
                             AND status = 'upload_pending'""", (job_id, event_key))


def claim(conn, job_id: str, event_key: str, *, lease_seconds: int = 180) -> Claim:
    token = str(uuid4())
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute("""SELECT input_bucket, input_object_key, source_content_type,
                             source_size_bytes, status, lease_expires_at, attempt_count
                             FROM public.midi_sheet_jobs WHERE job_id = %s
                             AND expires_at > CURRENT_TIMESTAMP FOR UPDATE""", (job_id,))
            row = cursor.fetchone()
            if row is None or row[0] != "clouddsp-uploads" or row[1] != event_key:
                return Claim("ignore")
            bucket, key, content_type, size, status, lease_expires_at, attempts = row
            if status in ("completed", "failed"):
                return Claim("terminal")
            if status == "processing" and lease_expires_at is not None:
                cursor.execute("SELECT CURRENT_TIMESTAMP")
                if lease_expires_at > cursor.fetchone()[0]:
                    return Claim("busy")
            # A Pod crash does not reset the retry budget. The locked durable
            # counter also bounds redeliveries without an x-sheet-attempt header.
            if attempts >= MAX_ATTEMPTS:
                cursor.execute("""UPDATE public.midi_sheet_jobs SET status = 'failed',
                                 error_message = 'Sheet rendering failed after retries.',
                                 lease_token = NULL, lease_expires_at = NULL,
                                 revision = revision + 1 WHERE job_id = %s""", (job_id,))
                return Claim("terminal")
            cursor.execute("""UPDATE public.midi_sheet_jobs SET status = 'processing',
                             source_uploaded = TRUE, lease_token = %s,
                             lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                             attempt_count = attempt_count + 1, revision = revision + 1,
                             error_message = NULL WHERE job_id = %s""",
                           (token, lease_seconds, job_id))
    return Claim("claimed", token, bucket, key, content_type, size)


def complete(conn, job_id: str, token: str, *, bucket: str, pdf_key: str, xml_key: str) -> bool:
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute("""UPDATE public.midi_sheet_jobs SET status = 'completed',
                             result_bucket = %s, result_pdf_key = %s,
                             result_musicxml_key = %s, lease_token = NULL,
                             lease_expires_at = NULL, revision = revision + 1
                             WHERE job_id = %s AND status = 'processing'
                             AND lease_token = %s AND lease_expires_at > CURRENT_TIMESTAMP
                             AND expires_at > CURRENT_TIMESTAMP
                             RETURNING job_id""", (bucket, pdf_key, xml_key, job_id, token))
            return cursor.fetchone() is not None


def retry(conn, job_id: str, token: str) -> None:
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute("""UPDATE public.midi_sheet_jobs SET status = 'source_uploaded',
                             lease_token = NULL, lease_expires_at = NULL,
                             revision = revision + 1 WHERE job_id = %s
                             AND lease_token = %s AND status = 'processing'""", (job_id, token))


def fail(conn, job_id: str, token: str, message: str) -> None:
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute("""UPDATE public.midi_sheet_jobs SET status = 'failed',
                             lease_token = NULL, lease_expires_at = NULL,
                             error_message = %s, revision = revision + 1
                             WHERE job_id = %s AND lease_token = %s
                             AND status = 'processing'""", (message[:1000], job_id, token))


def fail_unclaimed(conn, job_id: str, event_key: str, message: str) -> bool:
    """Finalize invalid intake or exhausted retries without stealing a live lease."""
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute("""UPDATE public.midi_sheet_jobs SET status = 'failed',
                             error_message = %s, lease_token = NULL,
                             lease_expires_at = NULL, revision = revision + 1
                             WHERE job_id = %s AND input_object_key = %s
                             AND status NOT IN ('completed', 'failed')
                             AND (lease_token IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                             RETURNING job_id""", (message[:1000], job_id, event_key))
            return cursor.fetchone() is not None
