"""Focused composition tests for the authenticated direct-upload POST route.

These tests replace every external boundary with a mock. They prove ordering
and the browser-safe response without contacting PostgreSQL, MinIO, Keycloak,
Docker, or Kubernetes. A later, separate smoke test will exercise the route
through the deployed Service with a real Keycloak token.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from app.authentication import AuthenticatedPrincipal, require_authenticated_principal
from app.database import CreatedDirectUploadJob, DatabaseUnavailable
from app.direct_upload_contract import DirectUploadJobRequest
from app.main import app, create_direct_upload_job
from app.object_storage import ObjectStorageConfigurationError, ObjectStorageSettings
from app.presigned_upload import (
    PresignedPost,
    PresignedUploadSigningError,
)


FIXED_JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
FIXED_EXPIRY = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


async def invoke_json_asgi(*, method: str, path: str, payload: object) -> tuple[int, dict[str, object]]:
    """Invoke the ASGI app without adding an HTTP client test dependency.

    The helper sends one complete JSON body and captures the response emitted
    by FastAPI. It is used only for the framework-level malformed-body case;
    ordinary route composition tests below call the route function directly.
    """

    body = json.dumps(payload).encode("utf-8")
    response_messages: list[dict[str, object]] = []
    request_delivered = False

    async def receive() -> dict[str, object]:
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        response_messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )

    start = next(message for message in response_messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in response_messages
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), json.loads(response_body)


class CreateDirectUploadRouteTests(unittest.TestCase):
    """Prove the route joins authentication, database, and signing safely."""

    def setUp(self) -> None:
        self.principal = AuthenticatedPrincipal(subject="verified-keycloak-subject")
        self.submission = DirectUploadJobRequest(
            filename="mix.wav",
            content_type="audio/x-wav",
            size_bytes=1_024,
            stem_mode="4-stems",
        )
        self.storage = ObjectStorageSettings(
            internal_endpoint="http://minio.test.svc:9000",
            public_endpoint="http://minio.localhost:8080",
            uploads_bucket="clouddsp-uploads",
            region="us-east-1",
            addressing_style="path",
            access_key="not-a-real-access-key",
            secret_key="not-a-real-secret-key",
        )
        self.created_job = CreatedDirectUploadJob(
            job_id=FIXED_JOB_ID,
            input_object_key=f"uploads/{FIXED_JOB_ID}/mix.wav",
            status="upload_pending",
            revision=1,
            expires_at=FIXED_EXPIRY,
        )
        self.upload_post = PresignedPost(
            url="http://minio.localhost:8080/clouddsp-uploads",
            fields={
                "key": f"uploads/{FIXED_JOB_ID}/mix.wav",
                "policy": "short-lived-policy",
                "x-amz-meta-job-id": FIXED_JOB_ID,
            },
            expires_in_seconds=300,
            maximum_source_bytes=256 * 1024 * 1024,
        )

    def test_route_persists_before_returning_one_constrained_upload_contract(self) -> None:
        """Configuration, durable intent, and local signing happen in order."""

        call_order: list[str] = []

        def load_storage() -> ObjectStorageSettings:
            call_order.append("storage")
            return self.storage

        def create_row(**kwargs: object) -> CreatedDirectUploadJob:
            call_order.append("database")
            return self.created_job

        def sign_post(*args: object, **kwargs: object) -> PresignedPost:
            call_order.append("signing")
            return self.upload_post

        with (
            patch("app.main.ObjectStorageSettings.from_environment", side_effect=load_storage),
            patch("app.main.create_direct_upload_pending_job", side_effect=create_row) as create_row_mock,
            patch("app.main.create_constrained_source_upload_post", side_effect=sign_post) as sign_post_mock,
        ):
            response = create_direct_upload_job(self.submission, self.principal)

        self.assertEqual(call_order, ["storage", "database", "signing"])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        payload = json.loads(response.body)
        self.assertEqual(payload["job_id"], FIXED_JOB_ID)
        self.assertEqual(payload["status"], "upload_pending")
        self.assertEqual(payload["revision"], 1)
        # FastAPI's JSON encoder writes the zero UTC offset in its compact Z
        # notation. Either this exact string or a future timezone offset is
        # still governed by the response model's timezone-aware validation.
        self.assertEqual(payload["expires_at"], "2026-09-20T12:00:00Z")
        self.assertEqual(payload["upload_url"], self.upload_post.url)
        self.assertEqual(payload["upload_fields"], dict(self.upload_post.fields))
        self.assertEqual(payload["max_source_bytes"], 256 * 1024 * 1024)
        self.assertNotIn("input_object_key", payload)
        self.assertNotIn("input_bucket", payload)

        create_row_mock.assert_called_once_with(
            owner_sub="verified-keycloak-subject",
            request=self.submission,
            input_bucket="clouddsp-uploads",
        )
        sign_post_mock.assert_called_once_with(
            self.storage,
            job_id=FIXED_JOB_ID,
            input_object_key=f"uploads/{FIXED_JOB_ID}/mix.wav",
            content_type="audio/wav",
            stem_mode="4-stems",
        )

    def test_bad_storage_configuration_creates_no_job(self) -> None:
        """The route fails before the transaction if Pod configuration is unsafe."""

        with (
            patch(
                "app.main.ObjectStorageSettings.from_environment",
                side_effect=ObjectStorageConfigurationError("private configuration detail"),
            ),
            patch("app.main.create_direct_upload_pending_job") as create_row_mock,
            patch("app.main.create_constrained_source_upload_post") as sign_post_mock,
        ):
            response = create_direct_upload_job(self.submission, self.principal)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), {"error": "Direct upload is temporarily unavailable."})
        create_row_mock.assert_not_called()
        sign_post_mock.assert_not_called()

    def test_database_failure_does_not_issue_an_upload_permission(self) -> None:
        """No browser form exists unless the durable authorization row exists."""

        with (
            patch("app.main.ObjectStorageSettings.from_environment", return_value=self.storage),
            patch(
                "app.main.create_direct_upload_pending_job",
                side_effect=DatabaseUnavailable("private database detail"),
            ),
            patch("app.main.create_constrained_source_upload_post") as sign_post_mock,
        ):
            response = create_direct_upload_job(self.submission, self.principal)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), {"error": "Direct upload is temporarily unavailable."})
        sign_post_mock.assert_not_called()

    def test_local_signing_failure_hides_implementation_detail(self) -> None:
        """A signer regression cannot expose MinIO policy material to the browser."""

        with (
            patch("app.main.ObjectStorageSettings.from_environment", return_value=self.storage),
            patch("app.main.create_direct_upload_pending_job", return_value=self.created_job),
            patch(
                "app.main.create_constrained_source_upload_post",
                side_effect=PresignedUploadSigningError("private SDK detail"),
            ),
        ):
            response = create_direct_upload_job(self.submission, self.principal)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), {"error": "Direct upload is temporarily unavailable."})

    def test_malformed_browser_body_uses_the_cloud_compatible_400_contract(self) -> None:
        """Framework validation detail remains private and does not become a 422 API."""

        app.dependency_overrides[require_authenticated_principal] = lambda: self.principal
        self.addCleanup(app.dependency_overrides.clear)

        status_code, payload = asyncio.run(
            invoke_json_asgi(
                method="POST",
                path="/jobs",
                payload={
                    "filename": "mix.wav",
                    "content_type": "audio/wav",
                    "size_bytes": "not-an-integer",
                    "unexpected": "browser-controlled value",
                },
            )
        )

        self.assertEqual(status_code, 400)
        self.assertEqual(payload, {"error": "Invalid direct-upload request."})

    def test_get_and_post_routes_share_a_path_but_not_an_http_method(self) -> None:
        """Saved-job history and direct job creation remain distinct operations."""

        methods = {
            frozenset(route.methods)
            for route in app.routes
            if getattr(route, "path", None) == "/jobs"
        }
        self.assertIn(frozenset({"GET"}), methods)
        self.assertIn(frozenset({"POST"}), methods)
