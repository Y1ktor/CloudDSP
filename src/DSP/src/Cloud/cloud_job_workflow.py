"""Shared durable-job helpers for CloudDSP Batch and MIDI workers.

Workers store artifact keys and progress in DynamoDB before emitting a small
WebSocket notification.  The notification is deliberately not the result
payload: a browser always retrieves a fresh snapshot from the Job API.
"""

import json
import math
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


TERMINAL_MIDI_STATES = {"ready", "failed"}
TEMPO_MIN_BPM = 70.0
TEMPO_MAX_BPM = 180.0
TEMPO_CLUSTER_BPM = 3.0
TEMPO_CLUSTER_RATIO = 0.03


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


def to_dynamodb_value(value: Any) -> Any:
    """Convert Python floats recursively because DynamoDB resources require Decimal."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: to_dynamodb_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_dynamodb_value(item) for item in value]
    return value


def numeric_value(value: Any) -> float | None:
    """Return a finite numeric value from DynamoDB Decimal or ordinary numbers."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_tempo(bpm: Any) -> float | None:
    """Fold clear half/double-time candidates into CloudDSP's canonical tempo range."""
    normalized = numeric_value(bpm)
    if normalized is None or normalized <= 0:
        return None
    while normalized < TEMPO_MIN_BPM:
        normalized *= 2
    while normalized > TEMPO_MAX_BPM:
        normalized /= 2
    return round(normalized, 2)


def tempo_candidate(stem_name: str, state: dict[str, Any]) -> dict[str, Any] | None:
    """Validate one persisted extractor candidate for job-level tempo selection."""
    candidate = state.get("tempo_candidate")
    if not isinstance(candidate, dict) or not candidate.get("credible"):
        return None
    bpm = normalize_tempo(candidate.get("bpm"))
    if bpm is None:
        return None

    confidence = str(candidate.get("confidence", "low"))
    confidence_weight = {"high": 3.0, "medium": 2.0, "low": 1.0}.get(confidence, 1.0)
    beat_count = numeric_value(candidate.get("beat_count")) or 0.0
    weight = confidence_weight * min(1.5, max(0.5, beat_count / 8.0))
    return {
        "stem_name": stem_name,
        "bpm": bpm,
        "confidence": confidence,
        "weight": weight,
        "source": str(candidate.get("source", state.get("extractor", "unknown"))),
    }


def weighted_median(candidates: list[dict[str, Any]]) -> float:
    """Return a robust weighted median for an already-agreeing tempo cluster."""
    ordered = sorted(candidates, key=lambda candidate: candidate["bpm"])
    midpoint = sum(candidate["weight"] for candidate in ordered) / 2
    running_weight = 0.0
    for candidate in ordered:
        running_weight += candidate["weight"]
        if running_weight >= midpoint:
            return float(candidate["bpm"])
    return float(ordered[-1]["bpm"])


def derive_job_tempo(midi_states: dict[str, Any]) -> dict[str, Any]:
    """Select one durable tempo: qualified ADTOF drums, then robust consensus.

    ADTOF's beat candidate is privileged only when that Lambda has marked it
    credible. Otherwise, candidate tempos are folded for obvious half/double
    time values, clustered, and resolved with a weighted median. A fallback is
    deliberately labelled as unknown rather than as a detected tempo.
    """
    candidates = [
        candidate
        for stem_name, state in midi_states.items()
        if isinstance(state, dict) and state.get("status") == "ready"
        for candidate in [tempo_candidate(stem_name, state)]
        if candidate is not None
    ]

    # Only the ADTOF worker has validated that detected drum hits corroborate
    # its beat tracker. A generic ``drums`` state must therefore join the
    # normal consensus rather than gaining this preference by name alone.
    drum_candidate = next(
        (
            candidate
            for candidate in candidates
            if candidate["stem_name"].lower() == "drums"
            and candidate["source"] == "adtof_drums"
        ),
        None,
    )
    if drum_candidate:
        return {
            "bpm": drum_candidate["bpm"],
            "source": "adtof_drums",
            "confidence": "high",
            "candidate_count": 1,
            "contributing_stems": [drum_candidate["stem_name"]],
        }

    non_vocal_candidates = [
        candidate for candidate in candidates if "vocal" not in candidate["stem_name"].lower()
    ]
    candidates = non_vocal_candidates or candidates
    if not candidates:
        return {
            "bpm": 120.0,
            "source": "fallback",
            "confidence": "unknown",
            "candidate_count": 0,
            "contributing_stems": [],
        }
    if len(candidates) == 1:
        candidate = candidates[0]
        return {
            "bpm": candidate["bpm"],
            "source": "single_stem",
            "confidence": "low",
            "candidate_count": 1,
            "contributing_stems": [candidate["stem_name"]],
        }

    clusters: list[list[dict[str, Any]]] = []
    for seed in candidates:
        tolerance = max(TEMPO_CLUSTER_BPM, seed["bpm"] * TEMPO_CLUSTER_RATIO)
        clusters.append([
            candidate for candidate in candidates
            if abs(candidate["bpm"] - seed["bpm"]) <= tolerance
        ])
    best_cluster = max(
        clusters,
        key=lambda cluster: (sum(candidate["weight"] for candidate in cluster), len(cluster)),
    )
    return {
        "bpm": weighted_median(best_cluster),
        "source": "consensus",
        "confidence": "medium",
        "candidate_count": len(best_cluster),
        "contributing_stems": sorted(candidate["stem_name"] for candidate in best_cluster),
    }


