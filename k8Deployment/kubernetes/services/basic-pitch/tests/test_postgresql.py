"""Unit tests for Basic Pitch's Psycopg connection/transaction boundary.

Tests use a fake driver and synthetic values. They never resolve cluster DNS,
contact PostgreSQL, load a real ignored Secret, or execute task/model logic.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from app.db.postgresql import (
    DEFAULT_DATABASE_HOST,
    DEFAULT_DATABASE_PORT,
    DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS,
    LOCAL_BASIC_PITCH_DATABASE_USERNAME,
    LOCAL_DATABASE_NAME,
    BasicPitchDatabaseConfigurationError,
    BasicPitchDatabaseSettings,
    BasicPitchDatabaseUnavailable,
    PsycopgBasicPitchDatabase,
)


VALID_ENVIRONMENT = {
    "BASIC_PITCH_DB_NAME": LOCAL_DATABASE_NAME,
    "BASIC_PITCH_DB_USERNAME": LOCAL_BASIC_PITCH_DATABASE_USERNAME,
    # Synthetic test text only; it is never copied from a local Secret.
    "BASIC_PITCH_DB_PASSWORD": "unit-test-basic-pitch-database-password",
}


class FakePsycopgError(Exception):
    """Stand in for Psycopg's base exception without importing Psycopg."""


class FakePsycopg:
    """Expose only the small driver surface the connection boundary needs."""

    Error = FakePsycopgError
    connect = MagicMock()


def settings() -> BasicPitchDatabaseSettings:
    """Return explicit non-secret settings for mocked connection assertions."""

    return BasicPitchDatabaseSettings(
        host=DEFAULT_DATABASE_HOST,
        port=DEFAULT_DATABASE_PORT,
        database=LOCAL_DATABASE_NAME,
        username=LOCAL_BASIC_PITCH_DATABASE_USERNAME,
        password="not-a-real-password",
        connect_timeout_seconds=3,
    )


class BasicPitchDatabaseSettingsTests(unittest.TestCase):
    """Prove a future Pod can use only its restricted local database authority."""

    def settings_from(self, overrides: dict[str, str] | None = None) -> BasicPitchDatabaseSettings:
        """Load a complete isolated environment without exposing real credentials."""

        environment = dict(VALID_ENVIRONMENT)
        if overrides:
            environment.update(overrides)
        with patch.dict(os.environ, environment, clear=True):
            return BasicPitchDatabaseSettings.from_environment()

    def test_defaults_to_private_service_and_hides_password(self) -> None:
        """The worker uses Service DNS and a normal repr cannot render its Secret."""

        loaded = self.settings_from()

        self.assertEqual(loaded.host, DEFAULT_DATABASE_HOST)
        self.assertEqual(loaded.port, DEFAULT_DATABASE_PORT)
        self.assertEqual(loaded.database, LOCAL_DATABASE_NAME)
        self.assertEqual(loaded.username, LOCAL_BASIC_PITCH_DATABASE_USERNAME)
        self.assertNotIn(VALID_ENVIRONMENT["BASIC_PITCH_DB_PASSWORD"], repr(loaded))

    def test_missing_password_fails_before_the_driver_loads(self) -> None:
        """Malformed Secret input remains a configuration category, not a driver leak."""

        environment = dict(VALID_ENVIRONMENT)
        environment.pop("BASIC_PITCH_DB_PASSWORD")
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(BasicPitchDatabaseConfigurationError) as raised:
                BasicPitchDatabaseSettings.from_environment()

        self.assertEqual(
            str(raised.exception),
            "Required environment variable BASIC_PITCH_DB_PASSWORD is absent.",
        )

    def test_rejects_widened_service_database_or_role(self) -> None:
        """A ConfigMap/Secret typo cannot silently become administrator access."""

        for overrides in (
            {"BASIC_PITCH_DB_HOST": "localhost"},
            {"BASIC_PITCH_DB_PORT": "6543"},
            {"BASIC_PITCH_DB_NAME": "another_database"},
            {"BASIC_PITCH_DB_USERNAME": "clouddsp-admin"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(BasicPitchDatabaseConfigurationError):
                    self.settings_from(overrides)

    def test_rejects_invalid_port_and_connect_timeout(self) -> None:
        """Invalid environment text cannot become unbounded driver behavior."""

        for overrides in (
            {"BASIC_PITCH_DB_PORT": "0"},
            {"BASIC_PITCH_DB_PORT": "not-a-number"},
            {"BASIC_PITCH_DB_CONNECT_TIMEOUT_SECONDS": "11"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(BasicPitchDatabaseConfigurationError):
                    self.settings_from(overrides)


class PsycopgBasicPitchDatabaseTests(unittest.TestCase):
    """Prove each later lease action receives one short transaction scope."""

    def setUp(self) -> None:
        """Reset the shared fake driver between normal/error-path assertions."""

        FakePsycopg.connect.reset_mock(return_value=True, side_effect=True)
        self.adapter = PsycopgBasicPitchDatabase(settings())
        self.dict_row = object()

    def _connection(self) -> MagicMock:
        """Create the nested context-managed connection expected by the adapter."""

        connection = MagicMock()
        FakePsycopg.connect.return_value.__enter__.return_value = connection
        return connection

    @patch("app.db.postgresql._load_psycopg")
    def test_write_cursor_uses_a_bounded_dictionary_row_transaction(self, load_psycopg) -> None:
        """A task-start commit can happen before later Basic Pitch CPU work."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        connection = self._connection()
        transaction_context = connection.transaction.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        with self.adapter.write_cursor() as received_cursor:
            self.assertIs(received_cursor, cursor)

        kwargs = FakePsycopg.connect.call_args.kwargs
        self.assertEqual(kwargs["host"], DEFAULT_DATABASE_HOST)
        self.assertEqual(kwargs["port"], DEFAULT_DATABASE_PORT)
        self.assertEqual(kwargs["application_name"], "clouddsp-basic-pitch-task-lease")
        self.assertEqual(kwargs["options"], f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS}")
        self.assertTrue(kwargs["autocommit"])
        self.assertIs(kwargs["row_factory"], self.dict_row)
        connection.transaction.assert_called_once_with()
        transaction_context.__exit__.assert_called_once_with(None, None, None)

    @patch("app.db.postgresql._load_psycopg")
    def test_application_error_reaches_transaction_for_rollback(self, load_psycopg) -> None:
        """A pure lease failure cannot commit partial state before model work."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        connection = self._connection()
        transaction_context = connection.transaction.return_value

        with self.assertRaisesRegex(ValueError, "simulated task-start failure"):
            with self.adapter.write_cursor():
                raise ValueError("simulated task-start failure")

        exit_args = transaction_context.__exit__.call_args.args
        self.assertIs(exit_args[0], ValueError)
        self.assertIn("simulated task-start failure", str(exit_args[1]))

    @patch("app.db.postgresql._load_psycopg")
    def test_driver_failure_becomes_a_safe_retryable_category(self, load_psycopg) -> None:
        """Raw PostgreSQL driver diagnostics must not escape into worker logs."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        FakePsycopg.connect.side_effect = FakePsycopgError("private database diagnostic")

        with self.assertRaises(BasicPitchDatabaseUnavailable) as raised:
            with self.adapter.write_cursor():
                pass

        self.assertEqual(str(raised.exception), "PostgreSQL Basic Pitch task access is unavailable.")
        self.assertNotIn("private database diagnostic", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
