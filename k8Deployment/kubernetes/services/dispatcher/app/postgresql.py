"""Short Psycopg transaction scopes for the dispatcher outbox runtime.

The future dispatcher needs three separate durable database actions: claim an
outbox lease, record confirmed publication, or schedule a known failure. This
module supplies the short commit-or-rollback cursor scope for each action. It
does not choose which SQL action to run, wait on RabbitMQ, hold a transaction
across a publish, create a connection pool, run a process loop, or call the
Kubernetes API.

Keeping the transaction boundary here makes the critical order visible: the
claim transaction must commit before the AMQP operation starts, and a later
publication/retry transaction must begin only after RabbitMQ has yielded a
known outcome. A database transaction cannot span PostgreSQL and RabbitMQ.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.outbox_lease import DatabaseCursor


# These defaults name the private in-cluster PostgreSQL Service. They are
# non-secret deployment configuration; database name/user/password come from
# the restricted dispatcher Secret and are never placed in a ConfigMap.
DEFAULT_DATABASE_HOST = "clouddsp-postgresql.clouddsp-data.svc"
DEFAULT_DATABASE_PORT = 5432
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3
DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS = 5_000


class DispatcherDatabaseConfigurationError(RuntimeError):
    """Raise a safe category for absent/invalid dispatcher Pod configuration."""


class DispatcherDatabaseUnavailable(RuntimeError):
    """Raise a safe retryable category without exposing Psycopg diagnostics."""


def _required_environment_text(name: str, *, default: str | None = None) -> str:
    """Read a required Secret/config value without logging its contents."""

    value = os.environ.get(name, default)
    if value is None or not isinstance(value, str) or not value or "\x00" in value:
        raise DispatcherDatabaseConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_positive_integer(*, name: str, value: str, maximum: int) -> int:
    """Parse a small port/timeout before it is handed to the PostgreSQL driver."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise DispatcherDatabaseConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise DispatcherDatabaseConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class DispatcherDatabaseSettings:
    """Private PostgreSQL connection settings for the future dispatcher Pod.

    The database login was created by the short-lived bootstrap Job and can
    access only the reviewed outbox columns. The password is hidden from the
    dataclass representation so accidental structured logging cannot disclose
    a Secret value.
    """

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "DispatcherDatabaseSettings":
        """Build settings from Deployment configuration and Secret key values."""

        return cls(
            host=_required_environment_text("DISPATCHER_DB_HOST", default=DEFAULT_DATABASE_HOST),
            port=_bounded_positive_integer(
                name="DISPATCHER_DB_PORT",
                value=os.environ.get("DISPATCHER_DB_PORT", str(DEFAULT_DATABASE_PORT)),
                maximum=65_535,
            ),
            database=_required_environment_text("DISPATCHER_DB_NAME"),
            username=_required_environment_text("DISPATCHER_DB_USERNAME"),
            password=_required_environment_text("DISPATCHER_DB_PASSWORD"),
            connect_timeout_seconds=_bounded_positive_integer(
                name="DISPATCHER_DB_CONNECT_TIMEOUT_SECONDS",
                value=os.environ.get(
                    "DISPATCHER_DB_CONNECT_TIMEOUT_SECONDS",
                    str(DEFAULT_CONNECT_TIMEOUT_SECONDS),
                ),
                # A failed Service/DNS/authentication attempt should quickly
                # return control to the later bounded supervisor backoff.
                maximum=10,
            ),
        )


def _load_psycopg() -> tuple[Any, Any]:
    """Load the hash-pinned driver only when a real database scope is entered.

    Focused lease/publisher tests use fakes and need no PostgreSQL package or
    socket. The later Linux image installs ``requirements.lock`` before this
    function can run in a real Pod.
    """

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise DispatcherDatabaseConfigurationError(
            "Pinned PostgreSQL client dependency is unavailable."
        ) from error
    return psycopg, dict_row


class PsycopgDispatcherDatabase:
    """Yield short commit-or-rollback outbox cursor scopes through Psycopg.

    This initial implementation opens one fresh connection per database action.
    That keeps lease ownership and commit timing easy to inspect: a claim is
    committed and its row lock released before the later runtime waits for a
    broker confirmation. A future performance task may put a reviewed pool
    behind this same method without widening database privileges.
    """

    def __init__(self, settings: DispatcherDatabaseSettings | None = None) -> None:
        # Resolve environment values when the future process starts, not during
        # module import. Unit tests can supply safe explicit settings instead.
        self._settings = DispatcherDatabaseSettings.from_environment() if settings is None else settings

    def _connection_kwargs(self) -> dict[str, object]:
        """Build parameterized driver options without constructing a DSN string."""

        return {
            "host": self._settings.host,
            "port": self._settings.port,
            "dbname": self._settings.database,
            "user": self._settings.username,
            "password": self._settings.password,
            "connect_timeout": self._settings.connect_timeout_seconds,
            # A broker is never contacted inside this transaction, so five
            # seconds is ample for one indexed claim/completion SQL statement.
            "options": f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS}",
            "application_name": "clouddsp-dispatcher-outbox",
            # Psycopg's explicit `connection.transaction()` below owns BEGIN,
            # COMMIT, and ROLLBACK. Autocommit outside that block ensures no
            # accidental idle-in-transaction state survives between actions.
            "autocommit": True,
        }

    @contextmanager
    def write_cursor(self) -> Generator[DatabaseCursor, None, None]:
        """Yield one outbox cursor inside a short commit-or-rollback transaction.

        Normal context exit commits one claim/mark/retry action. If the caller
        raises, Psycopg rolls that action back before the exception escapes.
        The future runtime must leave this context **before** it publishes AMQP
        work, otherwise a slow broker could retain an outbox row lock and hide
        it from other dispatcher replicas.
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
            raise DispatcherDatabaseUnavailable("PostgreSQL dispatcher outbox access is unavailable.") from error
