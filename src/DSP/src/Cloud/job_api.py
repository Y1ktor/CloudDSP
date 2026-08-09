"""Create and read durable CloudDSP processing jobs.

``POST /jobs`` creates the DynamoDB record before the browser uploads audio
and returns a presigned PUT URL for ``uploads/{job_id}/{filename}``.
``POST /jobs/link`` creates the same durable record and asynchronously invokes
the yt-dlp ingestion Lambda, which writes that input key itself.
``GET /jobs`` lists the authenticated user's recent jobs. ``GET /jobs/{job_id}``
returns the stored state with fresh presigned source and artifact URLs. Workers
store S3 keys, rather than expiring URLs, in the job record.

An API Gateway JWT authorizer must be attached before deployment.  This
handler uses the authenticated ``sub`` claim as the job owner and never trusts
a user ID supplied by the browser.
"""

import json
import ipaddress
import os
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import PurePath
from typing import Any
from urllib.parse import urlsplit

import boto3
from boto3.dynamodb.conditions import Key


VALID_STEM_MODES = {"2-stems", "4-stems", "6-stems"}
DEFAULT_JOB_TTL_DAYS = 7
USER_JOBS_INDEX_NAME = "user_id-updated_at-index"

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


def safe_media_url(value: Any) -> str:
    """Accept one absolute source URL for the yt-dlp ingestion worker."""
    if not isinstance(value, str) or not value.strip():
        raise RequestError(400, "source_url is required.")
    if len(value) > 2_048:
        raise RequestError(400, "source_url is too long.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise RequestError(400, "source_url must be an absolute HTTP or HTTPS URL.")
    if parsed.username or parsed.password:
        raise RequestError(400, "source_url must not contain credentials.")
    if parsed.hostname.lower() == "localhost":
        raise RequestError(400, "source_url must not target localhost.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        # A hostname is valid, but the Lambda's network controls must block
        # private destinations if it resolves to one at request time.
        pass
    else:
        if not address.is_global:
            raise RequestError(400, "source_url must not target a private or reserved IP address.")
    return value


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

    content_type = payload.get("content_type") or "application/octet-stream"
    if not isinstance(content_type, str) or len(content_type) > 255:
        raise RequestError(400, "content_type must be a short string.")

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

    upload_url = _s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": job["input_bucket"],
            "Key": input_key,
            "ContentType": content_type,
            "Metadata": {"job-id": job_id, "stem-mode": stem_mode},
        },
        ExpiresIn=configured_int("UPLOAD_URL_EXPIRY_SECONDS", 300),
        HttpMethod="PUT",
    )
    print(f"Created job {job_id} for authenticated user {user_id}.")
    return {
        "job_id": job_id,
        "status": job["status"],
        "revision": job["revision"],
        "input_key": input_key,
        "upload_url": upload_url,
        "upload_headers": {
            "Content-Type": content_type,
            "x-amz-meta-job-id": job_id,
            "x-amz-meta-stem-mode": stem_mode,
        },
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
        if key not in {"input_bucket", "input_key", "expires_at"}
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
    job_id = event.get("pathParameters", {}).get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise RequestError(400, "job_id is required.")
    item = _jobs.get_item(Key={"job_id": job_id}).get("Item")
    if not item or not job_is_retained(item):
        raise RequestError(404, "Job not found.")
    if item.get("user_id") != user_id:
        # Do not reveal whether a job ID belongs to another user.
        raise RequestError(404, "Job not found.")
    return artifact_snapshot(item)


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
        raise RequestError(404, "Route not found.")
    except RequestError as error:
        return response(error.status_code, {"error": error.message})
    except Exception as error:  # Keep operational details in CloudWatch only.
        print(f"Unexpected Job API error: {error}")
        return response(500, {"error": "Internal server error."})
