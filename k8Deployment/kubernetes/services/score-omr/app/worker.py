"""Consume score-intake deliveries with durable claims and manual ACKs."""

import logging
import os
import signal
import tempfile
import time
from pathlib import Path

import boto3
from botocore.config import Config
import pika
import psycopg

from app.event import BUCKET, score_candidates
from app.state import (MAX_ATTEMPTS, claim, complete, fail, fail_unclaimed,
                       mark_source_uploaded, read_source, retry)
from app.transcribe import InvalidScore, transcribe

LOG = logging.getLogger("score-omr")
QUEUE = "clouddsp.score-intake"
RETRY_EXCHANGE = "clouddsp.source-retry"
STOP = False


def _stop(_signum, _frame):
    global STOP
    STOP = True


def _db():
    return psycopg.connect(host="clouddsp-postgresql.clouddsp-data.svc",
                           dbname="clouddsp_job_api", user=os.environ["SCORE_DB_USER"],
                           password=os.environ["SCORE_DB_PASSWORD"], connect_timeout=5,
                           autocommit=True, application_name="clouddsp-score-omr",
                           options="-c statement_timeout=10000 -c lock_timeout=5000")


def _s3():
    return boto3.client("s3", endpoint_url="http://clouddsp-minio.clouddsp-data.svc:9000",
                        aws_access_key_id=os.environ["SCORE_S3_ACCESS_KEY"],
                        aws_secret_access_key=os.environ["SCORE_S3_SECRET_KEY"],
                        region_name="us-east-1",
                        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                                      retries={"max_attempts": 2}, connect_timeout=5, read_timeout=30))


def _process_candidate(connection, s3, job_id: str, event_key: str):
    with _db() as db:
        source = read_source(db, job_id, event_key)
        if source is None or source[4] in ("completed", "failed"):
            return
        bucket, key, content_type, expected_size, _status = source
        # MinIO notification fields are not authority: compare the actual
        # object length/type and the database-owned coordinates before claim.
        head = s3.head_object(Bucket=bucket, Key=key)
        metadata = head.get("Metadata", {})
        if (head["ContentLength"] != expected_size
                or head["ContentType"].split(";")[0] != content_type
                or metadata.get("job-id") != job_id
                or metadata.get("score-direction") != "score_to_midi"):
            if fail_unclaimed(db, job_id, event_key,
                              "The uploaded score does not match its upload request."):
                return
            raise RuntimeError("Source validation raced an active lease.")
        mark_source_uploaded(db, job_id, event_key)
        acquired = claim(db, job_id, event_key)
        # A duplicate notification can arrive while another Pod owns the
        # same score. Keep this delivery unacknowledged and the RabbitMQ
        # heartbeat alive until that lease finishes or expires. Retrying the
        # 30-second queue here would exhaust attempts before a 15-minute
        # inference lease could be reclaimed after a crashed Pod.
        while acquired.outcome == "busy":
            for _ in range(10):
                connection.process_data_events(time_limit=1)
                time.sleep(1)
            acquired = claim(db, job_id, event_key)
        if acquired.outcome in ("ignore", "terminal"):
            return
        token = acquired.token
        try:
            with tempfile.TemporaryDirectory(dir="/worker-scratch") as directory:
                work = Path(directory)
                source_file = work / Path(key).name
                s3.download_file(bucket, key, str(source_file))
                if source_file.stat().st_size != expected_size:
                    raise InvalidScore("The uploaded score size changed during processing.")
                midi, xml = transcribe(source_file, content_type, work,
                                       tick=lambda: connection.process_data_events(time_limit=0))
                midi_key = f"score-results/{job_id}/result.mid"
                xml_key = f"score-results/{job_id}/result.musicxml"
                s3.upload_file(str(midi), BUCKET, midi_key,
                               ExtraArgs={"ContentType": "audio/midi"})
                s3.upload_file(str(xml), BUCKET, xml_key,
                               ExtraArgs={"ContentType": "application/vnd.recordare.musicxml+xml"})
                if not complete(db, job_id, token, bucket=BUCKET, midi_key=midi_key, xml_key=xml_key):
                    raise RuntimeError("Score lease changed before completion.")
                LOG.info("score completed job_id=%s", job_id)
        except InvalidScore as error:
            fail(db, job_id, token, str(error))
            LOG.info("score failed job_id=%s reason=invalid-score", job_id)
        except Exception:
            retry(db, job_id, token)
            raise


def _handle(connection, channel, delivery, properties, body, s3):
    candidates = score_candidates(body)
    try:
        for job_id, event_key in candidates:
            _process_candidate(connection, s3, job_id, event_key)
    except Exception as error:
        raw_attempt = (properties.headers or {}).get("x-score-attempt", 0)
        attempt = (raw_attempt if isinstance(raw_attempt, int) and raw_attempt >= 0 else 0) + 1
        if attempt >= MAX_ATTEMPTS:
            # A transient outage may prevent this update. In that case keep
            # the delivery in the broker DLQ for operator recovery.
            try:
                with _db() as db:
                    fail_unclaimed(db, job_id, event_key, "Score processing failed after retries.")
            except Exception:
                LOG.warning("unable to finalize exhausted score job_id=%s", job_id)
            # Retain the original envelope for recovery, including any later
            # records in a batch. Never ACK work whose durable update failed.
            LOG.warning("score retry exhausted; dead-lettered type=%s", type(error).__name__)
            channel.basic_nack(delivery.delivery_tag, requeue=False)
            return
        try:
            channel.basic_publish(exchange=RETRY_EXCHANGE, routing_key="score.upload.retry",
                                  body=body, mandatory=True,
                                  properties=pika.BasicProperties(delivery_mode=2,
                                                              headers={"x-score-attempt": attempt}))
        except Exception:
            # A lost publisher confirm must never be followed by ACK.
            channel.basic_nack(delivery.delivery_tag, requeue=True)
            raise
        channel.basic_ack(delivery.delivery_tag)
        LOG.warning("score deferred to retry queue attempt=%s", attempt)
        return
    channel.basic_ack(delivery.delivery_tag)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    credentials = pika.PlainCredentials(os.environ["SCORE_AMQP_USER"], os.environ["SCORE_AMQP_PASSWORD"])
    parameters = pika.ConnectionParameters(host="clouddsp-rabbitmq.clouddsp-data.svc", port=5672,
                                           virtual_host="/clouddsp", credentials=credentials,
                                           heartbeat=60, blocked_connection_timeout=30)
    while not STOP:
        try:
            with pika.BlockingConnection(parameters) as connection:
                channel = connection.channel()
                channel.basic_qos(prefetch_count=1)
                channel.queue_declare(queue=QUEUE, passive=True)
                channel.confirm_delivery()
                s3 = _s3()
                while not STOP:
                    method, properties, body = channel.basic_get(queue=QUEUE, auto_ack=False)
                    if method is None:
                        connection.process_data_events(time_limit=1)
                        continue
                    _handle(connection, channel, method, properties, body, s3)
        except Exception as error:
            LOG.warning("score consumer disconnected; broker will redeliver type=%s", type(error).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
