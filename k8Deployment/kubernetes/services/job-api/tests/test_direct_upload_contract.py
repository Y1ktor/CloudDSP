"""Unit tests for the side-effect-free POST /jobs browser contract.

No test in this file registers a route, contacts Keycloak, writes PostgreSQL,
creates a job UUID, asks MinIO to sign a form, or uploads audio. Those actions
belong to the later small tasks after this request/response boundary is stable.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.direct_upload_contract import (
    MAX_SOURCE_UPLOAD_BYTES,
    DirectUploadJobCreatedResponse,
    DirectUploadJobRequest,
)


TEST_JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"


class DirectUploadJobRequestTests(unittest.TestCase):
    """Prove one browser request is narrow, supported, and canonicalized."""

    def test_accepts_a_known_audio_file_and_canonicalizes_browser_mime(self) -> None:
        """A browser-specific MP3 MIME still becomes CloudDSP's stable value."""

        request = DirectUploadJobRequest(
            filename="My Mix.MP3",
            content_type=" audio/mp3; codecs=unused ",
            size_bytes=1_024,
            stem_mode="4-stems",
        )

        self.assertEqual(request.filename, "My Mix.MP3")
        self.assertEqual(request.content_type, "audio/mp3")
        self.assertEqual(request.canonical_source_content_type, "audio/mpeg")
        self.assertEqual(request.stem_mode, "4-stems")
        self.assertEqual(request.size_bytes, 1_024)

    def test_uses_extension_when_a_browser_omits_or_generically_labels_mime(self) -> None:
        """Direct uploads remain usable on browsers with incomplete MIME detection."""

        without_type = DirectUploadJobRequest(filename="mix.aif", size_bytes=1)
        generic_type = DirectUploadJobRequest(
            filename="mix.ogg",
            content_type="application/octet-stream",
            size_bytes=1,
        )

        self.assertEqual(without_type.canonical_source_content_type, "audio/aiff")
        self.assertIsNone(without_type.content_type)
        self.assertEqual(generic_type.canonical_source_content_type, "audio/ogg")
        self.assertIsNone(generic_type.content_type)

    def test_rejects_paths_unsupported_extensions_and_mismatched_mime(self) -> None:
        """A future route cannot receive a path traversal or a misleading audio label."""

        invalid_payloads = (
            {"filename": "../mix.wav", "size_bytes": 100},
            {"filename": "folder/mix.wav", "size_bytes": 100},
            {"filename": r"folder\mix.wav", "size_bytes": 100},
            {"filename": "mix.txt", "size_bytes": 100},
            {"filename": "mix.flac", "content_type": "audio/mpeg", "size_bytes": 100},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    DirectUploadJobRequest(**payload)

    def test_rejects_extra_server_controlled_fields_non_integers_and_out_of_range_sizes(self) -> None:
        """The browser cannot supply ownership/state and cannot relax the size ceiling."""

        invalid_payloads = (
            {"filename": "mix.wav", "size_bytes": "100"},
            {"filename": "mix.wav", "size_bytes": True},
            {"filename": "mix.wav", "size_bytes": 0},
            {"filename": "mix.wav", "size_bytes": MAX_SOURCE_UPLOAD_BYTES + 1},
            {"filename": "mix.wav", "size_bytes": 100, "owner_sub": "browser-chosen-owner"},
            {"filename": "mix.wav", "size_bytes": 100, "status": "completed"},
            {"filename": "mix.wav", "size_bytes": 100, "stem_mode": "all-stems"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    DirectUploadJobRequest(**payload)


class DirectUploadJobCreatedResponseTests(unittest.TestCase):
    """Prove the future 201 payload has exactly the fields React needs."""

    def standard_response(self, **overrides: object) -> DirectUploadJobCreatedResponse:
        """Build one synthetic, non-secret signed-form response for each test."""

        response: dict[str, object] = {
            "job_id": TEST_JOB_ID,
            "status": "upload_pending",
            "revision": 1,
            "expires_at": datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
            "upload_url": "http://minio.localhost:8080/clouddsp-uploads",
            # These are representative field names only, not a real policy or
            # Signature V4 value. The presigned-upload test owns those details.
            "upload_fields": {"key": f"uploads/{TEST_JOB_ID}/mix.wav", "policy": "test-policy"},
            "max_source_bytes": MAX_SOURCE_UPLOAD_BYTES,
        }
        response.update(overrides)
        return DirectUploadJobCreatedResponse(**response)

    def test_serializes_the_complete_browser_contract(self) -> None:
        """The React direct-upload path can consume every returned field."""

        response = self.standard_response()
        payload = response.model_dump(mode="json")

        self.assertEqual(payload["job_id"], TEST_JOB_ID)
        self.assertEqual(payload["status"], "upload_pending")
        self.assertEqual(payload["revision"], 1)
        self.assertEqual(payload["expires_at"], "2026-09-20T12:00:00Z")
        self.assertEqual(payload["upload_url"], "http://minio.localhost:8080/clouddsp-uploads")
        self.assertEqual(payload["upload_fields"]["key"], f"uploads/{TEST_JOB_ID}/mix.wav")
        self.assertEqual(payload["max_source_bytes"], MAX_SOURCE_UPLOAD_BYTES)

    def test_rejects_invalid_server_response_shapes(self) -> None:
        """A future route cannot silently emit an incomplete or unsafe contract."""

        invalid_overrides = (
            {"job_id": "not-a-uuid"},
            {"job_id": TEST_JOB_ID.upper()},
            {"status": "source_uploaded"},
            {"revision": 0},
            {"expires_at": datetime(2026, 9, 20, 12, 0)},
            {"upload_url": ""},
            {"upload_fields": {}},
            {"upload_fields": {"file": "must-be-appended-by-browser"}},
            {"max_source_bytes": MAX_SOURCE_UPLOAD_BYTES + 1},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.standard_response(**overrides)
