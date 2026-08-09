"""Ingest a linked audio source into a durable CloudDSP processing job.

The Job API creates the DynamoDB job before asynchronously invoking this
Lambda.  yt-dlp downloads and converts the media to WAV, then writes exactly
the job's existing ``uploads/{job_id}/...`` input key in the uploads bucket.
That S3 write is deliberately the only handoff to EventBridge and the Demucs
Batch job.  This handler never uses a WebSocket connection ID as job state.
"""

from __future__ import annotations

import json
import ipaddress
import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import boto3
import yt_dlp
from botocore.exceptions import ClientError
from yt_dlp.networking.impersonate import ImpersonateTarget

from cloud_job_workflow import get_job, send_job_updated, utc_now


INGESTION_STATUS = "source_ingestion"
UPLOADED_STATUS = "upload_pending"
DEFAULT_MAX_SOURCE_DURATION_SECONDS = 480
MAX_DISPLAY_FILENAME_LENGTH = 255
DEFAULT_BROWSER_IMPERSONATION = "chrome"


def configured_positive_int(name: str, default: int) -> int:
    """Read a positive integer Lambda setting."""
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer.") from error
    if value < 1:
        raise ValueError(f"{name} must be positive.")
    return value


def safe_source_url(value: Any) -> str:
    """Validate an invocation's link before passing it to yt-dlp."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_url is required.")
    if len(value) > 2_048:
        raise ValueError("source_url is too long.")

    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("source_url must be an absolute HTTP or HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError("source_url must not contain credentials.")
    if parsed.hostname.lower() == "localhost":
        raise ValueError("source_url must not target localhost.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        # DNS hostnames are allowed. The caller's network policy must still
        # block access to private services through a hostname or DNS rebinding.
        pass
    else:
        if not address.is_global:
            raise ValueError("source_url must not target a private or reserved IP address.")
    return value


def display_filename(title: Any) -> str:
    """Create a safe display-only filename without changing the stable S3 key."""
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(title or "linked-audio")).strip(" ._")
    cleaned = cleaned or "linked-audio"
    suffix = ".wav"
    return f"{cleaned[:MAX_DISPLAY_FILENAME_LENGTH - len(suffix)]}{suffix}"


def configured_proxy_url() -> str | None:
    """Validate the optional proxy URL without ever logging its credentials."""
    proxy_url = os.environ.get("PROXY_URL", "").strip()
    if not proxy_url:
        return None
    parsed = urlsplit(proxy_url)
    if parsed.scheme not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"} or not parsed.hostname:
        raise ValueError(
            "PROXY_URL must be an HTTP(S) or SOCKS proxy URL with a hostname."
        )
    return proxy_url


def configured_impersonation_target() -> tuple[str, ImpersonateTarget | None]:
    """Convert the operator-facing string into yt-dlp's Python API type."""
    configured = os.environ.get(
        "YTDLP_IMPERSONATE", DEFAULT_BROWSER_IMPERSONATION
    ).strip()
    if not configured:
        return configured, None
    try:
        return configured, ImpersonateTarget.from_str(configured)
    except ValueError as error:
        raise ValueError(
            "YTDLP_IMPERSONATE must use yt-dlp's browser-target format, such as 'chrome'."
        ) from error


def source_already_uploaded(s3_client, bucket: str, key: str, job_id: str) -> bool:
    """Recognize a successful prior upload when Lambda retries an invocation."""
    try:
        metadata = s3_client.head_object(Bucket=bucket, Key=key).get("Metadata", {})
    except ClientError as error:
        error_code = error.response.get("Error", {}).get("Code")
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return False
        if error_code == "403":
            # S3 deliberately returns 403 rather than 404 for a missing object
            # when a principal lacks ListBucket. The IAM policy grants a
            # prefix-limited ListBucket permission, but treating this as absent
            # also lets a safe first upload recover while that policy update is
            # propagating. PutObject remains the authoritative permission check.
            print(
                f"HeadObject returned 403 for durable input s3://{bucket}/{key}; "
                "treating it as not yet uploaded."
            )
            return False
        raise
    return metadata.get("job-id") == job_id


def set_source_filename(jobs_table, job_id: str, filename: str) -> dict[str, Any]:
    """Record the downloaded title while preserving the ingestion state."""
    response = jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #source_filename = :filename, #content_type = :content_type, #updated_at = :updated_at ADD #revision :one",
        ConditionExpression="#status = :ingestion",
        ExpressionAttributeNames={
            "#source_filename": "source_filename",
            "#content_type": "source_content_type",
            "#updated_at": "updated_at",
            "#revision": "revision",
            "#status": "status",
        },
        ExpressionAttributeValues={
            ":filename": filename,
            ":content_type": "audio/wav",
            ":updated_at": utc_now(),
            ":ingestion": INGESTION_STATUS,
            ":one": 1,
        },
        ReturnValues="ALL_NEW",
    )
    return response["Attributes"]


