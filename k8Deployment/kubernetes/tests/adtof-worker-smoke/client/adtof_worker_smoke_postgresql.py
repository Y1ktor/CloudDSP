"""Construct short restricted Psycopg connections for the future ADTOF smoke run.

This source-only factory consumes the already fixed smoke settings and the
hash-pinned Psycopg dependency lazily. It does not run a database function,
inspect a table, create a MinIO client, call RabbitMQ, invoke ADTOF, sleep,
build an image, or create a Kubernetes Job. The existing fixed-function
database adapter owns SQL, commit/rollback, and connection closure.

Each factory call opens one new non-autocommit dictionary-row connection. That
matches the database adapter's short prepare/observe/cleanup transaction
lifetimes: it explicitly commits mutations and rolls observation reads back,
so no transaction can remain open while the future outer loop waits for the
dispatcher or worker.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adtof_worker_smoke_contract import (
    EXPECTED_DATABASE_NAME,
    EXPECTED_DATABASE_USERNAME,
    PRIVATE_POSTGRESQL_HOST,
    PRIVATE_POSTGRESQL_PORT,
    ADTOFWorkerSmokeSettings,
)
from adtof_worker_smoke_database import ADTOFWorkerSmokeDatabaseConnection


# These fixed bounds apply to the smoke client's three tiny security-definer
# calls only. They do not limit the separate ADTOF worker's CPU model runtime.
ADTOF_WORKER_SMOKE_POSTGRESQL_CONNECT_TIMEOUT_SECONDS = 3
ADTOF_WORKER_SMOKE_POSTGRESQL_STATEMENT_TIMEOUT_MILLISECONDS = 5_000
ADTOF_WORKER_SMOKE_POSTGRESQL_APPLICATION_NAME = "clouddsp-adtof-worker-smoke"


class ADTOFWorkerSmokePostgreSQLConfigurationError(RuntimeError):
    """Reject a forged or incomplete settings object before a driver import/connect."""


class ADTOFWorkerSmokePostgreSQLDependencyError(RuntimeError):
    """The future image omitted the reviewed hash-pinned Psycopg dependency."""


class ADTOFWorkerSmokePostgreSQLInfrastructureError(RuntimeError):
    """Hide concrete driver/Service diagnostics when a fixed connection cannot open."""


def _valid_secret_text(value: object) -> bool:
    """Accept one non-empty control-free secret without logging or returning it."""

    return isinstance(value, str) and bool(value) and "\x00" not in value


def _validated_settings(value: object) -> ADTOFWorkerSmokeSettings:
    """Revalidate database authority even if a caller hand-builds the data class.

    ``ADTOFWorkerSmokeSettings.from_environment`` already fixes these values,
    but a frozen data class can still be constructed directly in Python. The
    runtime factory repeats the private Service/name/role checks immediately
    before credentials could reach a driver, so a test or future caller cannot
    redirect this restricted identity through a forged settings instance.
    """

    if (
        not isinstance(value, ADTOFWorkerSmokeSettings)
        or value.database_host != PRIVATE_POSTGRESQL_HOST
        or value.database_port != PRIVATE_POSTGRESQL_PORT
        or value.database_name != EXPECTED_DATABASE_NAME
        or value.database_username != EXPECTED_DATABASE_USERNAME
        or not _valid_secret_text(value.database_password)
    ):
        raise ADTOFWorkerSmokePostgreSQLConfigurationError(
            "ADTOF smoke PostgreSQL settings are invalid."
        )
    return value


def _load_psycopg() -> tuple[Any, Any]:
    """Import the locked Psycopg package only when a future factory is invoked."""

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise ADTOFWorkerSmokePostgreSQLDependencyError(
            "Pinned ADTOF smoke PostgreSQL dependency is unavailable."
        ) from error
    return psycopg, dict_row


def create_psycopg_adtof_worker_smoke_connection_factory(
    settings: ADTOFWorkerSmokeSettings,
) -> Callable[[], ADTOFWorkerSmokeDatabaseConnection]:
    """Return a zero-argument factory for the existing fixed-function adapter.

    Constructing the returned callable does not import Psycopg or open a
    network connection. The existing database adapter calls it once per short
    `prepare`, `observe`, or `cleanup` operation and retains ownership of the
    resulting connection's `commit`, `rollback`, and `close` lifecycle.
    """

    approved = _validated_settings(settings)

    def open_connection() -> ADTOFWorkerSmokeDatabaseConnection:
        """Open one bounded dictionary-row connection with no ambient DSN lookup."""

        psycopg, dict_row = _load_psycopg()
        try:
            return psycopg.connect(
                host=approved.database_host,
                port=approved.database_port,
                dbname=approved.database_name,
                user=approved.database_username,
                password=approved.database_password,
                connect_timeout=ADTOF_WORKER_SMOKE_POSTGRESQL_CONNECT_TIMEOUT_SECONDS,
                # This server-side limit is distinct from the TCP connect
                # timeout. It keeps an unavailable/locked fixed function from
                # consuming the outer smoke observation budget indefinitely.
                options=(
                    "-c statement_timeout="
                    f"{ADTOF_WORKER_SMOKE_POSTGRESQL_STATEMENT_TIMEOUT_MILLISECONDS}"
                ),
                application_name=ADTOF_WORKER_SMOKE_POSTGRESQL_APPLICATION_NAME,
                # The existing adapter deliberately calls commit/rollback per
                # operation; autocommit would hide that short transaction scope.
                autocommit=False,
                row_factory=dict_row,
            )
        except Exception as error:  # Concrete Psycopg/OS errors stay private to the runtime.
            raise ADTOFWorkerSmokePostgreSQLInfrastructureError(
                "ADTOF smoke PostgreSQL connection is unavailable."
            ) from error

    return open_connection
