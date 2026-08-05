"""Maintain authenticated CloudDSP WebSocket subscriptions.

The handler records only the browser's current connection and subscriptions.
Audio workers persist results in DynamoDB first and send a small
``job_updated`` notification afterwards; the browser then refreshes the job
through the HTTP API to obtain current presigned URLs.
"""

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError


CONNECTION_TTL_SECONDS = 12 * 60 * 60

_connections = boto3.resource("dynamodb").Table(os.environ["CONNECTIONS_TABLE_NAME"])
_jobs = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE_NAME"])


class WebSocketRequestError(Exception):
    """A client message that should receive a 4xx-style WebSocket response."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def now() -> tuple[str, int]:
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return timestamp, int(time.time())


def connection_context(event: dict[str, Any]) -> tuple[str, str]:
    request_context = event.get("requestContext", {})
    connection_id = request_context.get("connectionId")
    if not isinstance(connection_id, str) or not connection_id:
        raise WebSocketRequestError("Connection ID is missing.")
    return connection_id, websocket_user_id(event)


def websocket_user_id(event: dict[str, Any]) -> str:
    """Read the identity injected by a WebSocket Lambda authorizer."""
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    user_id = authorizer.get("principalId")
    if not user_id:
        user_id = authorizer.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise WebSocketRequestError("Authentication is required.")
    return user_id


def management_client(event: dict[str, Any]):
    request_context = event["requestContext"]
    domain_name = request_context["domainName"]
    stage = request_context["stage"]
    return boto3.client(
        "apigatewaymanagementapi",
        endpoint_url=f"https://{domain_name}/{stage}",
    )


def post(event: dict[str, Any], connection_id: str, message: dict[str, Any]) -> None:
    """Write a response to the current socket and clean up a gone connection."""
    try:
        management_client(event).post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(message).encode("utf-8"),
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "GoneException":
            print(f"Deleting stale WebSocket connection {connection_id}.")
            _connections.delete_item(Key={"connection_id": connection_id})
            return
        raise


def parse_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body") or "{}"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise WebSocketRequestError("Message body must contain valid JSON.") from error
    if not isinstance(parsed, dict):
        raise WebSocketRequestError("Message body must be a JSON object.")
    return parsed


def put_connection(connection_id: str, user_id: str) -> None:
    timestamp, epoch_seconds = now()
    _connections.put_item(
        Item={
            "connection_id": connection_id,
            "user_id": user_id,
            "job_ids": [],
            "connected_at": timestamp,
            "last_seen_at": timestamp,
            "expires_at": epoch_seconds + CONNECTION_TTL_SECONDS,
        }
    )
    print(f"Registered WebSocket connection {connection_id} for user {user_id}.")


def update_heartbeat(connection_id: str, user_id: str) -> None:
    timestamp, epoch_seconds = now()
    _connections.update_item(
        Key={"connection_id": connection_id},
        UpdateExpression="SET last_seen_at = :last_seen_at, expires_at = :expires_at",
        ConditionExpression="user_id = :user_id",
        ExpressionAttributeValues={
            ":last_seen_at": timestamp,
            ":expires_at": epoch_seconds + CONNECTION_TTL_SECONDS,
            ":user_id": user_id,
        },
    )


def subscribe(event: dict[str, Any], connection_id: str, user_id: str) -> None:
    payload = parse_body(event)
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise WebSocketRequestError("job_id is required for subscribe.")

    job = _jobs.get_item(Key={"job_id": job_id}).get("Item")
    if not job or job.get("user_id") != user_id:
        raise WebSocketRequestError("Job not found.")

    connection = _connections.get_item(Key={"connection_id": connection_id}).get("Item")
    if not connection or connection.get("user_id") != user_id:
        raise WebSocketRequestError("Connection is not registered.")
    job_ids = set(connection.get("job_ids", []))
    job_ids.add(job_id)
    timestamp, epoch_seconds = now()
    _connections.update_item(
        Key={"connection_id": connection_id},
        UpdateExpression=(
            "SET job_ids = :job_ids, last_seen_at = :last_seen_at, expires_at = :expires_at"
        ),
        ConditionExpression="user_id = :user_id",
        ExpressionAttributeValues={
            ":job_ids": sorted(job_ids),
            ":last_seen_at": timestamp,
            ":expires_at": epoch_seconds + CONNECTION_TTL_SECONDS,
            ":user_id": user_id,
        },
    )
    post(
        event,
        connection_id,
        {"type": "job_updated", "job_id": job_id, "revision": job.get("revision", 0)},
    )
    print(f"Subscribed connection {connection_id} to job {job_id}.")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle API Gateway ``$connect``, ``subscribe``, and heartbeat routes."""
    route_key = event.get("requestContext", {}).get("routeKey", "$default")
    print(f"Received WebSocket route: {route_key}")
    connection_id: str | None = None
    try:
        connection_id, user_id = connection_context(event)
        if route_key == "$connect":
            put_connection(connection_id, user_id)
        elif route_key == "$disconnect":
            _connections.delete_item(Key={"connection_id": connection_id})
            print(f"Deleted WebSocket connection {connection_id}.")
        elif route_key == "subscribe":
            subscribe(event, connection_id, user_id)
        elif route_key == "heartbeat":
            update_heartbeat(connection_id, user_id)
            post(event, connection_id, {"type": "heartbeat_ack", "server_time": now()[0]})
        else:
            raise WebSocketRequestError("Unsupported WebSocket action.")
        return {"statusCode": 200}
    except WebSocketRequestError as error:
        print(f"Rejected WebSocket request: {error.message}")
        if connection_id:
            post(event, connection_id, {"type": "error", "error": error.message})
        return {"statusCode": 400, "body": error.message}
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"statusCode": 401, "body": "Connection is not authorized."}
        print(f"WebSocket DynamoDB error: {error}")
        return {"statusCode": 500, "body": "Internal server error."}
    except Exception as error:
        print(f"Unexpected WebSocket handler error: {error}")
        return {"statusCode": 500, "body": "Internal server error."}
