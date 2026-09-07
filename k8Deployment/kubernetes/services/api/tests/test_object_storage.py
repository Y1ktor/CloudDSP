"""Focused unit tests for the Job API's MinIO environment contract.

These tests use only Python's standard library and never contact MinIO. They
prove that future upload code receives separate private/public endpoint roles
and that malformed settings fail before a presigned URL or S3 client exists.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.object_storage import ObjectStorageConfigurationError, ObjectStorageSettings


VALID_OBJECT_STORAGE_ENVIRONMENT = {
    # A real API Pod uses this private Kubernetes Service name for server-side
    # S3 operations. The test does not attempt to resolve it or make a socket.
    "JOB_API_S3_INTERNAL_ENDPOINT": "http://clouddsp-minio.clouddsp-data.svc:9000",
    # Future presigned URLs use this browser-visible Traefik Ingress origin.
    "JOB_API_S3_PUBLIC_ENDPOINT": "http://minio.localhost:8080",
    "JOB_API_S3_UPLOADS_BUCKET": "clouddsp-uploads",
    "JOB_API_S3_REGION": "us-east-1",
    "JOB_API_S3_ADDRESSING_STYLE": "path",
    # Synthetic test values only. They must never be a copied local Secret.
    "JOB_API_S3_ACCESS_KEY": "test-access-key",
    "JOB_API_S3_SECRET_KEY": "test-secret-key-not-a-real-credential",
}


class ObjectStorageSettingsTests(unittest.TestCase):
    """Prove the future S3 client gets a safe and unambiguous configuration."""

    def settings_from(self, overrides: dict[str, str] | None = None) -> ObjectStorageSettings:
        """Load settings from an isolated process environment for one test."""

        environment = dict(VALID_OBJECT_STORAGE_ENVIRONMENT)
        if overrides:
            environment.update(overrides)
        with patch.dict(os.environ, environment, clear=True):
            return ObjectStorageSettings.from_environment()

    def test_reads_separate_internal_and_browser_visible_endpoints(self) -> None:
        """One settings object preserves both network audiences without I/O."""

        settings = self.settings_from()

        self.assertEqual(
            settings.internal_endpoint,
            "http://clouddsp-minio.clouddsp-data.svc:9000",
        )
        self.assertEqual(settings.public_endpoint, "http://minio.localhost:8080")
        self.assertEqual(settings.uploads_bucket, "clouddsp-uploads")
        self.assertEqual(settings.region, "us-east-1")
        self.assertEqual(settings.addressing_style, "path")

    def test_credentials_are_not_rendered_by_settings_repr(self) -> None:
        """A future debug log cannot accidentally serialize MinIO credentials."""

        settings = self.settings_from()
        rendered = repr(settings)

        self.assertNotIn(VALID_OBJECT_STORAGE_ENVIRONMENT["JOB_API_S3_ACCESS_KEY"], rendered)
        self.assertNotIn(VALID_OBJECT_STORAGE_ENVIRONMENT["JOB_API_S3_SECRET_KEY"], rendered)
        self.assertIn("uploads_bucket='clouddsp-uploads'", rendered)

    def test_missing_secret_key_names_only_the_missing_variable(self) -> None:
        """Configuration errors identify a key name but never print a secret."""

        environment = dict(VALID_OBJECT_STORAGE_ENVIRONMENT)
        environment.pop("JOB_API_S3_SECRET_KEY")
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ObjectStorageConfigurationError) as raised:
                ObjectStorageSettings.from_environment()

        self.assertEqual(
            str(raised.exception),
            "Required environment variable JOB_API_S3_SECRET_KEY is absent.",
        )
        self.assertNotIn("test-access-key", str(raised.exception))

    def test_internal_endpoint_must_use_private_service_dns(self) -> None:
        """A public host cannot accidentally become the API's server-side path."""

        with self.assertRaises(ObjectStorageConfigurationError) as raised:
            self.settings_from(
                {"JOB_API_S3_INTERNAL_ENDPOINT": "http://minio.localhost:8080"}
            )

        self.assertEqual(
            str(raised.exception),
            "JOB_API_S3_INTERNAL_ENDPOINT must use Kubernetes Service DNS ending in .svc.",
        )

    def test_public_endpoint_rejects_ambiguous_url_parts(self) -> None:
        """A browser URL cannot carry credentials, query text, or a proxy path."""

        for invalid_value in (
            "http://user:password@minio.localhost:8080",
            "http://minio.localhost:8080/s3",
            "http://minio.localhost:8080/?unexpected=query",
        ):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaises(ObjectStorageConfigurationError) as raised:
                    self.settings_from({"JOB_API_S3_PUBLIC_ENDPOINT": invalid_value})
                self.assertEqual(
                    str(raised.exception),
                    "JOB_API_S3_PUBLIC_ENDPOINT must be a simple absolute HTTP(S) origin URL.",
                )

    def test_public_endpoint_must_not_use_internal_service_dns(self) -> None:
        """A browser cannot resolve Kubernetes-only Service DNS names."""

        with self.assertRaises(ObjectStorageConfigurationError) as raised:
            self.settings_from(
                {
                    "JOB_API_S3_PUBLIC_ENDPOINT": (
                        "http://clouddsp-minio.clouddsp-data.svc:9000"
                    )
                }
            )

        self.assertEqual(
            str(raised.exception),
            "JOB_API_S3_PUBLIC_ENDPOINT must be browser-reachable and must not use .svc DNS.",
        )

    def test_path_style_and_valid_bucket_name_are_required(self) -> None:
        """The local one-host Ingress cannot serve virtual-hosted bucket URLs."""

        with self.assertRaises(ObjectStorageConfigurationError) as style_error:
            self.settings_from({"JOB_API_S3_ADDRESSING_STYLE": "virtual"})
        self.assertEqual(
            str(style_error.exception),
            "JOB_API_S3_ADDRESSING_STYLE must be path for the local S3 Ingress.",
        )

        with self.assertRaises(ObjectStorageConfigurationError) as bucket_error:
            self.settings_from({"JOB_API_S3_UPLOADS_BUCKET": "CloudDSP Uploads"})
        self.assertEqual(
            str(bucket_error.exception),
            "JOB_API_S3_UPLOADS_BUCKET must be a 3-63 character lowercase S3 bucket name.",
        )