def mark_source_uploaded(jobs_table, job_id: str) -> dict[str, Any] | None:
    """Atomically hand the durable job to the S3/EventBridge processing path.

    EventBridge can start Batch immediately after ``upload_file`` returns.  The
    conditional status transition prevents a late yt-dlp invocation from
    changing a job that Batch has already advanced to stem processing.
    """
    try:
        response = jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #status = :uploaded, #source_uploaded = :true, #updated_at = :updated_at REMOVE #error ADD #revision :one",
            ConditionExpression="#status = :ingestion",
            ExpressionAttributeNames={
                "#status": "status",
                "#source_uploaded": "source_uploaded",
                "#updated_at": "updated_at",
                "#error": "error",
                "#revision": "revision",
            },
            ExpressionAttributeValues={
                ":uploaded": UPLOADED_STATUS,
                ":true": True,
                ":ingestion": INGESTION_STATUS,
                ":updated_at": utc_now(),
                ":one": 1,
            },
            ReturnValues="ALL_NEW",
        )
        return response["Attributes"]
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        # EventBridge can start Batch immediately after S3 accepts the object.
        # Preserve that newer worker status but still record that the original
        # input is durable, so a later Batch failure can offer it for playback.
        try:
            response = jobs_table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #source_uploaded = :true, #updated_at = :updated_at ADD #revision :one",
                ConditionExpression="attribute_not_exists(#source_uploaded) OR #source_uploaded <> :true",
                ExpressionAttributeNames={
                    "#source_uploaded": "source_uploaded",
                    "#updated_at": "updated_at",
                    "#revision": "revision",
                },
                ExpressionAttributeValues={
                    ":true": True,
                    ":updated_at": utc_now(),
                    ":one": 1,
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as update_error:
            if update_error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            print(f"Job {job_id} had already recorded the durable source upload.")
            return None
        print(f"Job {job_id} advanced before yt-dlp set its upload state; recorded durable source availability.")
        return response["Attributes"]


def fail_ingestion(jobs_table, connections_table, job_id: str, error: Exception) -> None:
    """Persist an ingestion failure only while this Lambda owns the job state."""
    error_message = str(error).strip() or (
        f"{error.__class__.__name__} occurred while ingesting the linked source."
    )
    try:
        response = jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #status = :failed, #error = :error, #updated_at = :updated_at ADD #revision :one",
            ConditionExpression="#status = :ingestion",
            ExpressionAttributeNames={
                "#status": "status",
                "#error": "error",
                "#updated_at": "updated_at",
                "#revision": "revision",
            },
            ExpressionAttributeValues={
                ":failed": "failed",
                ":ingestion": INGESTION_STATUS,
                ":error": error_message[:1_000],
                ":updated_at": utc_now(),
                ":one": 1,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as update_error:
        if update_error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            print(f"Not marking job {job_id} failed because it has already advanced beyond source ingestion.")
            return
        raise
    item = response["Attributes"]
    send_job_updated(connections_table, item, job_id, item.get("revision", 0))


def download_to_wav(source_url: str, output_directory: Path) -> tuple[Path, str]:
    """Download one non-playlist source and convert it to a local WAV file."""
    maximum_duration = configured_positive_int(
        "MAX_SOURCE_DURATION_SECONDS", DEFAULT_MAX_SOURCE_DURATION_SECONDS
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    output_template = str(output_directory / "source.%(ext)s")

    def reject_long_media(info: dict[str, Any], *, incomplete: bool) -> None:
        duration = info.get("duration")
        if duration is not None and duration > maximum_duration:
            raise ValueError(
                f"Source audio exceeds the {maximum_duration // 60}-minute duration limit."
            )
        return None

    options: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "match_filter": reject_long_media,
        "noplaylist": True,
        "ffmpeg_location": "/usr/local/bin",
        # yt-dlp's EJS solver needs a JavaScript runtime for current YouTube
        # challenge handling. The Lambda image includes Deno and the default
        # yt-dlp package extra includes the matching yt-dlp-ejs scripts.
        "js_runtimes": {"deno": {"path": "/usr/local/bin/deno"}},
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
        "quiet": True,
        "no_warnings": True,
    }
    proxy_url = configured_proxy_url()
    if proxy_url:
        options["proxy"] = proxy_url
    impersonation_name, impersonation_target = configured_impersonation_target()
    if impersonation_target:
        # curl-cffi is installed through the yt-dlp package extra and supplies
        # this browser TLS/request fingerprint. It is deliberately optional so
        # an operator can disable it with YTDLP_IMPERSONATE="" if necessary.
        # The Python embedding API requires an ImpersonateTarget object; the
        # CLI-only string form causes an AssertionError during YoutubeDL setup.
        options["impersonate"] = impersonation_target

    print(
        "yt-dlp request configuration: "
        f"proxy={'configured' if proxy_url else 'direct'}, "
        f"browser impersonation={impersonation_name or 'disabled'}, "
        "Deno EJS runtime enabled."
    )

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(source_url, download=True)
    except yt_dlp.utils.DownloadError as error:
        # Keep the persisted job error actionable without exposing source URLs,
        # proxy credentials, signed media URLs, or provider response bodies.
        if "HTTP Error 403" in str(error):
            raise RuntimeError(
                "The media host rejected the download (HTTP 403). Verify that "
                "the residential proxy supports this source and region; some "
                "providers also require user-authorized cookies."
            ) from error
        raise RuntimeError("yt-dlp could not download the linked media source.") from error
    wav_candidates = sorted(output_directory.glob("*.wav"))
    if not wav_candidates:
        raise FileNotFoundError("yt-dlp did not produce a WAV file.")
    return wav_candidates[0], display_filename(info.get("title"))


def ingest_source(event: dict[str, Any]) -> dict[str, Any]:
    """Download a linked source, then emit the single S3 upload Batch expects."""
    job_id = event.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("job_id is required.")
    upload_bucket = os.environ["UPLOADS_BUCKET_NAME"]

    s3_client = boto3.client("s3")
    jobs_table = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE_NAME"])
    connections_table = boto3.resource("dynamodb").Table(os.environ["CONNECTIONS_TABLE_NAME"])
    temporary_directory = Path("/tmp/clouddsp-ytdlp") / job_id
    shutil.rmtree(temporary_directory, ignore_errors=True)
    try:
        # This includes the initial HeadObject check. Every failure after we
        # know the job ID must reach fail_ingestion so the browser receives a
        # terminal job state instead of polling source_ingestion forever.
        job = get_job(jobs_table, job_id)
        if job.get("input_bucket") != upload_bucket:
            raise ValueError(f"Job {job_id} does not target the configured uploads bucket.")
        input_key = job.get("input_key")
        if not isinstance(input_key, str) or not input_key.startswith(f"uploads/{job_id}/"):
            raise ValueError(f"Job {job_id} has an invalid durable input key.")

        if job.get("status") == "failed":
            # Lambda's asynchronous invocation can retry after the first
            # attempt fails. A persisted terminal failure must be idempotent,
            # not turn every retry into another invocation error.
            print(f"Skipping retry for terminally failed linked-source job {job_id}.")
            return {"job_id": job_id, "status": "failed", "skipped": True}

        if source_already_uploaded(s3_client, upload_bucket, input_key, job_id):
            item = mark_source_uploaded(jobs_table, job_id)
            if item:
                send_job_updated(connections_table, item, job_id, item.get("revision", 0))
            print(f"Source for job {job_id} is already uploaded; skipping duplicate yt-dlp work.")
            return {"job_id": job_id, "input_key": input_key, "already_uploaded": True}

        if job.get("status") != INGESTION_STATUS:
            print(
                f"Skipping yt-dlp invocation for job {job_id}; "
                f"its state is already {job.get('status')!r}."
            )
            return {"job_id": job_id, "status": job.get("status"), "skipped": True}

        source_url = safe_source_url(event.get("source_url"))
        hostname = urlsplit(source_url).hostname or "unknown host"
        print(f"Downloading linked source for job {job_id} from host {hostname}.")
        wav_path, filename = download_to_wav(source_url, temporary_directory)
        # Record the display title before the S3 event can start Batch. This
        # keeps a readable history entry even when Batch starts immediately.
        item = set_source_filename(jobs_table, job_id, filename)
        send_job_updated(connections_table, item, job_id, item.get("revision", 0))

        print(f"Uploading converted source for job {job_id} to s3://{upload_bucket}/{input_key}.")
        s3_client.upload_file(
            str(wav_path),
            upload_bucket,
            input_key,
            ExtraArgs={
                "ContentType": "audio/wav",
                "Metadata": {
                    "job-id": job_id,
                    "stem-mode": str(job.get("stem_mode", "6-stems")),
                    "source-type": "yt-dlp",
                },
            },
        )
        print(f"Linked source upload complete for job {job_id}; EventBridge will submit Demucs.")
        item = mark_source_uploaded(jobs_table, job_id)
        if item:
            send_job_updated(connections_table, item, job_id, item.get("revision", 0))
        return {"job_id": job_id, "input_key": input_key, "filename": filename}
    except Exception as error:
        fail_ingestion(jobs_table, connections_table, job_id, error)
        raise
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle an asynchronous Job API invocation for one durable linked job."""
    print("Received yt-dlp ingestion event:", json.dumps({"job_id": event.get("job_id")}))
    result = ingest_source(event)
    return {"statusCode": 200, "body": json.dumps(result)}
