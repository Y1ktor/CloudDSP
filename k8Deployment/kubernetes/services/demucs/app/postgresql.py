"""Restricted Psycopg connection boundary for the future Demucs worker.

Demucs must obtain its authority to process an audio source from PostgreSQL,
not from a RabbitMQ delivery. This module translates the future Pod's narrow
database Secret and reviewed local configuration into one short Psycopg write
transaction. It intentionally does **not** run a claim query, acknowledge a
broker delivery, inspect MinIO, invoke FFprobe/Demucs, or create a Kubernetes
resource. Those are separate layers so no database transaction is held while a
worker waits on external I/O or CPU processing.

The Psycopg import is lazy. That keeps the pure contract tests runnable without
a PostgreSQL server, driver installation, or mounted credentials. The already
locked production image contains Psycopg; it will be rebuilt only when a later
runtime-composition task needs this new module in an executable worker.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.task_lease import DatabaseCursor


# These fixed values describe the one authoritative PostgreSQL Service and the
# restricted Demucs login created by the reviewed bootstrap Job. They are not
# secrets; strict checks reject an accidental Mac host port, administrator
# account, or separate database that could weaken durable-lease correctness.
DEFAULT_DATABASE_HOST = "clouddsp-postgresql.clouddsp-data.svc"
DEFAULT_DATABASE_PORT = 5432
LOCAL_DATABASE_NAME = "clouddsp_job_api"
LOCAL_DEMUCS_DATABASE_USERNAME = "clouddsp-demucs"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3
DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS = 5_000


class DemucsDatabaseConfigurationError(RuntimeError):
    """A Pod setting is missing, malformed, or outside local policy.

    Messages name only the setting. They never include a password, host value,
    DSN, or raw driver diagnostic that could reach a worker log.
    """


class DemucsDatabaseUnavailable(RuntimeError):
    """A retryable PostgreSQL failure with a safe public message.

    The original driver error remains the exception cause for local debugging,
    but future AMQP code must map/log only this bounded category and leave an
    unclaimed delivery unacknowledged for broker redelivery.
    """


def _required_environment_text(name: str, *, default: str | None = None) -> str:
    """Read required text without returning a Secret value in an error."""

    value = os.environ.get(name, default)
    if value is None or not isinstance(value, str) or not value or "\x00" in value:
        raise DemucsDatabaseConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_positive_integer(*, name: str, value: str, maximum: int) -> int:
    """Parse a small port/timeout before handing it to Psycopg."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise DemucsDatabaseConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise DemucsDatabaseConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class DemucsDatabaseSettings:
    """Private PostgreSQL values injected into a future Demucs Pod.

    ``DEMUCS_DB_NAME``, ``DEMUCS_DB_USERNAME``, and ``DEMUCS_DB_PASSWORD`` come
    from `clouddsp-demucs-database-credentials`. The future Deployment supplies
    host/port/timeout as explicit non-secret configuration. ``password`` is
    excluded from ``repr`` so ordinary diagnostics cannot serialize it.
    """

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "DemucsDatabaseSettings":
        """Load reviewed local-only settings without opening a socket."""

        host = _required_environment_text("DEMUCS_DB_HOST", default=DEFAULT_DATABASE_HOST)
        if host != DEFAULT_DATABASE_HOST:
            raise DemucsDatabaseConfigurationError(
                "DEMUCS_DB_HOST must match the local PostgreSQL Service DNS."
            )
        database = _required_environment_text("DEMUCS_DB_NAME")
        if database != LOCAL_DATABASE_NAME:
            raise DemucsDatabaseConfigurationError(
                "DEMUCS_DB_NAME must match the authoritative CloudDSP database."
            )
        username = _required_environment_text("DEMUCS_DB_USERNAME")
        if username != LOCAL_DEMUCS_DATABASE_USERNAME:
            raise DemucsDatabaseConfigurationError(
                "DEMUCS_DB_USERNAME must match the restricted Demucs database role."
            )
        return cls(
            host=host,
            port=_bounded_positive_integer(
                name="DEMUCS_DB_PORT",
                value=os.environ.get("DEMUCS_DB_PORT", str(DEFAULT_DATABASE_PORT)),
                maximum=65_535,
            ),
            database=database,
            username=username,
            password=_required_environment_text("DEMUCS_DB_PASSWORD"),
            connect_timeout_seconds=_bounded_positive_integer(
                name="DEMUCS_DB_CONNECT_TIMEOUT_SECONDS",
                value=os.environ.get(
                    "DEMUCS_DB_CONNECT_TIMEOUT_SECONDS",
                    str(DEFAULT_CONNECT_TIMEOUT_SECONDS),
                ),
                # A blocked Service connection must return to the future
                # supervisor quickly instead of holding a worker slot forever.
                maximum=10,
            ),
        )


def _load_psycopg() -> tuple[Any, Any]:
    """Load the hash-pinned driver only when a transaction is requested."""

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise DemucsDatabaseConfigurationError(
            "Pinned Demucs PostgreSQL client dependency is unavailable."
        ) from error
    return psycopg, dict_row


class PsycopgDemucsDatabase:
    """Create short dictionary-row transactions for future task-lease SQL.

    The first implementation opens one fresh connection per action. That makes
    claim-before-ack timing inspectable: the later claim context commits and
    releases locks before RabbitMQ is acknowledged. A reviewed pool may replace
    this implementation later only if it preserves short transaction lifetime
    and the restricted credential boundary.
    """

    def __init__(self, settings: DemucsDatabaseSettings | None = None) -> None:
        # Resolve environment values when the future process starts—not at
        # module import—so a missing mounted Secret is a clear runtime error.
        self._settings = DemucsDatabaseSettings.from_environment() if settings is None else settings

    def _connection_kwargs(self) -> dict[str, object]:
        """Build parameterized driver options without a password-bearing DSN."""

        return {
            "host": self._settings.host,
            "port": self._settings.port,
            "dbname": self._settings.database,
            "user": self._settings.username,
            "password": self._settings.password,
            "connect_timeout": self._settings.connect_timeout_seconds,
            # First-claim, renewal, and recovery queries are indexed and must
            # complete quickly. This server-side guard is separate from TCP
            # connection timeout and limits lock retention during an outage.
            "options": f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS}",
            "application_name": "clouddsp-demucs-task-lease",
            # The explicit connection.transaction() below owns BEGIN/COMMIT/
            # ROLLBACK. Outside it, no idle transaction survives a delivery.
            "autocommit": True,
        }

    @contextmanager
    def write_cursor(self) -> Generator[DatabaseCursor, None, None]:
        """Yield one cursor inside a short commit-or-rollback transaction.

        A future task will call exactly one pure `task_lease.py` operation in
        this context. Normal exit commits its durable outcome; any exception
        rolls it back before the caller decides whether a RabbitMQ ack is safe.
        This context ends before MinIO, FFprobe, Demucs, stem upload, or broker
        I/O begins.
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
            raise DemucsDatabaseUnavailable("PostgreSQL Demucs task access is unavailable.") from error
