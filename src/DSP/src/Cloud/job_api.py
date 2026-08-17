"""Create and read durable CloudDSP processing jobs.

``POST /jobs`` creates the DynamoDB record before the browser uploads audio
and returns a size-constrained presigned POST contract for
``uploads/{job_id}/{filename}``.
``POST /jobs/link`` creates the same durable record and asynchronously invokes
the yt-dlp ingestion Lambda, which writes that input key itself.
``GET /jobs`` lists the authenticated user's recent jobs. ``GET /jobs/{job_id}``
returns the stored state with fresh presigned source and artifact URLs.
``DELETE /jobs/{job_id}`` permanently removes one terminal job and all of its
source, stem, MIDI, and tempo objects. Workers store S3 keys, rather than
expiring URLs, in the job record.

An API Gateway JWT authorizer must be attached before deployment.  This
handler uses the authenticated ``sub`` claim as the job owner and never trusts
a user ID supplied by the browser.
"""

import json
import os
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import PurePath
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from media_url_policy import MediaUrlPolicyError, validate_allowlisted_media_url


VALID_STEM_MODES = {"2-stems", "4-stems", "6-stems"}
TERMINAL_JOB_STATUSES = {"completed", "failed"}
DEFAULT_JOB_TTL_DAYS = 14
USER_JOBS_INDEX_NAME = "user_id-updated_at-index"
DEFAULT_MAX_SOURCE_BYTES = 256 * 1024 * 1024
SUPPORTED_AUDIO_MEDIA_TYPES = {
    ".wav": ("audio/wav", {"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"}),
    ".mp3": ("audio/mpeg", {"audio/mpeg", "audio/mp3"}),
    ".flac": ("audio/flac", {"audio/flac", "audio/x-flac"}),
    ".m4a": ("audio/mp4", {"audio/mp4", "audio/x-m4a", "audio/m4a"}),
    ".aac": ("audio/aac", {"audio/aac", "audio/x-aac"}),
    ".ogg": ("audio/ogg", {"audio/ogg", "application/ogg"}),
    ".opus": ("audio/ogg", {"audio/ogg", "audio/opus", "application/ogg"}),
    ".aiff": ("audio/aiff", {"audio/aiff", "audio/x-aiff"}),
    ".aif": ("audio/aiff", {"audio/aiff", "audio/x-aiff"}),
    ".webm": ("audio/webm", {"audio/webm"}),
}

_jobs = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE_NAME"])
_s3 = boto3.client("s3")
_lambda = boto3.client("lambda")


class RequestError(Exception):
    """An HTTP error caused by an invalid or unauthorized client request."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def utc_now() -> str:
    """Return an RFC 3339 UTC timestamp with second precision."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build an API Gateway HTTP API response."""
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, default=json_default),
    }


def json_default(value: Any) -> int | float:
    """Serialize DynamoDB ``Decimal`` values without losing integer revisions."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def parse_json_body(event: dict[str, Any]) -> dict[str, Any]:
    """Parse an optional JSON request body and return a JSON object."""
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raise RequestError(400, "Base64-encoded request bodies are not supported.")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise RequestError(400, "Request body must contain valid JSON.") from error
    if not isinstance(parsed, dict):
        raise RequestError(400, "Request body must be a JSON object.")
    return parsed


def authenticated_user_id(event: dict[str, Any]) -> str:
    """Return the principal established by an API Gateway JWT authorizer."""
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    claims = authorizer.get("jwt", {}).get("claims", {})
    user_id = claims.get("sub")
    if not user_id:
        # REST API/Lambda-authorizer compatibility for a future alternate API.
        user_id = authorizer.get("principalId")
    if not isinstance(user_id, str) or not user_id:
        raise RequestError(401, "Authentication is required.")
    return user_id


def safe_filename(value: Any) -> str:
    """Reject path traversal and empty names before constructing an S3 key."""
    if not isinstance(value, str) or not value.strip():
        raise RequestError(400, "filename is required.")
    filename = PurePath(value).name
    if filename in {"", ".", ".."} or filename != value or "\\" in value:
        raise RequestError(400, "filename must be a basename without path separators.")
    if len(filename) > 255:
        raise RequestError(400, "filename must be at most 255 characters.")
    return filename


def supported_source_content_type(filename: str, value: Any) -> str:
    """Validate the browser's filename/type pair and return a canonical MIME type."""
    extension = PurePath(filename).suffix.lower()
    supported = SUPPORTED_AUDIO_MEDIA_TYPES.get(extension)
    if not supported:
        raise RequestError(
            400,
            "Supported audio files are WAV, MP3, FLAC, M4A, AAC, OGG, Opus, AIFF, and WebM.",
        )

    canonical_type, accepted_types = supported
    if value in {None, "", "application/octet-stream"}:
        # Browser MIME detection is inconsistent for audio containers. The
        # extension whitelist decides the canonical S3 content type in that
        # case; Batch still probes the decoded file before Demucs runs.
        return canonical_type
    if not isinstance(value, str) or len(value) > 255:
        raise RequestError(400, "content_type must be a short string.")
    content_type = value.split(";", 1)[0].strip().lower()
    if content_type not in accepted_types:
        raise RequestError(400, f"{extension} must be uploaded with an audio content type.")
    return canonical_type


