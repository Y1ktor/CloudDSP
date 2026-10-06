"""Unit tests for Basic Pitch's private MinIO settings and lazy Boto3 factory.

These tests use synthetic credentials and patched SDK constructors only. They
never resolve Kubernetes Service DNS, contact MinIO/AWS, or require Boto3 in
the local Python interpreter.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from app.artifacts.minio_client import (
    BASIC_PITCH_MINIO_CONNECT_TIMEOUT_SECONDS,
    BASIC_PITCH_MINIO_MAX_ATTEMPTS,
    BASIC_PITCH_MINIO_READ_TIMEOUT_SECONDS,
    LOCAL_ARTIFACTS_BUCKET,
    LOCAL_MINIO_INTERNAL_ENDPOINT,
    LOCAL_S3_ADDRESSING_STYLE,
    LOCAL_S3_REGION,
    BasicPitchMinioConfigurationError,
    BasicPitchMinioSettings,
    create_boto3_basic_pitch_minio_client,
)


VALID_ENVIRONMENT = {
    "BASIC_PITCH_S3_INTERNAL_ENDPOINT": LOCAL_MINIO_INTERNAL_ENDPOINT,
    "BASIC_PITCH_S3_ARTIFACTS_BUCKET": LOCAL_ARTIFACTS_BUCKET,
    "BASIC_PITCH_S3_REGION": LOCAL_S3_REGION,
    "BASIC_PITCH_S3_ADDRESSING_STYLE": LOCAL_S3_ADDRESSING_STYLE,
    # Synthetic only: these values must never match a local ignored Secret.
    "BASIC_PITCH_S3_ACCESS_KEY": "unit-test-basic-pitch-access-key",
    "BASIC_PITCH_S3_SECRET_KEY": "unit-test-basic-pitch-secret-key",
}


class BasicPitchMinioSettingsTests(unittest.TestCase):
    """Prove future workers construct only the reviewed private S3 client."""

    def settings_from(self, overrides: dict[str, str] | None = None) -> BasicPitchMinioSettings:
        """Load a complete isolated environment without exposing real credentials."""

        environment = dict(VALID_ENVIRONMENT)
        if overrides:
            environment.update(overrides)
        with patch.dict(os.environ, environment, clear=True):
            return BasicPitchMinioSettings.from_environment()

    def test_reads_the_explicit_private_service_configuration(self) -> None:
        """The future Deployment can supply no alternate endpoint/bucket contract."""

        settings = self.settings_from()

        self.assertEqual(settings.internal_endpoint, LOCAL_MINIO_INTERNAL_ENDPOINT)
        self.assertEqual(settings.artifacts_bucket, LOCAL_ARTIFACTS_BUCKET)
        self.assertEqual(settings.region_name, LOCAL_S3_REGION)
        self.assertEqual(settings.addressing_style, LOCAL_S3_ADDRESSING_STYLE)

    def test_settings_repr_hides_mounted_credential_values(self) -> None:
        """An accidental normal settings log never renders either Secret value."""

        rendered = repr(self.settings_from())

        self.assertNotIn(VALID_ENVIRONMENT["BASIC_PITCH_S3_ACCESS_KEY"], rendered)
        self.assertNotIn(VALID_ENVIRONMENT["BASIC_PITCH_S3_SECRET_KEY"], rendered)

    def test_missing_or_unsafe_environment_text_names_the_variable_not_its_value(self) -> None:
        """Failures are actionable without making a mounted Secret observable."""

        for name, replacement in (
            ("BASIC_PITCH_S3_SECRET_KEY", None),
            ("BASIC_PITCH_S3_ACCESS_KEY", "unsafe\nvalue"),
        ):
            with self.subTest(name=name):
                environment = dict(VALID_ENVIRONMENT)
                if replacement is None:
                    environment.pop(name)
                else:
                    environment[name] = replacement
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(BasicPitchMinioConfigurationError) as raised:
                        BasicPitchMinioSettings.from_environment()

                self.assertEqual(str(raised.exception), f"Required environment variable {name} is absent.")
                self.assertNotIn(VALID_ENVIRONMENT["BASIC_PITCH_S3_SECRET_KEY"], str(raised.exception))

    def test_rejects_any_endpoint_other_than_the_private_minio_service(self) -> None:
        """Neither Traefik/localhost nor another `.svc` service is acceptable."""

        for endpoint in (
            "http://minio.localhost:8080",
            "http://clouddsp-minio.clouddsp-data.svc:9001",
            "http://another-service.clouddsp-data.svc:9000",
            "https://clouddsp-minio.clouddsp-data.svc:9000",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(BasicPitchMinioConfigurationError):
                    self.settings_from({"BASIC_PITCH_S3_INTERNAL_ENDPOINT": endpoint})

    def test_rejects_a_widened_bucket_region_or_addressing_style(self) -> None:
        """Storage routing cannot quietly diverge from the reviewed MinIO policy."""

        invalid_overrides = (
            {"BASIC_PITCH_S3_ARTIFACTS_BUCKET": "another-bucket"},
            {"BASIC_PITCH_S3_REGION": "another-region"},
            {"BASIC_PITCH_S3_ADDRESSING_STYLE": "virtual"},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(BasicPitchMinioConfigurationError):
                    self.settings_from(overrides)

    @patch("app.artifacts.minio_client._load_boto3_client_factories")
    def test_factory_uses_explicit_minio_credentials_path_style_and_time_bounds(self, loader) -> None:
        """Patched construction proves factory creation itself opens no network connection."""

        settings = self.settings_from()
        boto3_client = MagicMock(return_value=object())
        config_factory = MagicMock(return_value=object())
        loader.return_value = (boto3_client, config_factory)

        client = create_boto3_basic_pitch_minio_client(settings)

        self.assertIs(client, boto3_client.return_value)
        config_factory.assert_called_once_with(
            signature_version="s3v4",
            connect_timeout=BASIC_PITCH_MINIO_CONNECT_TIMEOUT_SECONDS,
            read_timeout=BASIC_PITCH_MINIO_READ_TIMEOUT_SECONDS,
            retries={"mode": "standard", "max_attempts": BASIC_PITCH_MINIO_MAX_ATTEMPTS},
            s3={"addressing_style": LOCAL_S3_ADDRESSING_STYLE},
        )
        boto3_client.assert_called_once_with(
            "s3",
            endpoint_url=LOCAL_MINIO_INTERNAL_ENDPOINT,
            region_name=LOCAL_S3_REGION,
            aws_access_key_id=VALID_ENVIRONMENT["BASIC_PITCH_S3_ACCESS_KEY"],
            aws_secret_access_key=VALID_ENVIRONMENT["BASIC_PITCH_S3_SECRET_KEY"],
            config=config_factory.return_value,
        )


if __name__ == "__main__":
    unittest.main()
