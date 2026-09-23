"""Unit tests for authenticated three-job direct-upload ingress without a cluster."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Lock
from typing import Mapping
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs
from uuid import UUID

import authenticated_load_client as load_client
from authenticated_load_client import (
    AuthenticatedLoadClientError,
    AuthenticatedLoadClientSettings,
    build_controlled_load_wav,
    submit_and_write_authenticated_load_coordinates,
    submit_three_concurrent_six_stem_uploads,
)
from keycloak_identity_lifecycle import HttpResponse
from lifecycle_handoff import (
    COORDINATES_FILE_NAME,
    TemporaryLoadClientIdentity,
    prepare_empty_handoff_directory,
    read_authenticated_load_coordinates,
    read_client_outcome,
)


_JOB_IDS = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
)


class IngressTransport:
    """A thread-safe fake Keycloak/API/MinIO transport with no network capability."""

    def __init__(self, *, invalid_upload_origin: bool = False) -> None:
        self._lock = Lock()
        self.calls: list[dict[str, object]] = []
        self.invalid_upload_origin = invalid_upload_origin

    def request(
        self,
        *,
        operation: str,
        url: str,
        expected_statuses: frozenset[int],
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        with self._lock:
            self.calls.append(
                {
                    "operation": operation,
                    "url": url,
                    "expected_statuses": expected_statuses,
                    "method": method,
                    "data": data,
                    "headers": dict(headers or {}),
                }
            )
        if operation == "six-stem-load temporary-user token request":
            return _response(200, {"access_token": "temporary-user-token"})
        if operation == "six-stem-load authenticated identity request":
            return _response(200, {"subject": "44444444-4444-4444-8444-444444444444"})
        if operation.startswith("six-stem-load direct-upload Job creation "):
            ordinal = int(operation.rsplit(" ", 1)[-1])
            request_payload = json.loads(data or b"{}")
            job_id = _JOB_IDS[ordinal - 1]
            filename = request_payload["filename"]
            browser_origin = "http://wrong-minio.localhost:8080" if self.invalid_upload_origin else "http://minio.localhost:8080"
            return _response(
                201,
                {
                    "job_id": job_id,
                    "status": "upload_pending",
                    "revision": 1,
                    "expires_at": "2026-09-23T00:00:00+00:00",
                    "upload_url": f"{browser_origin}/clouddsp-uploads",
                    "upload_fields": {
                        "key": f"uploads/{job_id}/{filename}",
                        "Content-Type": "audio/wav",
                        "x-amz-meta-job-id": job_id,
                        "x-amz-meta-stem-mode": "6-stems",
                        "x-amz-algorithm": "AWS4-HMAC-SHA256",
                    },
                    "max_source_bytes": 268435456,
                },
            )
        if operation.startswith("six-stem-load presigned source upload "):
            return _response(204)
        raise AssertionError(f"Unexpected operation: {operation}")


def _response(status: int, payload: object = None) -> HttpResponse:
    """Create one fake small HTTP response body."""

    return HttpResponse(
        status=status,
        headers={},
        body=b"" if payload is None else json.dumps(payload).encode("utf-8"),
    )


def settings() -> AuthenticatedLoadClientSettings:
    """Return the exact private routes required by the reviewed local profile."""

    return AuthenticatedLoadClientSettings(
        keycloak_internal_base_url="http://clouddsp-keycloak.clouddsp-data.svc:8080",
        application_realm="clouddsp",
        job_api_internal_base_url="http://clouddsp-job-api.clouddsp-app.svc:80",
        minio_internal_base_url="http://clouddsp-minio.clouddsp-data.svc:9000",
        handoff_directory="/var/run/clouddsp-six-stem-load",
    )


def identity() -> TemporaryLoadClientIdentity:
    """Use a fake temporary user configuration with no administrator value."""

    return TemporaryLoadClientIdentity(
        run_marker="loadrun01",
        client_id="clouddsp-six-stem-load-loadrun01",
        username="six-stem-load-loadrun01",
        password="temporary-user-password",
    )


class AuthenticatedLoadClientTests(unittest.TestCase):
    """Keep ingress concurrent, API-driven, signed-form-only, and metadata-bound."""

    def test_controlled_wavs_are_valid_distinct_bounded_audio(self) -> None:
        """The load uses ordinary valid audio, not a zero-byte or mocked source."""

        sources = tuple(build_controlled_load_wav(ordinal=ordinal) for ordinal in (1, 2, 3))

        self.assertEqual(len(set(sources)), 3)
        for source in sources:
            # 44-byte RIFF header + (44,100 frames/sec × 2 sec × 2 channels
            # × 2-byte PCM samples) is roughly 345 KiB.  This test asserts a
            # practical range rather than an accidental decimal conversion.
            self.assertGreater(len(source), 350_000)
            self.assertLess(len(source), 400_000)
            self.assertEqual(source[:4], b"RIFF")
            self.assertEqual(source[8:12], b"WAVE")

    def test_submissions_use_one_user_token_three_normal_api_jobs_and_three_presigned_uploads(self) -> None:
        """No direct database, S3 credential, or queue shortcut can make this pass."""

        transport = IngressTransport()
        ingress_result = submit_three_concurrent_six_stem_uploads(
            settings=settings(), identity=identity(), transport=transport
        )
        submitted = ingress_result.coordinates.jobs

        self.assertEqual([submission.job_id for submission in submitted], list(_JOB_IDS))
        self.assertEqual([submission.ordinal for submission in submitted], [1, 2, 3])
        self.assertEqual(len({submission.source_sha256 for submission in submitted}), 3)
        self.assertEqual(ingress_result.coordinates.subject, "44444444-4444-4444-8444-444444444444")
        self.assertEqual(ingress_result.coordinates.stem_mode, "6-stems")
        operations = [call["operation"] for call in transport.calls]
        self.assertEqual(operations.count("six-stem-load temporary-user token request"), 1)
        self.assertEqual(operations.count("six-stem-load authenticated identity request"), 1)
        self.assertEqual(len([item for item in operations if "direct-upload Job creation" in item]), 3)
        self.assertEqual(len([item for item in operations if "presigned source upload" in item]), 3)

        direct_creations = [call for call in transport.calls if "direct-upload Job creation" in call["operation"]]
        for call in direct_creations:
            payload = json.loads(call["data"])
            self.assertEqual(payload["stem_mode"], "6-stems")
            self.assertEqual(payload["content_type"], "audio/wav")
            self.assertTrue(payload["filename"].startswith("six-stem-load-loadrun01-"))
            self.assertGreater(payload["size_bytes"], 350_000)

        uploads = [call for call in transport.calls if "presigned source upload" in call["operation"]]
        for call in uploads:
            self.assertEqual(call["url"], "http://clouddsp-minio.clouddsp-data.svc:9000/clouddsp-uploads")
            self.assertTrue(call["headers"]["Content-Type"].startswith("multipart/form-data; boundary="))
            # Form controls include a Content-Disposition header before their
            # value.  Keep the expected byte fragment small so a failed test
            # cannot print the whole controlled WAV body in its assertion.
            self.assertIn(b'name="x-amz-meta-stem-mode"\r\n\r\n6-stems', call["data"])
            self.assertIn(b'Content-Disposition: form-data; name="file";', call["data"])

    def test_complete_ingress_writes_only_a_strict_private_coordinate_handoff(self) -> None:
        """The later broker receives a complete claim, never a token or upload form."""

        transport = IngressTransport()
        with tempfile.TemporaryDirectory() as temporary_directory:
            handoff_directory = Path(temporary_directory)
            os.chmod(handoff_directory, 0o700)
            prepare_empty_handoff_directory(handoff_directory)

            result = submit_and_write_authenticated_load_coordinates(
                settings=settings(),
                identity=identity(),
                handoff_directory=handoff_directory,
                transport=transport,
            )
            received = read_authenticated_load_coordinates(handoff_directory)

        self.assertEqual(received, result.coordinates)
        self.assertEqual([job.job_id for job in received.jobs], list(_JOB_IDS))
        self.assertEqual(received.subject, "44444444-4444-4444-8444-444444444444")

    def test_token_form_uses_only_temporary_user_values(self) -> None:
        """The broker's Keycloak administrator credentials never cross the handoff boundary."""

        transport = IngressTransport()
        submit_three_concurrent_six_stem_uploads(settings=settings(), identity=identity(), transport=transport)

        token_call = next(call for call in transport.calls if call["operation"] == "six-stem-load temporary-user token request")
        token_form = parse_qs(token_call["data"].decode("utf-8"), strict_parsing=True)
        self.assertEqual(token_form["client_id"], [identity().client_id])
        self.assertEqual(token_form["username"], [identity().username])
        self.assertEqual(token_form["password"], [identity().password])
        self.assertNotIn("KC_BOOTSTRAP_ADMIN", token_call["data"].decode("utf-8"))

    def test_rejects_a_form_with_the_wrong_browser_minio_origin_before_uploading(self) -> None:
        """An API/configuration regression cannot redirect an in-cluster test upload elsewhere."""

        transport = IngressTransport(invalid_upload_origin=True)

        with self.assertRaisesRegex(AuthenticatedLoadClientError, "direct uploads failed"):
            submit_three_concurrent_six_stem_uploads(settings=settings(), identity=identity(), transport=transport)

        self.assertFalse(any("presigned source upload" in call["operation"] for call in transport.calls))

    def test_failed_ingress_never_publishes_partial_coordinates(self) -> None:
        """A future broker cannot observe a subset of the three requested Jobs."""

        transport = IngressTransport(invalid_upload_origin=True)
        with tempfile.TemporaryDirectory() as temporary_directory:
            handoff_directory = Path(temporary_directory)
            os.chmod(handoff_directory, 0o700)
            prepare_empty_handoff_directory(handoff_directory)

            with self.assertRaisesRegex(AuthenticatedLoadClientError, "direct uploads failed"):
                submit_and_write_authenticated_load_coordinates(
                    settings=settings(),
                    identity=identity(),
                    handoff_directory=handoff_directory,
                    transport=transport,
                )

            self.assertFalse((handoff_directory / COORDINATES_FILE_NAME).exists())

    def test_executable_entrypoint_signals_success_and_failure_without_network_in_tests(self) -> None:
        """The manifest's command reaches the tested ingress helper and wakes the broker on failure."""

        completed = type("Completed", (), {"coordinates": type("Coordinates", (), {"jobs": (1, 2, 3)})()})()
        with (
            patch.object(load_client.AuthenticatedLoadClientSettings, "from_environment", return_value=settings()),
            patch.object(load_client, "wait_for_temporary_load_identity", return_value=identity()),
            patch.object(load_client, "submit_and_write_authenticated_load_coordinates", return_value=completed),
            patch.object(load_client, "write_client_outcome") as write_outcome,
        ):
            self.assertEqual(load_client.main(), 0)
        self.assertEqual(write_outcome.call_args.kwargs["outcome"], "succeeded")

        with (
            patch.object(load_client.AuthenticatedLoadClientSettings, "from_environment", return_value=settings()),
            patch.object(load_client, "wait_for_temporary_load_identity", return_value=identity()),
            patch.object(load_client, "submit_and_write_authenticated_load_coordinates", side_effect=RuntimeError()),
            patch.object(load_client, "write_client_outcome") as write_outcome,
        ):
            self.assertEqual(load_client.main(), 1)
        self.assertEqual(write_outcome.call_args.kwargs["outcome"], "failed")

    def test_module_process_exits_nonzero_on_startup_failure(self) -> None:
        """Kubernetes must see exit 1, not Python's discarded main() return value."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = os.environ.copy()
            environment.update({
                "SIX_STEM_LOAD_HANDOFF_DIR": temporary_directory,
                # The fixed-endpoint guard rejects this before any network call.
                "MINIO_INTERNAL_BASE_URL": "http://invalid-minio.invalid:9000",
            })
            process = subprocess.run(
                [sys.executable, str(Path(load_client.__file__).resolve())],
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            self.assertEqual(process.returncode, 1)
            self.assertIn("Six-stem authenticated client failed", process.stderr)
            self.assertEqual(read_client_outcome(Path(temporary_directory)), "failed")


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
