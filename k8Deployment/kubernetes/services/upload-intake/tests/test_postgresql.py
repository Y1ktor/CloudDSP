"""Unit tests for the concrete Psycopg upload-intake transaction adapter.

Psycopg is intentionally not installed in the development interpreter for this
learning task. The adapter loads it lazily, and these tests replace that loader
with a fake driver. They prove connection arguments, read-only enforcement,
write transaction ordering, rollback propagation, and safe driver failures
without connecting to PostgreSQL or exposing a credential.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from app.postgresql import (
    DEFAULT_DATABASE_HOST,
    PostgreSQLConfigurationError,
    PostgreSQLSettings,
    PostgreSQLUnavailable,
    PsycopgSourceIntakeDatabase,
)


class FakePsycopgError(Exception):
    """A stand-in for Psycopg's base Error class in no-network tests."""


class FakePsycopg:
    """A minimal dynamically injected driver with a mock ``connect`` method."""

    Error = FakePsycopgError
    connect = MagicMock()


def settings() -> PostgreSQLSettings:
    """Return non-secret test-only settings for mocked driver assertions."""

    return PostgreSQLSettings(
        host="postgresql.test",
        port=5432,
        database="clouddsp_job_api",
        username="clouddsp-upload-intake",
        password="not-a-real-password",
        connect_timeout_seconds=3,
    )


class PostgreSQLSettingsTests(unittest.TestCase):
    """Prove the adapter uses local private defaults and required Secret keys."""

    @patch.dict(
        os.environ,
        {
            "UPLOAD_INTAKE_DB_NAME": "clouddsp_job_api",
            "UPLOAD_INTAKE_DB_USERNAME": "clouddsp-upload-intake",
            "UPLOAD_INTAKE_DB_PASSWORD": "test-only-password",
        },
        clear=True,
    )
    def test_settings_default_to_private_service_dns_and_hide_password_repr(self) -> None:
        """A future Pod defaults to Service DNS, never a Mac host port."""

        loaded = PostgreSQLSettings.from_environment()

        self.assertEqual(loaded.host, DEFAULT_DATABASE_HOST)
        self.assertEqual(loaded.port, 5432)
        self.assertNotIn("test-only-password", repr(loaded))

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_database_secret_value_fails_before_a_driver_connection(self) -> None:
        """A bad/missing Secret cannot become a low-level connection failure."""

        with self.assertRaises(PostgreSQLConfigurationError):
            PostgreSQLSettings.from_environment()


class PsycopgSourceIntakeDatabaseTests(unittest.TestCase):
    """Prove read and write contexts have their distinct transaction contracts."""

    def setUp(self) -> None:
        # This fake driver is a class-level test double. Reset its return value
        # and side effect as well as its call history so the intentional outage
        # in one test cannot leak into an unrelated transaction test.
        FakePsycopg.connect.reset_mock(return_value=True, side_effect=True)
        self.adapter = PsycopgSourceIntakeDatabase(settings())
        self.dict_row = object()

    def _connection(self) -> MagicMock:
        """Prepare a context-managed fake Psycopg connection and cursor."""

        connection = MagicMock()
        FakePsycopg.connect.return_value.__enter__.return_value = connection
        return connection

    @patch("app.postgresql._load_psycopg")
    def test_read_scope_uses_read_only_autocommit_connection(self, load_psycopg) -> None:
        """The lookup scope ends before a future external MinIO request."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        connection = self._connection()
        cursor = connection.cursor.return_value.__enter__.return_value

        with self.adapter.read_cursor() as received_cursor:
            self.assertIs(received_cursor, cursor)

        kwargs = FakePsycopg.connect.call_args.kwargs
        self.assertEqual(kwargs["host"], "postgresql.test")
        self.assertEqual(kwargs["application_name"], "clouddsp-upload-intake-read-pending")
        self.assertTrue(kwargs["autocommit"])
        self.assertIn("default_transaction_read_only=on", kwargs["options"])
        self.assertIs(kwargs["row_factory"], self.dict_row)
        connection.transaction.assert_not_called()

    @patch("app.postgresql._load_psycopg")
    def test_write_scope_wraps_cursor_in_explicit_transaction(self, load_psycopg) -> None:
        """Normal exit lets Psycopg commit one state-and-outbox transaction."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        connection = self._connection()
        transaction_context = connection.transaction.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        with self.adapter.write_cursor() as received_cursor:
            self.assertIs(received_cursor, cursor)

        kwargs = FakePsycopg.connect.call_args.kwargs
        self.assertEqual(kwargs["application_name"], "clouddsp-upload-intake-write-state")
        self.assertTrue(kwargs["autocommit"])
        self.assertNotIn("default_transaction_read_only=on", kwargs["options"])
        connection.transaction.assert_called_once_with()
        transaction_context.__exit__.assert_called_once_with(None, None, None)

    @patch("app.postgresql._load_psycopg")
    def test_write_scope_passes_application_exception_to_transaction_for_rollback(self, load_psycopg) -> None:
        """A failed state transition cannot be committed before AMQP retry."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        connection = self._connection()
        transaction_context = connection.transaction.return_value

        with self.assertRaisesRegex(ValueError, "simulated handler failure"):
            with self.adapter.write_cursor():
                raise ValueError("simulated handler failure")

        transaction_exit_args = transaction_context.__exit__.call_args.args
        self.assertIs(transaction_exit_args[0], ValueError)
        self.assertIn("simulated handler failure", str(transaction_exit_args[1]))

    @patch("app.postgresql._load_psycopg")
    def test_driver_connection_error_becomes_safe_retryable_category(self, load_psycopg) -> None:
        """Neither host nor driver diagnostic leaves the adapter boundary."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        FakePsycopg.connect.side_effect = FakePsycopgError("private connection diagnostic")

        with self.assertRaises(PostgreSQLUnavailable) as raised:
            with self.adapter.read_cursor():
                pass

        self.assertEqual(str(raised.exception), "PostgreSQL upload-intake read is unavailable.")
