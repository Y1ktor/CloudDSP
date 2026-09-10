"""Unit tests for the Demucs Psycopg connection/transaction boundary.

These tests use a fake driver and synthetic credentials. They never contact
PostgreSQL, resolve cluster DNS, read an ignored local Secret, or test task
claim SQL. Claim/recovery semantics remain separate focused tasks.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from app.postgresql import (
    DEFAULT_DATABASE_HOST,
    DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS,
    LOCAL_DATABASE_NAME,
    LOCAL_DEMUCS_DATABASE_USERNAME,
    DemucsDatabaseConfigurationError,
    DemucsDatabaseSettings,
    DemucsDatabaseUnavailable,
    PsycopgDemucsDatabase,
)


VALID_ENVIRONMENT = {
    "DEMUCS_DB_NAME": LOCAL_DATABASE_NAME,
    "DEMUCS_DB_USERNAME": LOCAL_DEMUCS_DATABASE_USERNAME,
    # Synthetic test text only; it is never copied from a real local Secret.
    "DEMUCS_DB_PASSWORD": "unit-test-demucs-database-password",
}


class FakePsycopgError(Exception):
    """Stand in for Psycopg's base error in isolated no-network tests."""


class FakePsycopg:
    """Only the small driver surface this connection boundary needs."""

    Error = FakePsycopgError
    connect = MagicMock()


def settings() -> DemucsDatabaseSettings:
    """Return explicit non-secret settings for mocked-driver assertions."""

    return DemucsDatabaseSettings(
        host=DEFAULT_DATABASE_HOST,
        port=5432,
        database=LOCAL_DATABASE_NAME,
        username=LOCAL_DEMUCS_DATABASE_USERNAME,
        password="not-a-real-password",
        connect_timeout_seconds=3,
    )


class DemucsDatabaseSettingsTests(unittest.TestCase):
    """Prove a future Pod accepts only its narrow local database authority."""

    def settings_from(self, overrides: dict[str, str] | None = None) -> DemucsDatabaseSettings:
        """Load a fully isolated synthetic environment for one assertion."""

        environment = dict(VALID_ENVIRONMENT)
        if overrides:
            environment.update(overrides)
        with patch.dict(os.environ, environment, clear=True):
            return DemucsDatabaseSettings.from_environment()

    def test_defaults_to_private_service_and_hides_password(self) -> None:
        """A worker uses Service DNS and cannot expose a repr credential."""

        loaded = self.settings_from()

        self.assertEqual(loaded.host, DEFAULT_DATABASE_HOST)
        self.assertEqual(loaded.port, 5432)
        self.assertEqual(loaded.database, LOCAL_DATABASE_NAME)
        self.assertEqual(loaded.username, LOCAL_DEMUCS_DATABASE_USERNAME)
        self.assertNotIn(VALID_ENVIRONMENT["DEMUCS_DB_PASSWORD"], repr(loaded))

    def test_missing_password_fails_before_psycopg_is_loaded(self) -> None:
        """A malformed Secret is configuration failure, not a driver leak."""

        environment = dict(VALID_ENVIRONMENT)
        environment.pop("DEMUCS_DB_PASSWORD")
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(DemucsDatabaseConfigurationError) as raised:
                DemucsDatabaseSettings.from_environment()

        self.assertEqual(
            str(raised.exception),
            "Required environment variable DEMUCS_DB_PASSWORD is absent.",
        )

    def test_rejects_widened_host_database_or_database_role(self) -> None:
        """A typo cannot silently switch to an admin or host database."""

        for overrides in (
            {"DEMUCS_DB_HOST": "localhost"},
            {"DEMUCS_DB_NAME": "another_database"},
            {"DEMUCS_DB_USERNAME": "clouddsp-admin"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(DemucsDatabaseConfigurationError):
                    self.settings_from(overrides)

    def test_rejects_invalid_port_and_connect_timeout(self) -> None:
        """Malformed environment text cannot become unbounded driver behavior."""

        for overrides in (
            {"DEMUCS_DB_PORT": "0"},
            {"DEMUCS_DB_PORT": "not-a-number"},
            {"DEMUCS_DB_CONNECT_TIMEOUT_SECONDS": "11"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(DemucsDatabaseConfigurationError):
                    self.settings_from(overrides)


class PsycopgDemucsDatabaseTests(unittest.TestCase):
    """Prove every later claim/recovery action gets a short transaction scope."""

    def setUp(self) -> None:
        """Reset shared fake-driver state between normal and error-path tests."""

        FakePsycopg.connect.reset_mock(return_value=True, side_effect=True)
        self.adapter = PsycopgDemucsDatabase(settings())
        self.dict_row = object()

    def _connection(self) -> MagicMock:
        """Create the context-managed fake connection used by each test."""

        connection = MagicMock()
        FakePsycopg.connect.return_value.__enter__.return_value = connection
        return connection

    @patch("app.postgresql._load_psycopg")
    def test_write_cursor_uses_bounded_dictionary_row_transaction(self, load_psycopg) -> None:
        """A durable claim can commit before the later AMQP acknowledgement."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        connection = self._connection()
        transaction_context = connection.transaction.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        with self.adapter.write_cursor() as received_cursor:
            self.assertIs(received_cursor, cursor)

        kwargs = FakePsycopg.connect.call_args.kwargs
        self.assertEqual(kwargs["host"], DEFAULT_DATABASE_HOST)
        self.assertEqual(kwargs["application_name"], "clouddsp-demucs-task-lease")
        self.assertEqual(kwargs["options"], f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS}")
        self.assertTrue(kwargs["autocommit"])
        self.assertIs(kwargs["row_factory"], self.dict_row)
        connection.transaction.assert_called_once_with()
        transaction_context.__exit__.assert_called_once_with(None, None, None)

    @patch("app.postgresql._load_psycopg")
    def test_application_error_reaches_transaction_for_rollback(self, load_psycopg) -> None:
        """An adapter failure cannot commit partial task state before an ack."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        connection = self._connection()
        transaction_context = connection.transaction.return_value

        with self.assertRaisesRegex(ValueError, "simulated lease failure"):
            with self.adapter.write_cursor():
                raise ValueError("simulated lease failure")

        exit_args = transaction_context.__exit__.call_args.args
        self.assertIs(exit_args[0], ValueError)
        self.assertIn("simulated lease failure", str(exit_args[1]))

    @patch("app.postgresql._load_psycopg")
    def test_driver_failure_becomes_safe_unavailable_category(self, load_psycopg) -> None:
        """Connection diagnostics cannot escape into future worker logs."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        FakePsycopg.connect.side_effect = FakePsycopgError("private database diagnostic")

        with self.assertRaises(DemucsDatabaseUnavailable) as raised:
            with self.adapter.write_cursor():
                pass

        self.assertEqual(str(raised.exception), "PostgreSQL Demucs task access is unavailable.")
        self.assertNotIn("private database diagnostic", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
