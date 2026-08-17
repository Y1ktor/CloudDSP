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
import math
import os
import re
import shutil
import socket
import wave
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import boto3
import yt_dlp
from botocore.exceptions import ClientError
from yt_dlp.networking.impersonate import ImpersonateTarget

from cloud_job_workflow import get_job, send_job_updated, utc_now
from media_url_policy import MediaUrlPolicyError, validate_allowlisted_media_url


INGESTION_STATUS = "source_ingestion"
UPLOADED_STATUS = "upload_pending"
DEFAULT_MAX_SOURCE_DURATION_SECONDS = 500
DEFAULT_MAX_SOURCE_DOWNLOAD_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_CONVERTED_WAV_BYTES = 128 * 1024 * 1024
MAX_DISPLAY_FILENAME_LENGTH = 255
DEFAULT_BROWSER_IMPERSONATION = "chrome"


class SourceSizeLimitError(ValueError):
    """Raised before a linked source can consume unbounded Lambda storage."""


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
    """Validate an allowlisted invocation URL before passing it to yt-dlp."""
    try:
        source_url = validate_allowlisted_media_url(value)
    except MediaUrlPolicyError as error:
        raise ValueError(str(error)) from error

    parsed = urlsplit(source_url)
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        # A public-looking hostname can still resolve to a private address.
        # Reject it before yt-dlp gets the opportunity to contact internal
        # services. DNS rebinding remains an egress-control concern, so this is
        # a defense in depth rather than a substitute for restricted networking.
        try:
            resolved_addresses = {
                result[4][0]
                for result in socket.getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM)
            }
        except socket.gaierror as error:
            raise ValueError("source_url hostname could not be resolved.") from error
        if not resolved_addresses:
            raise ValueError("source_url hostname did not resolve to an address.")
        for resolved_address in resolved_addresses:
            if not ipaddress.ip_address(resolved_address).is_global:
                raise ValueError("source_url hostname resolves to a private or reserved IP address.")
    else:
        if not address.is_global:
            raise ValueError("source_url must not target a private or reserved IP address.")
    return source_url


def display_filename(title: Any) -> str:
    """Create a safe display-only filename without changing the stable S3 key."""
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(title or "linked-audio")).strip(" ._")
    cleaned = cleaned or "linked-audio"
    suffix = ".wav"
    return f"{cleaned[:MAX_DISPLAY_FILENAME_LENGTH - len(suffix)]}{suffix}"