def tempo_changed(existing: Any, proposed: dict[str, Any]) -> bool:
    """Avoid a revision bump when a state update leaves the resolved tempo unchanged."""
    if not isinstance(existing, dict):
        return True
    current_bpm = numeric_value(existing.get("bpm"))
    proposed_bpm = numeric_value(proposed.get("bpm"))
    return (
        current_bpm is None
        or proposed_bpm is None
        or abs(current_bpm - proposed_bpm) > 0.01
        or existing.get("source") != proposed.get("source")
        or existing.get("confidence") != proposed.get("confidence")
        or numeric_value(existing.get("candidate_count")) != numeric_value(proposed.get("candidate_count"))
        or existing.get("contributing_stems") != proposed.get("contributing_stems")
    )


def update_job_tempo(
    jobs_table,
    job_id: str,
    tempo: dict[str, Any],
    *,
    expected_revision: Any,
) -> dict[str, Any]:
    """Persist tempo only if no concurrent MIDI worker changed the job first."""
    response = jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #tempo = :tempo, #updated_at = :updated_at ADD #revision :one",
        ConditionExpression="attribute_exists(job_id) AND #revision = :expected_revision",
        ExpressionAttributeNames={
            "#tempo": "tempo",
            "#updated_at": "updated_at",
            "#revision": "revision",
        },
        ExpressionAttributeValues={
            ":tempo": to_dynamodb_value(tempo),
            ":updated_at": utc_now(),
            ":one": 1,
            ":expected_revision": expected_revision,
        },
        ReturnValues="ALL_NEW",
    )
    return response["Attributes"]


def update_job_status(
    jobs_table,
    job_id: str,
    status: str,
    *,
    error: str | None = None,
    expected_revision: Any | None = None,
) -> dict[str, Any]:
    """Update top-level state, optionally only when the job has not changed."""
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
    condition_expression = "attribute_exists(job_id)"
    if expected_revision is not None:
        values[":expected_revision"] = expected_revision
        condition_expression += " AND #revision = :expected_revision"
    response = jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression=expression + " ADD #revision :one",
        ConditionExpression=condition_expression,
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
            ":midi": to_dynamodb_value(midi_state),
            ":updated_at": utc_now(),
            ":one": 1,
        },
        ReturnValues="ALL_NEW",
    )
    # Resolve tempo from the durable state rather than a Lambda's local event.
    # Extractors complete concurrently. Conditional revision updates make a
    # stale worker reload and recompute instead of overwriting a newer
    # consensus with its partial view of the job.
    item = response["Attributes"]
    while True:
        tempo = derive_job_tempo(item.get("midi") or {})
        if not tempo_changed(item.get("tempo"), tempo):
            break
        try:
            item = update_job_tempo(
                jobs_table,
                job_id,
                tempo,
                expected_revision=item.get("revision", 0),
            )
            break
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            print(f"Job {job_id} changed while resolving tempo; retrying from the latest state.")
            item = get_job(jobs_table, job_id)

    # Apply the aggregate status using the same optimistic concurrency rule.
    # In particular, a worker with an older partial snapshot must never change
    # an already completed job back to ``midi_processing``.
    while True:
        item = get_job(jobs_table, job_id)
        midi = item.get("midi") or {}
        states = [entry.get("status") for entry in midi.values() if isinstance(entry, dict)]
        if states and all(state in TERMINAL_MIDI_STATES for state in states):
            desired_status = "completed" if all(state == "ready" for state in states) else "failed"
        elif item.get("status") in {"failed", "completed"}:
            return item
        else:
            desired_status = "midi_processing"

        if item.get("status") == desired_status:
            return item
        try:
            return update_job_status(
                jobs_table,
                job_id,
                desired_status,
                expected_revision=item.get("revision", 0),
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            print(f"Job {job_id} changed while resolving final status; retrying from the latest state.")


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
