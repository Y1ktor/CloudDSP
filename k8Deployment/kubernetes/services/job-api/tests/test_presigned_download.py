"""Prove browser downloads are fresh, direct MinIO URLs for one Job artifact."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.object_storage import ObjectStorageSettings
from app.presigned_download import (
    DEFAULT_DOWNLOAD_URL_EXPIRY_SECONDS,
    PresignedDownloadContractError,
    create_presigned_download_url,
)


JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"


class PresignedDownloadTests(unittest.TestCase):
    """Check signer audience, deterministic keys, and private-path rejection."""

    def setUp(self) -> None:
        self.settings = ObjectStorageSettings(
            internal_endpoint="http://clouddsp-minio.clouddsp-data.svc:9000",
            public_endpoint="http://minio.localhost:8080",
            uploads_bucket="clouddsp-uploads",
            region="us-east-1",
            addressing_style="path",
            access_key="unit-test-access-key",
            secret_key="unit-test-secret-key-not-real",
        )

    @patch("app.presigned_download.boto3.client")
    def test_signs_exact_midi_through_the_browser_ingress_without_network(self, boto_client) -> None:
        """Artifact bytes are fetched by the browser rather than proxied by API."""

        client = MagicMock()
        client.generate_presigned_url.return_value = (
            "http://minio.localhost:8080/clouddsp-uploads/midi/"
            f"{JOB_ID}/vocals.mid?X-Amz-Signature=unit-test"
        )
        boto_client.return_value = client

        with patch("botocore.httpsession.URLLib3Session.send") as transport_send:
            url = create_presigned_download_url(
                self.settings,
                job_id=JOB_ID,
                object_key=f"midi/{JOB_ID}/vocals.mid",
                kind="midi",
                stem_name="vocals",
            )

        transport_send.assert_not_called()
        self.assertIn("minio.localhost:8080/clouddsp-uploads/midi/", url)
        self.assertEqual(boto_client.call_args.kwargs["endpoint_url"], self.settings.public_endpoint)
        self.assertEqual(boto_client.call_args.kwargs["aws_access_key_id"], "unit-test-access-key")
        generated = client.generate_presigned_url.call_args.kwargs
        self.assertEqual(generated["ClientMethod"], "get_object")
        self.assertEqual(
            generated["Params"],
            {"Bucket": "clouddsp-uploads", "Key": f"midi/{JOB_ID}/vocals.mid"},
        )
        self.assertEqual(generated["ExpiresIn"], DEFAULT_DOWNLOAD_URL_EXPIRY_SECONDS)

    @patch("app.presigned_download.boto3.client")
    def test_source_download_accepts_one_uploaded_filename_and_preserves_public_endpoint(self, boto_client) -> None:
        """A safe original filename remains addressable without widening its Job prefix."""

        client = MagicMock()
        client.generate_presigned_url.return_value = "http://minio.localhost:8080/a-signed-source-url"
        boto_client.return_value = client

        url = create_presigned_download_url(
            self.settings,
            job_id=JOB_ID,
            object_key=f"uploads/{JOB_ID}/my recording (final).wav",
            kind="source",
        )

        self.assertEqual(url, client.generate_presigned_url.return_value)
        self.assertEqual(
            client.generate_presigned_url.call_args.kwargs["Params"],
            {
                "Bucket": "clouddsp-uploads",
                "Key": f"uploads/{JOB_ID}/my recording (final).wav",
            },
        )

    def test_rejects_foreign_jobs_wrong_artifact_keys_and_unsafe_expiry(self) -> None:
        """No detail snapshot can make this signer cross a Job or artifact boundary."""

        invalid_requests = (
            {"job_id": JOB_ID, "object_key": f"midi/{JOB_ID}/bass.mid", "kind": "midi", "stem_name": "vocals"},
            {"job_id": JOB_ID, "object_key": f"stems/other-job/vocals.wav", "kind": "stem", "stem_name": "vocals"},
            {"job_id": JOB_ID, "object_key": f"midi/{JOB_ID}/drums.wav", "kind": "tempo"},
            {"job_id": "not-a-uuid", "object_key": f"midi/{JOB_ID}/vocals.mid", "kind": "midi", "stem_name": "vocals"},
            {"job_id": JOB_ID, "object_key": f"uploads/{JOB_ID}/nested/file.wav", "kind": "source"},
            {"job_id": JOB_ID, "object_key": f"midi/{JOB_ID}/vocals.mid", "kind": "midi", "stem_name": "vocals", "expires_in_seconds": 3601},
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(PresignedDownloadContractError):
                    create_presigned_download_url(self.settings, **request)

    def test_rejects_unreviewed_bucket_before_signing(self) -> None:
        """A configuration typo cannot change the scope of the restricted API key."""

        settings = ObjectStorageSettings(
            **{
                **self.settings.__dict__,
                "uploads_bucket": "other-private-bucket",
            }
        )
        with self.assertRaises(PresignedDownloadContractError):
            create_presigned_download_url(
                settings,
                job_id=JOB_ID,
                object_key=f"midi/{JOB_ID}/vocals.mid",
                kind="midi",
                stem_name="vocals",
            )


if __name__ == "__main__":  # pragma: no cover - directly runnable learning aid.
    unittest.main()
