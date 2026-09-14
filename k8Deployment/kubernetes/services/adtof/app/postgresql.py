"""Restricted Psycopg connection boundary for the future ADTOF worker.

ADTOF derives authority from PostgreSQL's durable drums-task lease, never from
a RabbitMQ delivery alone. This adapter turns the future Pod's least-privilege
runtime Secret and fixed internal Service coordinates into one short Psycopg
transaction. It deliberately holds no database transaction while performing
MinIO I/O, CPU inference, output upload, or broker acknowledgement.

Psycopg imports lazily, allowing contract tests to run without the driver, a
PostgreSQL server, or any mounted Secret. Pinning/installing the client library
belongs to a later dedicated dependency/image task before a real worker can
invoke this adapter.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.task_claim import DatabaseCursor


# These immutable local values identify the one authoritative task database.
# The completed ADTOF bootstrap Job created this restricted role. Accepting a
# host port, another database, or an administrator account here could turn a
# future ConfigMap/Secret mistake into widened durable-processing authority.
DEFAULT_DATABASE_HOST = "clouddsp-postgresql.clouddsp-data.svc"
DEFAULT_DATABASE_PORT = 5432
LOCAL_DATABASE_NAME = "clouddsp_job_api"
LOCAL_ADTOF_DATABASE_USERNAME = "clouddsp-adtof"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3
DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS = 5_000


class ADTOFDatabaseConfigurationError(RuntimeError):
    """A required Pod setting is missing, malformed, or outside local policy.

    Messages identify the configuration category but never include a host, DSN,
    password, or raw driver diagnostic that could expose private cluster data.
    """


class ADTOFDatabaseUnavailable(RuntimeError):
    """A retryable PostgreSQL access failure with a non-sensitive message."""


def _required_environment_text(name: str, *, default: str | None = None) -> str:
    """Read non-empty control-free Pod input without echoing Secret values."""

    value = os.environ.get(name, default)
    if (
        value is None
        or not isinstance(value, str)
        or not value
        or any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise ADTOFDatabaseConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_positive_integer(*, name: str, value: str, maximum: int) -> int:
    """Parse a finite port/timeout before handing it to the database driver."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise ADTOFDatabaseConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise ADTOFDatabaseConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class ADTOFDatabaseSettings:
    """The future Pod's minimal private PostgreSQL configuration.

    The long-lived `clouddsp-adtof-database-credentials` Secret supplies the
    database/name/password trio. Service DNS, port, and timeout are non-secret
    Deployment settings but are still validated, so an accidental manifest edit
    cannot redirect this constrained credential. ``repr=False`` protects the
    password if a settings object is accidentally rendered in a future log.
    """

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "ADTOFDatabaseSettings":
        """Load fixed local settings plus one restricted Secret without I/O."""

        host = _required_environment_text("ADTOF_DB_HOST", default=DEFAULT_DATABASE_HOST)
        if host != DEFAULT_DATABASE_HOST:
            raise ADTOFDatabaseConfigurationError(
                "ADTOF_DB_HOST must match the local PostgreSQL Service DNS."
            )
        port = _bounded_positive_integer(
            name="ADTOF_DB_PORT",
            value=os.environ.get("ADTOF_DB_PORT", str(DEFAULT_DATABASE_PORT)),
            maximum=65_535,
        )
        if port != DEFAULT_DATABASE_PORT:
            raise ADTOFDatabaseConfigurationError(
                "ADTOF_DB_PORT must match the local PostgreSQL Service port."
            )
        database = _required_environment_text("ADTOF_DB_NAME")
        if database != LOCAL_DATABASE_NAME:
            raise ADTOFDatabaseConfigurationError(
                "ADTOF_DB_NAME must match the authoritative CloudDSP database."
            )
        username = _required_environment_text("ADTOF_DB_USERNAME")
        if username != LOCAL_ADTOF_DATABASE_USERNAME:
            raise ADTOFDatabaseConfigurationError(
                "ADTOF_DB_USERNAME must match the restricted ADTOF database role."
            )
        return cls(
            host=host,
            port=port,
            database=database,
            username=username,
            password=_required_environment_text("ADTOF_DB_PASSWORD"),
            connect_timeout_seconds=_bounded_positive_integer(
                name="ADTOF_DB_CONNECT_TIMEOUT_SECONDS",
                value=os.environ.get(
                    "ADTOF_DB_CONNECT_TIMEOUT_SECONDS",
                    str(DEFAULT_CONNECT_TIMEOUT_SECONDS),
                ),
                # A blocked in-cluster Service must release a worker slot fast.
                maximum=10,
            ),
        )


def _load_psycopg() -> tuple[Any, Any]:
    """Load Psycopg/dictionary-row support only when a transaction begins."""

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise ADTOFDatabaseConfigurationError(
            "Pinned ADTOF PostgreSQL client dependency is unavailable."
        ) from error
    return psycopg, dict_row


class PsycopgADTOFDatabase:
    """Create short dictionary-row transactions for ADTOF lease operations.

    One fresh connection per durable action keeps the commit-before-broker/model
    boundary inspectable. A later connection pool could replace this only after
    preserving the same role, settings validation, and transaction lifetime.
    """

    def __init__(self, settings: ADTOFDatabaseSettings | None = None) -> None:
        # Resolve mounted environment at worker construction, not module import,
        # so a missing Secret produces a safe configuration error at startup.
        self._settings = ADTOFDatabaseSettings.from_environment() if settings is None else settings

    def _connection_kwargs(self) -> dict[str, object]:
        """Build explicit driver arguments instead of a password-bearing DSN."""

        return {
            "host": self._settings.host,
            "port": self._settings.port,
            "dbname": self._settings.database,
            "user": self._settings.username,
            "password": self._settings.password,
            "connect_timeout": self._settings.connect_timeout_seconds,
            # Server-side statement timeout is distinct from TCP connection
            # timeout. It bounds task/Job locks during database-side trouble.
            "options": f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS}",
            "application_name": "clouddsp-adtof-task-lease",
            # The nested transaction context explicitly owns BEGIN/COMMIT/
            # ROLLBACK, preventing an implicit transaction from leaking.
            "autocommit": True,
        }

    @contextmanager
    def write_cursor(self) -> Generator[DatabaseCursor, None, None]:
        """Yield one short dictionary-row commit-or-rollback transaction.

        The first-claim wrapper calls its pure SQL adapter only inside this
        context. Normal exit commits before a future worker can acknowledge a
        RabbitMQ delivery; an exception rolls back first. The context must end
        before MinIO download, CPU ADTOF inference, output upload, or long
        backoff so a durable row lock is never held across external work.
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
            raise ADTOFDatabaseUnavailable(
                "PostgreSQL ADTOF task access is unavailable."
            ) from error