def bounded_source_size(value: Any, maximum: int) -> int:
    """Check the UI's exact File.size before issuing an S3 upload contract."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestError(400, "size_bytes must be an integer.")
    if value < 1 or value > maximum:
        raise RequestError(400, f"Audio files must be between 1 byte and {maximum} bytes.")
    return value


def safe_media_url(value: Any) -> str:
    """Accept one reviewed HTTPS media-page URL for the ingestion worker."""
    try:
        return validate_allowlisted_media_url(value)
    except MediaUrlPolicyError as error:
        raise RequestError(400, str(error)) from error


def configured_int(name: str, default: int, minimum: int = 1) -> int:
    """Read a positive integer environment variable with a safe default."""
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer.") from error
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}.")
    return value


def create_job(event: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Persist a queued job and generate the exact upload location."""
    payload = parse_json_body(event)
    filename = safe_filename(payload.get("filename"))
    stem_mode = payload.get("stem_mode", "6-stems")
    if stem_mode not in VALID_STEM_MODES:
        raise RequestError(400, "stem_mode must be 2-stems, 4-stems, or 6-stems.")

    content_type = supported_source_content_type(filename, payload.get("content_type"))
    maximum_source_bytes = configured_int("MAX_SOURCE_BYTES", DEFAULT_MAX_SOURCE_BYTES)
    source_size_bytes = bounded_source_size(payload.get("size_bytes"), maximum_source_bytes)

    job_id = str(uuid.uuid4())
    input_key = f"uploads/{job_id}/{filename}"
    created_at = utc_now()
    expires_at = int(time.time()) + configured_int(
        "JOB_TTL_DAYS", DEFAULT_JOB_TTL_DAYS
    ) * 24 * 60 * 60
    job = {
        "job_id": job_id,
        "user_id": user_id,
        "status": "upload_pending",
        "input_bucket": os.environ["UPLOADS_BUCKET_NAME"],
        "input_key": input_key,
        "source_filename": filename,
        "source_content_type": content_type,
        "source_size_bytes": source_size_bytes,
        "stem_mode": stem_mode,
        "stems": {},
        "midi": {},
        "revision": 1,
        "created_at": created_at,
        "updated_at": created_at,
        "expires_at": expires_at,
    }
    _jobs.put_item(
        Item=job,
        ConditionExpression="attribute_not_exists(job_id)",
    )

    upload_contract = _s3.generate_presigned_post(
        Bucket=job["input_bucket"],
        Key=input_key,
        Fields={
            "Content-Type": content_type,
            "x-amz-meta-job-id": job_id,
            "x-amz-meta-stem-mode": stem_mode,
        },
        Conditions=[
            {"Content-Type": content_type},
            {"x-amz-meta-job-id": job_id},
            {"x-amz-meta-stem-mode": stem_mode},
            ["content-length-range", 1, maximum_source_bytes],
        ],
        ExpiresIn=configured_int("UPLOAD_URL_EXPIRY_SECONDS", 300),
    )
    print(
        f"Created job {job_id} for authenticated user {user_id}; "
        f"declared source size is {source_size_bytes} bytes (limit {maximum_source_bytes})."
    )
    return {
        "job_id": job_id,
        "status": job["status"],
        "revision": job["revision"],
        "input_key": input_key,
        "expires_at": expires_at,
        "upload_url": upload_contract["url"],
        "upload_fields": upload_contract["fields"],
        "max_source_bytes": maximum_source_bytes,
    }


