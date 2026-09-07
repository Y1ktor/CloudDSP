"""Unit tests for locally generated, constrained MinIO upload contracts.

These tests use fake MinIO credentials and do not need MinIO, Docker, or the
Kubernetes cluster. Boto3's presigned POST operation is a local Signature V4
calculation, so decoding its generated policy lets the test prove what MinIO
will enforce without ever uploading an audio file.
"""

from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import MagicMock, patch

from app.object_storage import ObjectStorageSettings
from app.presigned_upload import (
    DEFAULT_UPLOAD_POST_EXPIRY_SECONDS,
    MAX_SOURCE_UPLOAD_BYTES,
    PresignedUploadContractError,
    create_constrained_source_upload_post,
)


TEST_JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
TEST_OBJECT_KEY = f"uploads/{TEST_JOB_ID}/mix.wav"


def decoded_policy(fields: dict[str, str]) -> dict[str, object]:
    """Decode the opaque browser form policy so assertions stay readable."""

    return json.loads(base64.b64decode(fields["policy"]))


class PresignedSourceUploadPostTests(unittest.TestCase):
    """Prove the helper signs a narrow browser form and rejects broad inputs."""

    def setUp(self) -> None:
        # These are synthetic credentials. They deliberately resemble neither
        # a checked-in Secret template nor a live MinIO application identity.
        self.settings = ObjectStorageSettings(
            internal_endpoint="http://clouddsp-minio.clouddsp-data.svc:9000",
            public_endpoint="http://minio.localhost:8080",
            uploads_bucket="clouddsp-uploads",
            region="us-east-1",
            addressing_style="path",
            access_key="unit-test-access-key",
            secret_key="unit-test-secret-key-not-real",
        )

    def create_contract(self, **overrides: object):
        """Create one standard direct-upload form, overriding one argument as needed."""

        arguments: dict[str, object] = {
            "job_id": TEST_JOB_ID,
            "input_object_key": TEST_OBJECT_KEY,
            "content_type": "audio/wav",
            "stem_mode": "4-stems",
        }
        arguments.update(overrides)
        return create_constrained_source_upload_post(self.settings, **arguments)

    def test_real_sdk_generates_one_path_style_policy_without_network(self) -> None:
        """The actual SDK policy binds bucket, key, MIME type, metadata, and size."""

        # If future code accidentally performs S3 I/O while presigning, the
        # patched low-level transport makes this test fail rather than allowing
        # a unit test to reach a real endpoint.
        with patch("botocore.httpsession.URLLib3Session.send") as transport_send:
            contract = self.create_contract()

        transport_send.assert_not_called()
        self.assertEqual(contract.url, "http://minio.localhost:8080/clouddsp-uploads")
        self.assertEqual(contract.expires_in_seconds, DEFAULT_UPLOAD_POST_EXPIRY_SECONDS)
        self.assertEqual(contract.maximum_source_bytes, MAX_SOURCE_UPLOAD_BYTES)
        self.assertEqual(contract.fields["key"], TEST_OBJECT_KEY)
        self.assertEqual(contract.fields["Content-Type"], "audio/wav")
        self.assertEqual(contract.fields["x-amz-meta-job-id"], TEST_JOB_ID)
        self.assertEqual(contract.fields["x-amz-meta-stem-mode"], "4-stems")
        self.assertEqual(contract.fields["x-amz-algorithm"], "AWS4-HMAC-SHA256")

        policy = decoded_policy(dict(contract.fields))
        conditions = policy["conditions"]
        self.assertIn({"bucket": "clouddsp-uploads"}, conditions)
        self.assertIn({"key": TEST_OBJECT_KEY}, conditions)
        self.assertIn({"Content-Type": "audio/wav"}, conditions)
        self.assertIn({"x-amz-meta-job-id": TEST_JOB_ID}, conditions)
        self.assertIn({"x-amz-meta-stem-mode": "4-stems"}, conditions)
        self.assertIn(["content-length-range", 1, MAX_SOURCE_UPLOAD_BYTES], conditions)

    @patch("app.presigned_upload.boto3.client")
    def test_signer_uses_public_endpoint_and_path_style(self, boto_client) -> None:
        """The Pod signs browser URLs; it does not sign its private .svc endpoint."""

        client = MagicMock()
        client.generate_presigned_post.return_value = {
            "url": "http://minio.localhost:8080/clouddsp-uploads",
            "fields": {"key": TEST_OBJECT_KEY, "policy": "not-a-real-policy"},
        }
        boto_client.return_value = client

        contract = self.create_contract(maximum_source_bytes=1024, expires_in_seconds=60)

        self.assertEqual(contract.maximum_source_bytes, 1024)
        self.assertEqual(contract.expires_in_seconds, 60)
        boto_client.assert_called_once()
        client_arguments = boto_client.call_args.kwargs
        self.assertEqual(client_arguments["endpoint_url"], "http://minio.localhost:8080")
        self.assertEqual(client_arguments["region_name"], "us-east-1")
        self.assertEqual(client_arguments["config"].signature_version, "s3v4")
        self.assertEqual(client_arguments["config"].s3, {"addressing_style": "path"})

        generated = client.generate_presigned_post.call_args.kwargs
        self.assertEqual(generated["Bucket"], "clouddsp-uploads")
        self.assertEqual(generated["Key"], TEST_OBJECT_KEY)
        self.assertEqual(generated["Fields"]["Content-Type"], "audio/wav")
        self.assertEqual(
            generated["Conditions"],
            [
                {"Content-Type": "audio/wav"},
                {"x-amz-meta-job-id": TEST_JOB_ID},
                {"x-amz-meta-stem-mode": "4-stems"},
                ["content-length-range", 1, 1024],
            ],
        )
        self.assertEqual(generated["ExpiresIn"], 60)

    def test_rejects_key_outside_the_current_jobs_single_filename_prefix(self) -> None:
        """A restricted S3 identity cannot be asked to sign a broad or other-job key."""

        invalid_keys = (
            "uploads/other-job/mix.wav",
            f"uploads/{TEST_JOB_ID}/nested/mix.wav",
            f"uploads/{TEST_JOB_ID}/../private.wav",
            f"uploads/{TEST_JOB_ID}/",
        )
        for invalid_key in invalid_keys:
            with self.subTest(invalid_key=invalid_key):
                with self.assertRaises(PresignedUploadContractError) as raised:
                    self.create_contract(input_object_key=invalid_key)
                self.assertIn("input_object_key", str(raised.exception))

    def test_rejects_noncanonical_inputs_and_any_limit_above_the_product_ceiling(self) -> None:
        """The helper cannot accidentally create a form that relaxes route policy."""

        invalid_arguments = (
            {"job_id": "not-a-uuid"},
            {"content_type": "audio/wav; charset=utf-8"},
            {"content_type": ""},
            {"stem_mode": "all-stems"},
            {"maximum_source_bytes": 0},
            {"maximum_source_bytes": MAX_SOURCE_UPLOAD_BYTES + 1},
            {"maximum_source_bytes": True},
            {"expires_in_seconds": 0},
            {"expires_in_seconds": 901},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(PresignedUploadContractError):
                    self.create_contract(**arguments)

    def test_contract_repr_hides_short_lived_signature_fields(self) -> None:
        """Accidental debug logging cannot print a policy or Signature V4 value."""

        contract = self.create_contract()
        rendered = repr(contract)

        self.assertNotIn(contract.fields["policy"], rendered)
        self.assertNotIn(contract.fields["x-amz-signature"], rendered)
        self.assertIn("maximum_source_bytes=268435456", rendered)
