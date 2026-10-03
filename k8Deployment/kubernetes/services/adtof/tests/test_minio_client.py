"""Unit tests for ADTOF's private MinIO settings and lazy Boto3 factory.

Tests use synthetic credentials and patched SDK constructors. They never
resolve Service DNS, contact MinIO/AWS, require Boto3 in this interpreter, read
an object, invoke ADTOF, or create Docker/Kubernetes state.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from app.artifacts.minio_client import (
    ADTOF_MINIO_CONNECT_TIMEOUT_SECONDS,
    ADTOF_MINIO_MAX_ATTEMPTS,
    ADTOF_MINIO_READ_TIMEOUT_SECONDS,
    LOCAL_ARTIFACTS_BUCKET,
    LOCAL_MINIO_INTERNAL_ENDPOINT,
    LOCAL_S3_ADDRESSING_STYLE,
    LOCAL_S3_REGION,
    ADTOFMinioConfigurationError,
    ADTOFMinioSettings,
    create_boto3_adtof_minio_client,
)


VALID_ENVIRONMENT = {
    "ADTOF_S3_INTERNAL_ENDPOINT": LOCAL_MINIO_INTERNAL_ENDPOINT,
    "ADTOF_S3_ARTIFACTS_BUCKET": LOCAL_ARTIFACTS_BUCKET,
    "ADTOF_S3_REGION": LOCAL_S3_REGION,
    "ADTOF_S3_ADDRESSING_STYLE": LOCAL_S3_ADDRESSING_STYLE,
    # Synthetic-only values must never equal a user's ignored runtime Secret.
    "ADTOF_S3_ACCESS_KEY": "unit-test-adtof-access-key",
    "ADTOF_S3_SECRET_KEY": "unit-test-adtof-secret-key",
}


class ADTOFMinioSettingsTests(unittest.TestCase):
    """Prove future ADTOF Pods construct only the reviewed private S3 client."""

    def settings_from(self, overrides: dict[str, str] | None = None) -> ADTOFMinioSettings:
        """Load a complete isolated environment without exposing real Secrets."""

        environment = dict(VALID_ENVIRONMENT)
        if overrides:
            environment.update(overrides)
        with patch.dict(os.environ, environment, clear=True):
            return ADTOFMinioSettings.from_environment()

    def test_reads_the_explicit_private_service_configuration(self) -> None:
        """The future Deployment has no alternate endpoint/bucket contract."""

        settings = self.settings_from()

        self.assertEqual(settings.internal_endpoint, LOCAL_MINIO_INTERNAL_ENDPOINT)
        self.assertEqual(settings.artifacts_bucket, LOCAL_ARTIFACTS_BUCKET)
        self.assertEqual(settings.region_name, LOCAL_S3_REGION)
        self.assertEqual(settings.addressing_style, LOCAL_S3_ADDRESSING_STYLE)

    def test_settings_repr_hides_mounted_credential_values(self) -> None:
        """An ordinary settings log cannot render either Secret-derived value."""

        rendered = repr(self.settings_from())

        self.assertNotIn(VALID_ENVIRONMENT["ADTOF_S3_ACCESS_KEY"], rendered)
        self.assertNotIn(VALID_ENVIRONMENT["ADTOF_S3_SECRET_KEY"], rendered)

    def test_missing_or_unsafe_environment_text_names_variable_not_secret_value(self) -> None:
        """Errors remain actionable without making mounted credentials visible."""

        for name, replacement in (
            ("ADTOF_S3_SECRET_KEY", None),
            ("ADTOF_S3_ACCESS_KEY", "unsafe\nvalue"),
        ):
            with self.subTest(name=name):
                environment = dict(VALID_ENVIRONMENT)
                if replacement is None:
                    environment.pop(name)
                else:
                    environment[name] = replacement
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(ADTOFMinioConfigurationError) as raised:
                        ADTOFMinioSettings.from_environment()

                self.assertEqual(str(raised.exception), f"Required environment variable {name} is absent.")
                self.assertNotIn(VALID_ENVIRONMENT["ADTOF_S3_SECRET_KEY"], str(raised.exception))

    def test_rejects_any_endpoint_other_than_private_minio_service(self) -> None:
        """Neither Ingress/localhost nor another Service can receive this key pair."""

        for endpoint in (
            "http://minio.localhost:8080",
            "http://clouddsp-minio.clouddsp-data.svc:9001",
            "http://another-service.clouddsp-data.svc:9000",
            "https://clouddsp-minio.clouddsp-data.svc:9000",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ADTOFMinioConfigurationError):
                    self.settings_from({"ADTOF_S3_INTERNAL_ENDPOINT": endpoint})

    def test_rejects_widened_bucket_region_or_addressing_style(self) -> None:
        """Storage routing cannot diverge quietly from the reviewed MinIO policy."""

        for overrides in (
            {"ADTOF_S3_ARTIFACTS_BUCKET": "another-bucket"},
            {"ADTOF_S3_REGION": "another-region"},
            {"ADTOF_S3_ADDRESSING_STYLE": "virtual"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ADTOFMinioConfigurationError):
                    self.settings_from(overrides)

    @patch("app.artifacts.minio_client._load_boto3_client_factories")
    def test_direct_settings_cannot_redirect_credentials_before_sdk_load(self, loader) -> None:
        """Frozen dataclass construction cannot bypass the endpoint/bucket contract."""

        widened = ADTOFMinioSettings(
            internal_endpoint="http://attacker.example:9000",
            artifacts_bucket=LOCAL_ARTIFACTS_BUCKET,
            region_name=LOCAL_S3_REGION,
            addressing_style=LOCAL_S3_ADDRESSING_STYLE,
            access_key="unit-test-adtof-access-key",
            secret_key="unit-test-adtof-secret-key",
        )

        with self.assertRaises(ADTOFMinioConfigurationError):
            create_boto3_adtof_minio_client(widened)

        loader.assert_not_called()

    @patch("app.artifacts.minio_client._load_boto3_client_factories")
    def test_factory_uses_explicit_minio_credentials_path_style_and_time_bounds(self, loader) -> None:
        """Patched construction proves client creation itself opens no connection."""

        settings = self.settings_from()
        boto3_client = MagicMock(return_value=object())
        config_factory = MagicMock(return_value=object())
        loader.return_value = (boto3_client, config_factory)

        client = create_boto3_adtof_minio_client(settings)

        self.assertIs(client, boto3_client.return_value)
        config_factory.assert_called_once_with(
            signature_version="s3v4",
            connect_timeout=ADTOF_MINIO_CONNECT_TIMEOUT_SECONDS,
            read_timeout=ADTOF_MINIO_READ_TIMEOUT_SECONDS,
            retries={"mode": "standard", "max_attempts": ADTOF_MINIO_MAX_ATTEMPTS},
            s3={"addressing_style": LOCAL_S3_ADDRESSING_STYLE},
        )
        boto3_client.assert_called_once_with(
            "s3",
            endpoint_url=LOCAL_MINIO_INTERNAL_ENDPOINT,
            region_name=LOCAL_S3_REGION,
            aws_access_key_id=VALID_ENVIRONMENT["ADTOF_S3_ACCESS_KEY"],
            aws_secret_access_key=VALID_ENVIRONMENT["ADTOF_S3_SECRET_KEY"],
            config=config_factory.return_value,
        )


if __name__ == "__main__":
    unittest.main()