def create_link_job(event: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Create a durable job and hand source ingestion to the yt-dlp Lambda.

    The linked URL stays in the asynchronous invocation payload rather than in
    DynamoDB.  The table therefore continues to store only durable processing
    state and S3 keys, not a potentially sensitive query string.
    """
    function_arn = os.environ.get("YTDLP_FUNCTION_ARN")
    if not function_arn:
        raise RequestError(503, "Linked-source ingestion is not deployed.")

    payload = parse_json_body(event)
    source_url = safe_media_url(payload.get("source_url"))
    stem_mode = payload.get("stem_mode", "6-stems")
    if stem_mode not in VALID_STEM_MODES:
        raise RequestError(400, "stem_mode must be 2-stems, 4-stems, or 6-stems.")

    job_id = str(uuid.uuid4())
    # The final title is not known until yt-dlp reads the media metadata. The
    # S3 key must nevertheless be decided before invocation so Batch can use
    # the identical durable key and no extra S3 routing rule is required.
    input_key = f"uploads/{job_id}/linked-audio.wav"
    created_at = utc_now()
    expires_at = int(time.time()) + configured_int(
        "JOB_TTL_DAYS", DEFAULT_JOB_TTL_DAYS
    ) * 24 * 60 * 60
    job = {
        "job_id": job_id,
        "user_id": user_id,
        "status": "source_ingestion",
        "input_bucket": os.environ["UPLOADS_BUCKET_NAME"],
        "input_key": input_key,
        "source_filename": "linked-audio.wav",
        "source_content_type": "audio/wav",
        "source_type": "yt-dlp",
        "stem_mode": stem_mode,
        "stems": {},
        "midi": {},
        "revision": 1,
        "created_at": created_at,
        "updated_at": created_at,
        "expires_at": expires_at,
    }
    _jobs.put_item(Item=job, ConditionExpression="attribute_not_exists(job_id)")

    try:
        invocation = _lambda.invoke(
            FunctionName=function_arn,
            InvocationType="Event",
            Payload=json.dumps({"job_id": job_id, "source_url": source_url}).encode("utf-8"),
        )
        if invocation.get("StatusCode") not in {202, 200}:
            raise RuntimeError(f"Unexpected asynchronous invocation response: {invocation}")
    except Exception as error:
        # The job remains visible to its owner with a terminal reason rather
        # than leaving a source_ingestion entry that cannot make progress.
        _jobs.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #status = :failed, #error = :error, #updated_at = :updated_at ADD #revision :one",
            ExpressionAttributeNames={
                "#status": "status",
                "#error": "error",
                "#updated_at": "updated_at",
                "#revision": "revision",
            },
            ExpressionAttributeValues={
                ":failed": "failed",
                ":error": f"Could not start linked-source ingestion: {error}"[:1_000],
                ":updated_at": utc_now(),
                ":one": 1,
            },
        )
        print(f"Could not invoke yt-dlp for job {job_id}: {error}")
        raise RequestError(503, "Could not start linked-source ingestion.") from error

    print(f"Created linked-source job {job_id} for authenticated user {user_id}.")
    return {
        "job_id": job_id,
        "status": job["status"],
        "revision": job["revision"],
        "input_key": input_key,
        "expires_at": expires_at,
    }


def presigned_get_url(bucket: str, key: str) -> str:
    return _s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=configured_int("DOWNLOAD_URL_EXPIRY_SECONDS", 3600),
    )


def artifact_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    """Return a browser-safe job view, replacing stored keys with fresh URLs."""
    output_bucket = os.environ["PROCESSED_AUDIO_BUCKET_NAME"]
    result = {
        key: value
        for key, value in item.items()
        if key not in {"input_bucket", "input_key"}
    }
    input_bucket = item.get("input_bucket")
    input_key = item.get("input_key")
    linked_source_failed_before_upload = (
        item.get("source_type") == "yt-dlp"
        and item.get("status") == "failed"
        and not item.get("source_uploaded")
    )
    if input_bucket and input_key and not linked_source_failed_before_upload:
        # The original upload remains private. Its URL is generated only after
        # the authenticated caller has passed the ownership check in get_job.
        # A linked source that failed before its first S3 upload deliberately
        # has no original URL: signing the predetermined missing key produces
        # a misleading S3 403 in the browser.
        result["original_url"] = presigned_get_url(input_bucket, input_key)
    for collection_name in ("stems", "midi"):
        artifacts = item.get(collection_name) or {}
        rendered: dict[str, dict[str, Any]] = {}
        for stem_name, artifact in artifacts.items():
            rendered_artifact = dict(artifact)
            s3_key = rendered_artifact.pop("s3_key", None)
            bpm_key = rendered_artifact.pop("bpm_key", None)
            if s3_key:
                rendered_artifact["url"] = presigned_get_url(output_bucket, s3_key)
            if bpm_key:
                rendered_artifact["bpm_url"] = presigned_get_url(output_bucket, bpm_key)
            rendered[stem_name] = rendered_artifact
        result[collection_name] = rendered
    return result


def job_list_item(item: dict[str, Any]) -> dict[str, Any]:
    """Render the compact, non-sensitive item used by the saved-jobs library."""
    return {
        "job_id": item["job_id"],
        "source_filename": item.get("source_filename", "Untitled audio"),
        "status": item.get("status", "unknown"),
        "stem_mode": item.get("stem_mode"),
        "tempo": item.get("tempo"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "expires_at": item.get("expires_at"),
    }


def job_is_retained(item: dict[str, Any]) -> bool:
    """Hide records whose retention window has elapsed before TTL removes them."""
    expires_at = item.get("expires_at")
    if expires_at is None:
        return True
    try:
        return int(expires_at) > int(time.time())
    except (TypeError, ValueError):
        # A malformed legacy TTL must not be disclosed through the library.
        return False


def list_jobs(user_id: str) -> dict[str, list[dict[str, Any]]]:
    """List every non-expired job owned by one authenticated Cognito subject.

    ``user_id`` comes exclusively from the JWT authorizer.  Querying the GSI
    avoids a table scan and returns the most recently updated jobs first.
    DynamoDB pages query results at roughly 1 MB, so continue until the caller
    has the complete history retained by the table's TTL policy.
    """
    query = {
        "IndexName": os.environ.get("USER_JOBS_INDEX_NAME", USER_JOBS_INDEX_NAME),
        "KeyConditionExpression": Key("user_id").eq(user_id),
        "ScanIndexForward": False,
    }
    jobs: list[dict[str, Any]] = []
    while True:
        page = _jobs.query(**query)
        jobs.extend(
            job_list_item(item)
            for item in page.get("Items", [])
            if job_is_retained(item)
        )
        last_evaluated_key = page.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
        query["ExclusiveStartKey"] = last_evaluated_key

    print(f"Listed {len(jobs)} jobs for authenticated user {user_id}.")
    return {"jobs": jobs}


def get_job(event: dict[str, Any], user_id: str) -> dict[str, Any]:
    path_parameters = event.get("pathParameters") or {}
    job_id = path_parameters.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise RequestError(400, "job_id is required.")
    item = _jobs.get_item(Key={"job_id": job_id}).get("Item")
    if not item or not job_is_retained(item):
        raise RequestError(404, "Job not found.")
    if item.get("user_id") != user_id:
        # Do not reveal whether a job ID belongs to another user.
        raise RequestError(404, "Job not found.")
    return artifact_snapshot(item)


def canonical_job_id(event: dict[str, Any]) -> str:
    """Read and normalize the UUID used for a job-owned S3 prefix."""
    path_parameters = event.get("pathParameters") or {}
    job_id = path_parameters.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise RequestError(400, "job_id is required.")
    try:
        return str(uuid.UUID(job_id))
    except (AttributeError, ValueError) as error:
        raise RequestError(400, "job_id must be a UUID.") from error


def delete_s3_prefix_versions(bucket: str, prefix: str) -> int:
    """Permanently delete every current and noncurrent version under a prefix.

    Both audio buckets are versioned. Deleting only the current object would
    leave the audio recoverable as a noncurrent version and would continue to
    consume storage, so collect both object versions and delete markers first.
    The job UUID prefix keeps this bounded to the authenticated user's one job.
    """
    versions: list[dict[str, str]] = []
    key_marker: str | None = None
    version_id_marker: str | None = None

    while True:
        request: dict[str, str] = {"Bucket": bucket, "Prefix": prefix}
        if key_marker:
            request["KeyMarker"] = key_marker
        if version_id_marker:
            request["VersionIdMarker"] = version_id_marker
        page = _s3.list_object_versions(**request)
        versions.extend(
            {"Key": entry["Key"], "VersionId": entry["VersionId"]}
            for entry in [*page.get("Versions", []), *page.get("DeleteMarkers", [])]
        )
        if not page.get("IsTruncated"):
            break
        key_marker = page.get("NextKeyMarker")
        version_id_marker = page.get("NextVersionIdMarker")
        if not key_marker:
            raise RuntimeError(
                f"S3 returned a truncated version listing without a continuation marker for {prefix}."
            )

    deleted_count = 0
    for offset in range(0, len(versions), 1_000):
        batch = versions[offset:offset + 1_000]
        deletion = _s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": batch, "Quiet": True},
        )
        errors = deletion.get("Errors", [])
        if errors:
            failed_keys = ", ".join(error.get("Key", "unknown") for error in errors[:5])
            raise RuntimeError(f"Could not delete S3 objects under {prefix}: {failed_keys}")
        deleted_count += len(batch)
    return deleted_count


def delete_job(event: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Permanently delete one owned, terminal job and its job-scoped artifacts."""
    job_id = canonical_job_id(event)
    item = _jobs.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if not item or not job_is_retained(item) or item.get("user_id") != user_id:
        # Keep the same response for absent and another user's jobs so a
        # caller cannot enumerate jobs outside their account.
        raise RequestError(404, "Job not found.")
    if item.get("status") not in TERMINAL_JOB_STATUSES:
        raise RequestError(409, "Only completed or failed jobs can be deleted.")

    artifact_prefixes = (
        (os.environ["UPLOADS_BUCKET_NAME"], f"uploads/{job_id}/"),
        (os.environ["PROCESSED_AUDIO_BUCKET_NAME"], f"stems/{job_id}/"),
        (os.environ["PROCESSED_AUDIO_BUCKET_NAME"], f"midi/{job_id}/"),
    )
    deleted_objects = 0
    for bucket, prefix in artifact_prefixes:
        deleted_here = delete_s3_prefix_versions(bucket, prefix)
        deleted_objects += deleted_here
        print(f"Deleted {deleted_here} versioned S3 object(s) from s3://{bucket}/{prefix}.")

    try:
        _jobs.delete_item(
            Key={"job_id": job_id},
            ConditionExpression="#user_id = :user_id AND #status IN (:completed, :failed)",
            ExpressionAttributeNames={
                "#user_id": "user_id",
                "#status": "status",
            },
            ExpressionAttributeValues={
                ":user_id": user_id,
                ":completed": "completed",
                ":failed": "failed",
            },
        )
    except _jobs.meta.client.exceptions.ConditionalCheckFailedException as error:
        # The job's artifacts have already been removed. Do not report a
        # false success if its ownership or terminal status changed meanwhile.
        raise RequestError(409, "Job changed while it was being deleted. Refresh the library.") from error

    print(
        f"Permanently deleted terminal job {job_id} for authenticated user {user_id}; "
        f"removed {deleted_objects} S3 object version(s)."
    )
    return {"job_id": job_id, "deleted_objects": deleted_objects}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle authenticated CloudDSP job API requests."""
    route_key = event.get("routeKey")
    print(f"Received Job API route: {route_key}")
    try:
        user_id = authenticated_user_id(event)
        if route_key == "POST /jobs":
            return response(201, create_job(event, user_id))
        if route_key == "POST /jobs/link":
            return response(202, create_link_job(event, user_id))
        if route_key == "GET /jobs":
            return response(200, list_jobs(user_id))
        if route_key == "GET /jobs/{job_id}":
            return response(200, get_job(event, user_id))
        if route_key == "DELETE /jobs/{job_id}":
            return response(200, delete_job(event, user_id))
        raise RequestError(404, "Route not found.")
    except RequestError as error:
        return response(error.status_code, {"error": error.message})
    except Exception as error:  # Keep operational details in CloudWatch only.
        print(f"Unexpected Job API error: {error}")
        return response(500, {"error": "Internal server error."})
