"""Unit tests for ADTOF's Psycopg configuration and transaction boundary.

Tests use a fake driver and synthetic credential text only. They never resolve
cluster DNS, contact PostgreSQL, read an ignored local Secret, execute task SQL,
or run RabbitMQ/MinIO/ADTOF/Kubernetes operations.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from app.postgresql import (
    DEFAULT_DATABASE_HOST,
    DEFAULT_DATABASE_PORT,
    DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS,
    LOCAL_ADTOF_DATABASE_USERNAME,
    LOCAL_DATABASE_NAME,
    ADTOFDatabaseConfigurationError,
    ADTOFDatabaseSettings,
    ADTOFDatabaseUnavailable,
    PsycopgADTOFDatabase,
)


VALID_ENVIRONMENT = {
    "ADTOF_DB_NAME": LOCAL_DATABASE_NAME,
    "ADTOF_DB_USERNAME": LOCAL_ADTOF_DATABASE_USERNAME,
    # Synthetic test text only; this is never copied from an ignored Secret.
    "ADTOF_DB_PASSWORD": "unit-test-adtof-database-password",
}


class FakePsycopgError(Exception):
    """Stand in for Psycopg's base error without importing Psycopg."""


class FakePsycopg:
    """Expose only the driver surface used by the concrete adapter."""

    Error = FakePsycopgError
    connect = MagicMock()


def settings() -> ADTOFDatabaseSettings:
    """Return explicit non-secret settings for mocked connection assertions."""

    return ADTOFDatabaseSettings(
        host=DEFAULT_DATABASE_HOST,
        port=DEFAULT_DATABASE_PORT,
        database=LOCAL_DATABASE_NAME,
        username=LOCAL_ADTOF_DATABASE_USERNAME,
        password="not-a-real-password",
        connect_timeout_seconds=3,
    )


class ADTOFDatabaseSettingsTests(unittest.TestCase):
    """Prove an ADTOF Pod can use only its restricted local database authority."""

    def settings_from(self, overrides: dict[str, str] | None = None) -> ADTOFDatabaseSettings:
        """Load a complete isolated environment without exposing real credentials."""

        environment = dict(VALID_ENVIRONMENT)
        if overrides:
            environment.update(overrides)
        with patch.dict(os.environ, environment, clear=True):
            return ADTOFDatabaseSettings.from_environment()

    def test_defaults_to_private_service_and_hides_password(self) -> None:
        """Service DNS is fixed and ordinary repr cannot render the Secret."""

        loaded = self.settings_from()

        self.assertEqual(loaded.host, DEFAULT_DATABASE_HOST)
        self.assertEqual(loaded.port, DEFAULT_DATABASE_PORT)
        self.assertEqual(loaded.database, LOCAL_DATABASE_NAME)
        self.assertEqual(loaded.username, LOCAL_ADTOF_DATABASE_USERNAME)
        self.assertNotIn(VALID_ENVIRONMENT["ADTOF_DB_PASSWORD"], repr(loaded))

    def test_missing_password_fails_before_the_driver_loads(self) -> None:
        """A malformed Secret is a configuration category, not a driver leak."""

        environment = dict(VALID_ENVIRONMENT)
        environment.pop("ADTOF_DB_PASSWORD")
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ADTOFDatabaseConfigurationError) as raised:
                ADTOFDatabaseSettings.from_environment()

        self.assertEqual(
            str(raised.exception),
            "Required environment variable ADTOF_DB_PASSWORD is absent.",
        )

    def test_rejects_widened_service_database_or_role(self) -> None:
        """A ConfigMap/Secret typo cannot silently become wider DB authority."""

        for overrides in (
            {"ADTOF_DB_HOST": "localhost"},
            {"ADTOF_DB_PORT": "6543"},
            {"ADTOF_DB_NAME": "another_database"},
            {"ADTOF_DB_USERNAME": "clouddsp-admin"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ADTOFDatabaseConfigurationError):
                    self.settings_from(overrides)

    def test_rejects_invalid_port_and_connect_timeout(self) -> None:
        """Invalid Pod text cannot become unbounded database-driver behavior."""

        for overrides in (
            {"ADTOF_DB_PORT": "0"},
            {"ADTOF_DB_PORT": "not-a-number"},
            {"ADTOF_DB_CONNECT_TIMEOUT_SECONDS": "11"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ADTOFDatabaseConfigurationError):
                    self.settings_from(overrides)


class PsycopgADTOFDatabaseTests(unittest.TestCase):
    """Prove each durable ADTOF action receives one short transaction scope."""

    def setUp(self) -> None:
        """Reset the shared fake driver between normal/error-path assertions."""

        FakePsycopg.connect.reset_mock(return_value=True, side_effect=True)
        self.adapter = PsycopgADTOFDatabase(settings())
        self.dict_row = object()

    def _connection(self) -> MagicMock:
        """Create the nested context-managed connection the adapter expects."""

        connection = MagicMock()
        FakePsycopg.connect.return_value.__enter__.return_value = connection
        return connection

    @patch("app.postgresql._load_psycopg")
    def test_write_cursor_uses_a_bounded_dictionary_row_transaction(self, load_psycopg) -> None:
        """A claim commit happens before later ADTOF CPU inference begins."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        connection = self._connection()
        transaction_context = connection.transaction.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        with self.adapter.write_cursor() as received_cursor:
            self.assertIs(received_cursor, cursor)

        kwargs = FakePsycopg.connect.call_args.kwargs
        self.assertEqual(kwargs["host"], DEFAULT_DATABASE_HOST)
        self.assertEqual(kwargs["port"], DEFAULT_DATABASE_PORT)
        self.assertEqual(kwargs["application_name"], "clouddsp-adtof-task-lease")
        self.assertEqual(kwargs["options"], f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS}")
        self.assertTrue(kwargs["autocommit"])
        self.assertIs(kwargs["row_factory"], self.dict_row)
        connection.transaction.assert_called_once_with()
        transaction_context.__exit__.assert_called_once_with(None, None, None)

    @patch("app.postgresql._load_psycopg")
    def test_application_error_reaches_transaction_for_rollback(self, load_psycopg) -> None:
        """A pure claim failure cannot commit partial state before model work."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        connection = self._connection()
        transaction_context = connection.transaction.return_value

        with self.assertRaisesRegex(ValueError, "simulated task-claim failure"):
            with self.adapter.write_cursor():
                raise ValueError("simulated task-claim failure")

        exit_args = transaction_context.__exit__.call_args.args
        self.assertIs(exit_args[0], ValueError)
        self.assertIn("simulated task-claim failure", str(exit_args[1]))

    @patch("app.postgresql._load_psycopg")
    def test_driver_failure_becomes_a_safe_retryable_category(self, load_psycopg) -> None:
        """Raw PostgreSQL diagnostics cannot escape into future worker logs."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        FakePsycopg.connect.side_effect = FakePsycopgError("private database diagnostic")

        with self.assertRaises(ADTOFDatabaseUnavailable) as raised:
            with self.adapter.write_cursor():
                pass

        self.assertEqual(str(raised.exception), "PostgreSQL ADTOF task access is unavailable.")
        self.assertNotIn("private database diagnostic", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
