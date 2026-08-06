"""Create and read durable CloudDSP processing jobs.

``POST /jobs`` creates the DynamoDB record before the browser uploads audio
and returns a presigned PUT URL for ``uploads/{job_id}/{filename}``.
``GET /jobs/{job_id}`` returns the stored state with fresh presigned artifact
URLs.  Workers store S3 keys, rather than expiring URLs, in the job record.

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


VALID_STEM_MODES = {"2-stems", "4-stems", "6-stems"}
DEFAULT_JOB_TTL_DAYS = 7

_jobs = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE_NAME"])
_s3 = boto3.client("s3")


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


def presigned_get_url(bucket: str, key: str) -> str:
    return _s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=configured_int("DOWNLOAD_URL_EXPIRY_SECONDS", 3600),
    )


def artifact_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    """Return a browser-safe job view, replacing artifact keys with fresh URLs."""
    output_bucket = os.environ["PROCESSED_AUDIO_BUCKET_NAME"]
    result = {
        key: value
        for key, value in item.items()
        if key not in {"input_bucket", "input_key", "expires_at"}
    }
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


def get_job(event: dict[str, Any], user_id: str) -> dict[str, Any]:
    job_id = event.get("pathParameters", {}).get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise RequestError(400, "job_id is required.")
    item = _jobs.get_item(Key={"job_id": job_id}).get("Item")
    if not item:
        raise RequestError(404, "Job not found.")
    if item.get("user_id") != user_id:
        # Do not reveal whether a job ID belongs to another user.
        raise RequestError(404, "Job not found.")
    return artifact_snapshot(item)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle ``POST /jobs`` and ``GET /jobs/{job_id}`` HTTP API requests."""
    route_key = event.get("routeKey")
    print(f"Received Job API route: {route_key}")
    try:
        user_id = authenticated_user_id(event)
        if route_key == "POST /jobs":
            return response(201, create_job(event, user_id))
        if route_key == "GET /jobs/{job_id}":
            return response(200, get_job(event, user_id))
        raise RequestError(404, "Route not found.")
    except RequestError as error:
        return response(error.status_code, {"error": error.message})
    except Exception as error:  # Keep operational details in CloudWatch only.
        print(f"Unexpected Job API error: {error}")
        return response(500, {"error": "Internal server error."})
