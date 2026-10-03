"""Restricted Psycopg connection boundary for the future Basic Pitch worker.

Basic Pitch obtains authority to process a stem from PostgreSQL's durable task
lease—not from a RabbitMQ delivery. This module converts the future Pod's
least-privilege Secret and fixed private Service settings into one short
Psycopg transaction. It holds no database transaction during MinIO I/O or CPU
model execution.

Psycopg imports lazily, allowing pure contract tests to run without a driver,
PostgreSQL server, or mounted local Secret. The later dependency/image task
must install the reviewed driver before a real worker can call this adapter.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.db.task_lease import DatabaseCursor


# These immutable local values name the one authoritative database boundary.
# The role bootstrap Job created this distinct login with only Basic Pitch task
# grants; a host port, an administrator role, or another database would break
# durable lease ownership and is rejected before the driver opens a socket.
DEFAULT_DATABASE_HOST = "clouddsp-postgresql.clouddsp-data.svc"
DEFAULT_DATABASE_PORT = 5432
LOCAL_DATABASE_NAME = "clouddsp_job_api"
LOCAL_BASIC_PITCH_DATABASE_USERNAME = "clouddsp-basic-pitch"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3
DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS = 5_000


class BasicPitchDatabaseConfigurationError(RuntimeError):
    """A required Pod setting is missing, malformed, or outside local policy.

    Public messages name a variable/category but never disclose a database
    host, DSN, password, or raw driver diagnostics into future worker logs.
    """


class BasicPitchDatabaseUnavailable(RuntimeError):
    """A retryable PostgreSQL failure with a safe non-sensitive message."""


def _required_environment_text(name: str, *, default: str | None = None) -> str:
    """Return non-empty control-free text without echoing a mounted Secret value."""

    value = os.environ.get(name, default)
    if (
        value is None
        or not isinstance(value, str)
        or not value
        or any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise BasicPitchDatabaseConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_positive_integer(*, name: str, value: str, maximum: int) -> int:
    """Parse a finite port/timeout before handing it to the database driver."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise BasicPitchDatabaseConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise BasicPitchDatabaseConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class BasicPitchDatabaseSettings:
    """The future Pod's minimal private PostgreSQL configuration.

    The permanent `clouddsp-basic-pitch-database-credentials` Secret supplies
    database/name/password fields. The non-secret host/port/timeout values are
    still validated to prevent a ConfigMap typo from redirecting the worker.
    ``password`` deliberately uses ``repr=False`` as defence in depth.
    """

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "BasicPitchDatabaseSettings":
        """Load fixed local settings and one restricted Secret without network I/O."""

        host = _required_environment_text("BASIC_PITCH_DB_HOST", default=DEFAULT_DATABASE_HOST)
        if host != DEFAULT_DATABASE_HOST:
            raise BasicPitchDatabaseConfigurationError(
                "BASIC_PITCH_DB_HOST must match the local PostgreSQL Service DNS."
            )
        port = _bounded_positive_integer(
            name="BASIC_PITCH_DB_PORT",
            value=os.environ.get("BASIC_PITCH_DB_PORT", str(DEFAULT_DATABASE_PORT)),
            maximum=65_535,
        )
        if port != DEFAULT_DATABASE_PORT:
            raise BasicPitchDatabaseConfigurationError(
                "BASIC_PITCH_DB_PORT must match the local PostgreSQL Service port."
            )
        database = _required_environment_text("BASIC_PITCH_DB_NAME")
        if database != LOCAL_DATABASE_NAME:
            raise BasicPitchDatabaseConfigurationError(
                "BASIC_PITCH_DB_NAME must match the authoritative CloudDSP database."
            )
        username = _required_environment_text("BASIC_PITCH_DB_USERNAME")
        if username != LOCAL_BASIC_PITCH_DATABASE_USERNAME:
            raise BasicPitchDatabaseConfigurationError(
                "BASIC_PITCH_DB_USERNAME must match the restricted Basic Pitch database role."
            )
        return cls(
            host=host,
            port=port,
            database=database,
            username=username,
            password=_required_environment_text("BASIC_PITCH_DB_PASSWORD"),
            connect_timeout_seconds=_bounded_positive_integer(
                name="BASIC_PITCH_DB_CONNECT_TIMEOUT_SECONDS",
                value=os.environ.get(
                    "BASIC_PITCH_DB_CONNECT_TIMEOUT_SECONDS",
                    str(DEFAULT_CONNECT_TIMEOUT_SECONDS),
                ),
                # A blocked cluster Service must release this worker slot fast.
                maximum=10,
            ),
        )


def _load_psycopg() -> tuple[Any, Any]:
    """Load Psycopg and dictionary-row support only when a transaction starts."""

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise BasicPitchDatabaseConfigurationError(
            "Pinned Basic Pitch PostgreSQL client dependency is unavailable."
        ) from error
    return psycopg, dict_row


class PsycopgBasicPitchDatabase:
    """Create short dictionary-row transactions for Basic Pitch lease SQL.

    One fresh connection per durable action makes the commit-before-model
    boundary easy to inspect. A future reviewed pool may replace this only if
    it preserves the same restricted credential and transaction lifetime.
    """

    def __init__(self, settings: BasicPitchDatabaseSettings | None = None) -> None:
        # Resolve environment settings at worker start rather than module import
        # so a missing Secret produces a safe runtime configuration error.
        self._settings = BasicPitchDatabaseSettings.from_environment() if settings is None else settings

    def _connection_kwargs(self) -> dict[str, object]:
        """Build explicit driver arguments instead of a password-bearing DSN."""

        return {
            "host": self._settings.host,
            "port": self._settings.port,
            "dbname": self._settings.database,
            "user": self._settings.username,
            "password": self._settings.password,
            "connect_timeout": self._settings.connect_timeout_seconds,
            # This server-side limit is separate from TCP connection timeout.
            # It bounds task-claim/start lock retention during database trouble.
            "options": f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS}",
            "application_name": "clouddsp-basic-pitch-task-lease",
            # The explicit transaction below owns BEGIN/COMMIT/ROLLBACK, so
            # a connection never carries an idle implicit transaction forward.
            "autocommit": True,
        }

    @contextmanager
    def write_cursor(self) -> Generator[DatabaseCursor, None, None]:
        """Yield one cursor in a short commit-or-rollback transaction scope.

        A later composition will call one pure claim/start/result statement in
        this context. Normal exit commits before the caller can begin model
        work; exceptions roll back before any RabbitMQ acknowledgement choice.
        The scope must end before MinIO download, Basic Pitch CPU work, or MIDI
        upload so no durable lock is held during external I/O.
        """

        psycopg, dict_row = _load_psycopg()
        try:
            with psycopg.connect(
                **self._connection_kwargs(),
                row_factory=dict_row,
            ) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        yield cursor
        except (psycopg.Error, OSError) as error:
            raise BasicPitchDatabaseUnavailable(
                "PostgreSQL Basic Pitch task access is unavailable."
            ) from error