def configured_proxy_url() -> str | None:
    """Read and validate the optional proxy credential without logging it."""
    parameter_name = os.environ.get("PROXY_SSM_PARAMETER_NAME", "").strip()
    if not parameter_name:
        return None

    try:
        parameter = boto3.client("ssm").get_parameter(
            Name=parameter_name,
            WithDecryption=True,
        )
        proxy_url = str(parameter["Parameter"]["Value"]).strip()
    except Exception as error:
        # Do not include the AWS error message: it may echo a configured name
        # and must never cause a credential-bearing value to reach CloudWatch.
        raise RuntimeError("Unable to retrieve the configured proxy credential.") from error

    if not proxy_url:
        raise ValueError("The configured proxy credential is empty.")
    parsed = urlsplit(proxy_url)
    if parsed.scheme not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"} or not parsed.hostname:
        raise ValueError(
            "The configured proxy credential must be an HTTP(S) or SOCKS proxy URL with a hostname."
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


def set_source_filename(
    jobs_table, job_id: str, filename: str, source_size_bytes: int
) -> dict[str, Any]:
    """Record the normalized WAV title and actual size before its S3 upload."""
    response = jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression=(
            "SET #source_filename = :filename, #content_type = :content_type, "
            "#source_size_bytes = :source_size_bytes, #updated_at = :updated_at "
            "ADD #revision :one"
        ),
        ConditionExpression="#status = :ingestion",
        ExpressionAttributeNames={
            "#source_filename": "source_filename",
            "#content_type": "source_content_type",
            "#source_size_bytes": "source_size_bytes",
            "#updated_at": "updated_at",
            "#revision": "revision",
            "#status": "status",
        },
        ExpressionAttributeValues={
            ":filename": filename,
            ":content_type": "audio/wav",
            ":source_size_bytes": source_size_bytes,
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


def as_positive_size(value: Any) -> int | None:
    """Read yt-dlp's optional exact/approximate byte fields safely."""
    if isinstance(value, bool):
        return None
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None
    return size if size > 0 else None


def selected_download_entries(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Find yt-dlp's selected format entries, retaining safe fallbacks."""
    for key in ("requested_downloads", "requested_formats"):
        entries = info.get(key)
        if isinstance(entries, list):
            selected = [entry for entry in entries if isinstance(entry, dict)]
            if selected:
                return selected
    return [info]


def validate_source_metadata(
    info: dict[str, Any], maximum_duration: int, maximum_download_bytes: int
) -> None:
    """Reject known oversized media before yt-dlp starts its transfer."""
    duration = info.get("duration")
    if duration is not None:
        try:
            numeric_duration = float(duration)
        except (TypeError, ValueError) as error:
            raise ValueError("Linked media has an invalid duration.") from error
        if not math.isfinite(numeric_duration) or numeric_duration <= 0:
            raise ValueError("Linked media must have a finite, positive duration.")
        if numeric_duration > maximum_duration:
            raise ValueError(
                f"Source audio exceeds the {maximum_duration}-second duration limit."
            )
    else:
        # A hard time limit cannot be guaranteed if an extractor gives us no
        # duration. Reject rather than permit an unbounded long source to use
        # Lambda temporary storage and then GPU Batch capacity.
        raise ValueError("Could not determine the linked media duration before download.")

    entries = selected_download_entries(info)
    sizes = [
        as_positive_size(entry.get("filesize"))
        or as_positive_size(entry.get("filesize_approx"))
        for entry in entries
    ]
    if all(size is not None for size in sizes):
        total_size = sum(size for size in sizes if size is not None)
        print(
            f"yt-dlp metadata reports {total_size} source bytes for {len(entries)} selected format(s); "
            f"limit is {maximum_download_bytes}."
        )
        if total_size > maximum_download_bytes:
            raise SourceSizeLimitError(
                f"Linked media exceeds the {maximum_download_bytes}-byte download limit."
            )
    else:
        # DASH/HLS and some providers do not publish a trustworthy size. Do
        # not reject legitimate media on that basis; the progress hook below
        # remains the authoritative limit while bytes stream to /tmp.
        print(
            "yt-dlp metadata has no complete source-size estimate; "
            f"a {maximum_download_bytes}-byte transfer cap will be enforced."
        )


def validate_normalized_wav_duration(wav_path: Path, maximum_duration: int) -> float:
    """Verify the normalized PCM output also respects the time cap."""
    try:
        with wave.open(str(wav_path), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            duration = wav_file.getnframes() / frame_rate if frame_rate else 0
    except (OSError, wave.Error) as error:
        raise ValueError("yt-dlp produced an invalid WAV file.") from error
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Converted WAV must have a finite, positive duration.")
    if duration > maximum_duration:
        raise ValueError(
            f"Converted WAV exceeds the {maximum_duration}-second duration limit."
        )
    return duration


def download_to_wav(source_url: str, output_directory: Path) -> tuple[Path, str]:
    """Preflight, size-limit, download, and normalize one non-playlist source."""
    maximum_duration = configured_positive_int(
        "MAX_SOURCE_DURATION_SECONDS", DEFAULT_MAX_SOURCE_DURATION_SECONDS
    )
    maximum_download_bytes = configured_positive_int(
        "MAX_SOURCE_DOWNLOAD_BYTES", DEFAULT_MAX_SOURCE_DOWNLOAD_BYTES
    )
    maximum_wav_bytes = configured_positive_int(
        "MAX_CONVERTED_WAV_BYTES", DEFAULT_MAX_CONVERTED_WAV_BYTES
    )
    print(
        "yt-dlp intake limits: "
        f"duration={maximum_duration}s, encoded-download={maximum_download_bytes} bytes, "
        f"normalized-wav={maximum_wav_bytes} bytes."
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    output_template = str(output_directory / "source.%(ext)s")

    def reject_oversized_transfer(status: dict[str, Any]) -> None:
        downloaded_bytes = as_positive_size(status.get("downloaded_bytes"))
        if downloaded_bytes and downloaded_bytes > maximum_download_bytes:
            raise SourceSizeLimitError(
                f"Linked media exceeds the {maximum_download_bytes}-byte download limit."
            )

    options: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "ffmpeg_location": "/usr/local/bin",
        # yt-dlp's EJS solver needs a JavaScript runtime for current YouTube
        # challenge handling. The Lambda image includes Deno and the default
        # yt-dlp package extra includes the matching yt-dlp-ejs scripts.
        "js_runtimes": {"deno": {"path": "/usr/local/bin/deno"}},
        # Keep the largest accepted 500-second track around 84 MiB. Demucs
        # does not benefit from preserving source multichannel/high-rate PCM.
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "postprocessor_args": ["-ac", "2", "-ar", "44100", "-sample_fmt", "s16"],
        "max_filesize": maximum_download_bytes,
        "progress_hooks": [reject_oversized_transfer],
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
        # Retrieve metadata before transferring media. This catches a known
        # duration/size at the inexpensive extractor stage; a second extraction
        # gets short-lived provider download URLs immediately before transfer.
        with yt_dlp.YoutubeDL(options) as metadata_downloader:
            metadata = metadata_downloader.extract_info(source_url, download=False)
        if not isinstance(metadata, dict):
            raise ValueError("yt-dlp did not return media metadata.")
        validate_source_metadata(metadata, maximum_duration, maximum_download_bytes)

        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(source_url, download=True)
    except SourceSizeLimitError:
        raise
    except yt_dlp.utils.DownloadError as error:
        if "max-filesize" in str(error).lower() or "maximum file size" in str(error).lower():
            raise SourceSizeLimitError(
                f"Linked media exceeds the {maximum_download_bytes}-byte download limit."
            ) from error
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
    wav_path = wav_candidates[0]
    wav_size_bytes = wav_path.stat().st_size
    if wav_size_bytes > maximum_wav_bytes:
        raise SourceSizeLimitError(
            f"Converted WAV exceeds the {maximum_wav_bytes}-byte output limit."
        )
    wav_duration = validate_normalized_wav_duration(wav_path, maximum_duration)
    print(
        f"yt-dlp produced normalized WAV ({wav_size_bytes} bytes, {wav_duration:.2f} seconds; "
        f"size limit {maximum_wav_bytes}, duration limit {maximum_duration})."
    )
    return wav_path, display_filename(info.get("title"))


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
        wav_size_bytes = wav_path.stat().st_size
        # Record the display title before the S3 event can start Batch. This
        # keeps a readable history entry even when Batch starts immediately.
        item = set_source_filename(jobs_table, job_id, filename, wav_size_bytes)
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
                    "source-size-bytes": str(wav_size_bytes),
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
