"""Shared durable-job helpers for CloudDSP Batch and MIDI workers.

Workers store artifact keys and progress in DynamoDB before emitting a small
WebSocket notification.  The notification is deliberately not the result
payload: a browser always retrieves a fresh snapshot from the Job API.
"""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


TERMINAL_MIDI_STATES = {"ready", "failed"}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def job_id_from_key(file_key: str, prefix: str) -> str:
    """Extract a job ID from the required ``prefix/{job_id}/...`` key shape."""
    parts = PurePosixPath(file_key).parts
    if len(parts) < 3 or parts[0] != prefix or not parts[1]:
        raise ValueError(f"Expected S3 key in the form {prefix}/{{job_id}}/filename, got {file_key!r}.")
    return parts[1]


def get_job(jobs_table, job_id: str) -> dict[str, Any]:
    item = jobs_table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if not item:
        raise ValueError(f"Job {job_id} does not exist; refusing to process an untracked artifact.")
    return item


def update_job_status(jobs_table, job_id: str, status: str, *, error: str | None = None) -> dict[str, Any]:
    """Update a top-level job state and increment its monotonic revision."""
    names = {"#status": "status", "#updated_at": "updated_at", "#revision": "revision"}
    values: dict[str, Any] = {
        ":status": status,
        ":updated_at": utc_now(),
        ":one": 1,
    }
    expression = "SET #status = :status, #updated_at = :updated_at"
    if error:
        names["#error"] = "error"
        values[":error"] = error[:1000]
        expression += ", #error = :error"
    response = jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression=expression + " ADD #revision :one",
        ConditionExpression="attribute_exists(job_id)",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return response["Attributes"]


def record_stem_results(jobs_table, job_id: str, stem_keys: dict[str, str]) -> dict[str, Any]:
    """Atomically publish all Demucs stem keys and queue their MIDI work."""
    stems = {name: {"status": "ready", "s3_key": key} for name, key in stem_keys.items()}
    midi = {
        name: {
            "status": "queued",
            "extractor": "adtof" if name.lower() == "drums" else "basic_pitch",
        }
        for name in stem_keys
    }
    response = jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression=(
            "SET #stems = :stems, #midi = :midi, #status = :status, "
            "#updated_at = :updated_at REMOVE #error ADD #revision :one"
        ),
        ConditionExpression="attribute_exists(job_id)",
        ExpressionAttributeNames={
            "#stems": "stems",
            "#midi": "midi",
            "#status": "status",
            "#updated_at": "updated_at",
            "#error": "error",
            "#revision": "revision",
        },
        ExpressionAttributeValues={
            ":stems": stems,
            ":midi": midi,
            ":status": "midi_processing",
            ":updated_at": utc_now(),
            ":one": 1,
        },
        ReturnValues="ALL_NEW",
    )
    return response["Attributes"]


def record_midi_state(
    jobs_table,
    job_id: str,
    stem_name: str,
    midi_state: dict[str, Any],
) -> dict[str, Any]:
    """Persist one extractor state and derive the aggregate terminal status."""
    response = jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #midi.#stem = :midi, #updated_at = :updated_at ADD #revision :one",
        ConditionExpression="attribute_exists(job_id)",
        ExpressionAttributeNames={
            "#midi": "midi",
            "#stem": stem_name,
            "#updated_at": "updated_at",
            "#revision": "revision",
        },
        ExpressionAttributeValues={
            ":midi": midi_state,
            ":updated_at": utc_now(),
            ":one": 1,
        },
        ReturnValues="ALL_NEW",
    )
    item = response["Attributes"]
    midi = item.get("midi") or {}
    states = [entry.get("status") for entry in midi.values() if isinstance(entry, dict)]
    if states and all(state in TERMINAL_MIDI_STATES for state in states):
        final_status = "completed" if all(state == "ready" for state in states) else "failed"
        if item.get("status") != final_status:
            item = update_job_status(jobs_table, job_id, final_status)
    elif item.get("status") not in {"failed", "completed"}:
        item = update_job_status(jobs_table, job_id, "midi_processing")
    return item


def send_job_updated(
    connections_table,
    job: dict[str, Any],
    job_id: str,
    revision: int | float,
) -> None:
    """Best-effort notify every active socket subscribed to a user's job."""
    websocket_url = os.environ.get("WEBSOCKET_API_URL")
    user_id = job.get("user_id")
    if not websocket_url or not user_id:
        print("Skipping job notification (missing WebSocket URL or job owner).")
        return

    index_name = os.environ.get("USER_CONNECTIONS_INDEX_NAME", "user_id-last_seen_at-index")
    response = connections_table.query(
        IndexName=index_name,
        KeyConditionExpression=Key("user_id").eq(user_id),
        FilterExpression=Attr("job_ids").contains(job_id),
    )
    records = response.get("Items", [])
    while response.get("LastEvaluatedKey"):
        response = connections_table.query(
            IndexName=index_name,
            KeyConditionExpression=Key("user_id").eq(user_id),
            FilterExpression=Attr("job_ids").contains(job_id),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        records.extend(response.get("Items", []))

    payload = json.dumps({"type": "job_updated", "job_id": job_id, "revision": int(revision)}).encode("utf-8")
    client = boto3.client("apigatewaymanagementapi", endpoint_url=websocket_url)
    delivered = 0
    for connection in records:
        connection_id = connection.get("connection_id")
        if not connection_id:
            continue
        try:
            client.post_to_connection(ConnectionId=connection_id, Data=payload)
            delivered += 1
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "GoneException":
                # The connection table's TTL will remove a stale entry. Workers
                # deliberately have no DeleteItem permission.
                print(f"WebSocket connection {connection_id} is gone; it will expire from the registry.")
            else:
                print(f"Warning: Could not notify WebSocket connection {connection_id}: {error}")
    print(f"Sent job_updated revision {revision} for job {job_id} to {delivered} connection(s).")


def fail_job(jobs_table, connections_table, job_id: str, error: Exception | str) -> None:
    """Persist a terminal worker failure before notifying the user."""
    message = str(error)
    try:
        item = update_job_status(jobs_table, job_id, "failed", error=message)
        send_job_updated(connections_table, item, job_id, item.get("revision", 0))
    except ClientError as update_error:
        print(f"Unable to persist failure for job {job_id}: {update_error}")
