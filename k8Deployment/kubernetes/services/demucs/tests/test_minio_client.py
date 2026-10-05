"""Unit tests for Demucs's private MinIO settings and lazy Boto3 factory.

Tests use only synthetic credentials and patched SDK constructors. They never
resolve Service DNS, open MinIO/AWS network connections, or require Boto3 to be
installed in the current development interpreter.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from app.artifacts.minio_client import (
    DEMUCS_MINIO_CONNECT_TIMEOUT_SECONDS,
    DEMUCS_MINIO_MAX_ATTEMPTS,
    DEMUCS_MINIO_READ_TIMEOUT_SECONDS,
    LOCAL_S3_REGION,
    LOCAL_UPLOADS_BUCKET,
    DemucsMinioConfigurationError,
    DemucsMinioSettings,
    create_boto3_demucs_minio_client,
)


VALID_ENVIRONMENT = {
    "DEMUCS_S3_INTERNAL_ENDPOINT": "http://clouddsp-minio.clouddsp-data.svc:9000",
    "DEMUCS_S3_UPLOADS_BUCKET": LOCAL_UPLOADS_BUCKET,
    "DEMUCS_S3_REGION": LOCAL_S3_REGION,
    "DEMUCS_S3_ADDRESSING_STYLE": "path",
    # Synthetic values only: these must never match a checked-in template or a
    # local ignored Secret used by a real MinIO identity.
    "DEMUCS_S3_ACCESS_KEY": "unit-test-demucs-access-key",
    "DEMUCS_S3_SECRET_KEY": "unit-test-demucs-secret-key",
}


class DemucsMinioSettingsTests(unittest.TestCase):
    """Prove a future worker can construct only the reviewed private S3 client."""

    def settings_from(self, overrides: dict[str, str] | None = None) -> DemucsMinioSettings:
        """Load one isolated environment without exposing production credentials."""

        environment = dict(VALID_ENVIRONMENT)
        if overrides:
            environment.update(overrides)
        with patch.dict(os.environ, environment, clear=True):
            return DemucsMinioSettings.from_environment()

    def test_reads_the_explicit_private_service_configuration(self) -> None:
        """All non-secret routing values come from the future Deployment contract."""

        settings = self.settings_from()

        self.assertEqual(
            settings.internal_endpoint,
            "http://clouddsp-minio.clouddsp-data.svc:9000",
        )
        self.assertEqual(settings.uploads_bucket, LOCAL_UPLOADS_BUCKET)
        self.assertEqual(settings.region_name, LOCAL_S3_REGION)
        self.assertEqual(settings.addressing_style, "path")

    def test_settings_repr_hides_mounted_credential_values(self) -> None:
        """A normal debug statement cannot serialize either S3 Secret field."""

        rendered = repr(self.settings_from())

        self.assertNotIn(VALID_ENVIRONMENT["DEMUCS_S3_ACCESS_KEY"], rendered)
        self.assertNotIn(VALID_ENVIRONMENT["DEMUCS_S3_SECRET_KEY"], rendered)

    def test_missing_credential_names_the_variable_but_not_any_secret_value(self) -> None:
        """Configuration errors give a useful fix without leaking mounted content."""

        environment = dict(VALID_ENVIRONMENT)
        environment.pop("DEMUCS_S3_SECRET_KEY")
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(DemucsMinioConfigurationError) as raised:
                DemucsMinioSettings.from_environment()

        self.assertEqual(
            str(raised.exception),
            "Required environment variable DEMUCS_S3_SECRET_KEY is absent.",
        )
        self.assertNotIn(VALID_ENVIRONMENT["DEMUCS_S3_ACCESS_KEY"], str(raised.exception))

    def test_rejects_browser_ambiguous_or_non_service_endpoint(self) -> None:
        """A Pod may never read private source audio through Traefik/localhost."""

        for endpoint in (
            "http://minio.localhost:8080",
            "http://clouddsp-minio.clouddsp-data.svc/private-prefix",
            "http://clouddsp-minio.clouddsp-data.svc?token=not-allowed",
            "http://clouddsp-minio.clouddsp-data.svc:9000@attacker.example",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(DemucsMinioConfigurationError):
                    self.settings_from({"DEMUCS_S3_INTERNAL_ENDPOINT": endpoint})

    def test_rejects_a_widened_bucket_region_or_addressing_style(self) -> None:
        """Storage routing cannot quietly diverge from the reviewed local policy."""

        invalid_overrides = (
            {"DEMUCS_S3_UPLOADS_BUCKET": "another-bucket"},
            {"DEMUCS_S3_REGION": "another-region"},
            {"DEMUCS_S3_ADDRESSING_STYLE": "virtual"},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(DemucsMinioConfigurationError):
                    self.settings_from(overrides)

    @patch("app.artifacts.minio_client._load_boto3_client_factories")
    def test_factory_uses_explicit_minio_credentials_path_style_and_time_bounds(self, loader) -> None:
        """Factory construction is local; patched constructors prove no network call occurs."""

        settings = self.settings_from()
        boto3_client = MagicMock(return_value=object())
        config_factory = MagicMock(return_value=object())
        loader.return_value = (boto3_client, config_factory)

        client = create_boto3_demucs_minio_client(settings)

        self.assertIs(client, boto3_client.return_value)
        config_factory.assert_called_once_with(
            signature_version="s3v4",
            connect_timeout=DEMUCS_MINIO_CONNECT_TIMEOUT_SECONDS,
            read_timeout=DEMUCS_MINIO_READ_TIMEOUT_SECONDS,
            retries={"mode": "standard", "max_attempts": DEMUCS_MINIO_MAX_ATTEMPTS},
            s3={"addressing_style": "path"},
        )
        boto3_client.assert_called_once_with(
            "s3",
            endpoint_url=settings.internal_endpoint,
            region_name=settings.region_name,
            aws_access_key_id=VALID_ENVIRONMENT["DEMUCS_S3_ACCESS_KEY"],
            aws_secret_access_key=VALID_ENVIRONMENT["DEMUCS_S3_SECRET_KEY"],
            config=config_factory.return_value,
        )


if __name__ == "__main__":
    unittest.main()
