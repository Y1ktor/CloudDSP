"""Concrete Psycopg transaction adapter for the upload-intake Pod.

The message handler depends on two abstract cursor scopes: a short read scope
and a commit-or-rollback write transaction. This module provides those scopes
using the dedicated ``clouddsp-upload-intake`` PostgreSQL login. It owns no
RabbitMQ acknowledgement, MinIO call, HTTP endpoint, Kubernetes API access, or
container lifecycle; it is only the safe connection/transaction bridge.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.database_transition import DatabaseCursor


# These non-secret defaults describe the current private in-cluster route. A
# future Deployment can state them explicitly while still keeping source code
# runnable in focused tests without a YAML parser or a Kubernetes client.
DEFAULT_DATABASE_HOST = "clouddsp-postgresql.clouddsp-data.svc"
DEFAULT_DATABASE_PORT = 5432
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3
DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS = 3_000


class PostgreSQLConfigurationError(RuntimeError):
    """Raise a safe category for missing/invalid Pod database configuration."""


class PostgreSQLUnavailable(RuntimeError):
    """Raise a retryable category without exposing a driver diagnostic.

    The original Psycopg/OSError remains the exception cause for local
    debugging, but callers must log/map only this stable message. A future
    AMQP adapter will leave its delivery unacknowledged and use the bounded
    retry path when this error reaches it.
    """


def _required_environment_text(name: str, *, default: str | None = None) -> str:
    """Read one required config/Secret value without ever logging its value."""

    value = os.environ.get(name, default)
    if value is None or not isinstance(value, str) or not value or "\x00" in value:
        raise PostgreSQLConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_positive_integer(*, name: str, value: str, maximum: int) -> int:
    """Parse a bounded port/timeout value before it reaches the driver."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise PostgreSQLConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise PostgreSQLConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class PostgreSQLSettings:
    """Private connection settings injected into the eventual consumer Pod.

    Database name and username are identifiers; password is a Secret value.
    Excluding it from the automatic dataclass representation prevents an
    accidental diagnostic log from disclosing the credential.
    """

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "PostgreSQLSettings":
        """Build settings from restricted Secret and Deployment env values."""

        host = _required_environment_text("UPLOAD_INTAKE_DB_HOST", default=DEFAULT_DATABASE_HOST)
        port = _bounded_positive_integer(
            name="UPLOAD_INTAKE_DB_PORT",
            value=os.environ.get("UPLOAD_INTAKE_DB_PORT", str(DEFAULT_DATABASE_PORT)),
            maximum=65_535,
        )
        connect_timeout_seconds = _bounded_positive_integer(
            name="UPLOAD_INTAKE_DB_CONNECT_TIMEOUT_SECONDS",
            value=os.environ.get(
                "UPLOAD_INTAKE_DB_CONNECT_TIMEOUT_SECONDS",
                str(DEFAULT_CONNECT_TIMEOUT_SECONDS),
            ),
            # A consumer should fail/retry promptly during a database outage;
            # one stuck TCP handshake must not occupy a RabbitMQ consumer slot.
            maximum=10,
        )
        return cls(
            host=host,
            port=port,
            database=_required_environment_text("UPLOAD_INTAKE_DB_NAME"),
            username=_required_environment_text("UPLOAD_INTAKE_DB_USERNAME"),
            password=_required_environment_text("UPLOAD_INTAKE_DB_PASSWORD"),
            connect_timeout_seconds=connect_timeout_seconds,
        )


def _load_psycopg() -> tuple[Any, Any]:
    """Load the pinned runtime driver lazily so pure unit tests need no SDK.

    The later container image installs ``requirements.lock`` with hash checks.
    Deferring the import until a real connection is requested lets parser,
    transition, and transaction-order tests run in a lightweight interpreter
    that deliberately has no PostgreSQL library or network access.
    """

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise PostgreSQLConfigurationError("Pinned PostgreSQL client dependency is unavailable.") from error
    return psycopg, dict_row


class PsycopgSourceIntakeDatabase:
    """Supply handler-compatible read and write cursor contexts through Psycopg.

    Every context opens a fresh bounded connection for this first local
    milestone. This keeps transactions easy to inspect and guarantees one
    message does not retain a connection after an error. A later performance
    task may add a reviewed pool behind this same two-method interface without
    changing message-handler correctness.
    """

    def __init__(self, settings: PostgreSQLSettings | None = None) -> None:
        # Construct settings lazily in the consumer process, rather than during
        # module import. That makes a missing Kubernetes Secret a clear runtime
        # configuration failure and keeps unit tests independent of os.environ.
        self._settings = PostgreSQLSettings.from_environment() if settings is None else settings

    def _connection_kwargs(self, *, application_name: str, read_only: bool) -> dict[str, object]:
        """Build shared safe driver arguments without a password-bearing DSN."""

        options = f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS}"
        if read_only:
            # PostgreSQL enforces this separately from application intent. An
            # accidental write added to the lookup path fails before changing a
            # job, even though the application's role has narrow UPDATE rights.
            options += " -c default_transaction_read_only=on"
        return {
            "host": self._settings.host,
            "port": self._settings.port,
            "dbname": self._settings.database,
            "user": self._settings.username,
            "password": self._settings.password,
            "connect_timeout": self._settings.connect_timeout_seconds,
            "options": options,
            "application_name": application_name,
            # Read scopes use one implicit read-only transaction. Write scopes
            # create an explicit ``connection.transaction()`` block below.
            "autocommit": True,
        }

    @contextmanager
    def read_cursor(self) -> Generator[DatabaseCursor, None, None]:
        """Yield a short read-only cursor and close it before MinIO HeadObject.

        Autocommit gives this single SELECT its own short transaction; it avoids
        retaining a transaction snapshot while the handler performs storage I/O.
        Any driver/OSError becomes the safe retryable category after cursors and
        the connection have been closed by their context managers.
        """

        psycopg, dict_row = _load_psycopg()
        try:
            with psycopg.connect(
                **self._connection_kwargs(
                    application_name="clouddsp-upload-intake-read-pending",
                    read_only=True,
                ),
                row_factory=dict_row,
            ) as connection:
                with connection.cursor() as cursor:
                    yield cursor
        except (psycopg.Error, OSError) as error:
            raise PostgreSQLUnavailable("PostgreSQL upload-intake read is unavailable.") from error

    @contextmanager
    def write_cursor(self) -> Generator[DatabaseCursor, None, None]:
        """Yield one cursor inside an explicit commit-or-rollback transaction.

        ``connection.transaction()`` sends PostgreSQL ``BEGIN`` before yielding
        the cursor. Normal context exit commits the conditional jobs update and
        its outbox insert together; any exception rolls both back before
        escaping. The handler returns an acknowledgement-safe result only after
        this context has completed.
        """

        psycopg, dict_row = _load_psycopg()
        try:
            with psycopg.connect(
                **self._connection_kwargs(
                    application_name="clouddsp-upload-intake-write-state",
                    read_only=False,
                ),
                row_factory=dict_row,
            ) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        yield cursor
        except (psycopg.Error, OSError) as error:
            raise PostgreSQLUnavailable("PostgreSQL upload-intake write is unavailable.") from error
