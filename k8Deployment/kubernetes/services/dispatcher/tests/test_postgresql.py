"""Unit tests for the dispatcher Psycopg transaction boundary.

Psycopg is loaded lazily, so these tests replace it with a fake driver. They
prove connection settings, explicit commit/rollback scope, and safe driver
failure handling without opening PostgreSQL or reading a real Secret.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from app.postgresql import (
    DEFAULT_DATABASE_HOST,
    DispatcherDatabaseConfigurationError,
    DispatcherDatabaseSettings,
    DispatcherDatabaseUnavailable,
    PsycopgDispatcherDatabase,
)


class FakePsycopgError(Exception):
    """Stand in for Psycopg's base driver exception in no-network tests."""


class FakePsycopg:
    """A small fake driver surface used by the transaction adapter tests."""

    Error = FakePsycopgError
    connect = MagicMock()


def settings() -> DispatcherDatabaseSettings:
    """Return non-secret test-only settings for mocked driver assertions."""

    return DispatcherDatabaseSettings(
        host="postgresql.test",
        port=5432,
        database="clouddsp_job_api",
        username="clouddsp-dispatcher",
        password="not-a-real-password",
        connect_timeout_seconds=3,
    )


class DispatcherDatabaseSettingsTests(unittest.TestCase):
    """Prove the adapter uses private Service defaults and hides its password."""

    @patch.dict(
        os.environ,
        {
            "DISPATCHER_DB_NAME": "clouddsp_job_api",
            "DISPATCHER_DB_USERNAME": "clouddsp-dispatcher",
            "DISPATCHER_DB_PASSWORD": "test-only-password",
        },
        clear=True,
    )
    def test_defaults_to_private_service_dns_and_hides_password(self) -> None:
        """A dispatcher Pod does not use a Mac host port to reach PostgreSQL."""

        loaded = DispatcherDatabaseSettings.from_environment()

        self.assertEqual(loaded.host, DEFAULT_DATABASE_HOST)
        self.assertEqual(loaded.port, 5432)
        self.assertNotIn("test-only-password", repr(loaded))

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_secret_value_fails_before_a_driver_connection(self) -> None:
        """A bad Secret is a clear configuration failure, not a vague driver error."""

        with self.assertRaises(DispatcherDatabaseConfigurationError):
            DispatcherDatabaseSettings.from_environment()


class PsycopgDispatcherDatabaseTests(unittest.TestCase):
    """Prove each durable outbox action gets its own short write transaction."""

    def setUp(self) -> None:
        """Reset the shared fake driver so one test's outage cannot leak."""

        FakePsycopg.connect.reset_mock(return_value=True, side_effect=True)
        self.adapter = PsycopgDispatcherDatabase(settings())
        self.dict_row = object()

    def _connection(self) -> MagicMock:
        """Prepare a context-managed fake connection, transaction, and cursor."""

        connection = MagicMock()
        FakePsycopg.connect.return_value.__enter__.return_value = connection
        return connection

    @patch("app.postgresql._load_psycopg")
    def test_write_scope_uses_explicit_transaction_and_dictionary_rows(self, load_psycopg) -> None:
        """A claim/completion SQL call commits before the future AMQP step."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        connection = self._connection()
        transaction_context = connection.transaction.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        with self.adapter.write_cursor() as received_cursor:
            self.assertIs(received_cursor, cursor)

        kwargs = FakePsycopg.connect.call_args.kwargs
        self.assertEqual(kwargs["host"], "postgresql.test")
        self.assertEqual(kwargs["application_name"], "clouddsp-dispatcher-outbox")
        self.assertEqual(kwargs["options"], "-c statement_timeout=5000")
        self.assertTrue(kwargs["autocommit"])
        self.assertIs(kwargs["row_factory"], self.dict_row)
        connection.transaction.assert_called_once_with()
        transaction_context.__exit__.assert_called_once_with(None, None, None)

    @patch("app.postgresql._load_psycopg")
    def test_application_error_reaches_transaction_for_rollback(self, load_psycopg) -> None:
        """A failed outbox SQL operation cannot commit a partial state change."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        connection = self._connection()
        transaction_context = connection.transaction.return_value

        with self.assertRaisesRegex(ValueError, "simulated outbox failure"):
            with self.adapter.write_cursor():
                raise ValueError("simulated outbox failure")

        exit_args = transaction_context.__exit__.call_args.args
        self.assertIs(exit_args[0], ValueError)
        self.assertIn("simulated outbox failure", str(exit_args[1]))

    @patch("app.postgresql._load_psycopg")
    def test_driver_connection_failure_becomes_safe_unavailable_category(self, load_psycopg) -> None:
        """The future supervisor receives no host/driver diagnostic to log."""

        load_psycopg.return_value = (FakePsycopg, self.dict_row)
        FakePsycopg.connect.side_effect = FakePsycopgError("private connection diagnostic")

        with self.assertRaises(DispatcherDatabaseUnavailable) as raised:
            with self.adapter.write_cursor():
                pass

        self.assertEqual(str(raised.exception), "PostgreSQL dispatcher outbox access is unavailable.")
        self.assertNotIn("private connection diagnostic", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
