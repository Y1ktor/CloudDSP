"""Construct real clients and run the bounded Basic Pitch KEDA burst smoke.

This is the thin runtime edge around the dependency-free contract, adapters,
and runner. It imports Boto3/Psycopg only inside its two factories so unit
tests can replace them with in-memory fakes. The later Docker image installs
the matching hash-pinned dependencies from ``requirements.lock``.

The process has no HTTP listener, RabbitMQ client, Kubernetes API client,
worker/model invocation, ambient AWS credential discovery, or general
database/table authority. It runs the one narrow burst sequence and exits.
"""

from __future__ import annotations

import sys
from contextlib import suppress
from typing import cast

from basic_pitch_keda_burst_smoke import (
    BasicPitchKedaBurstConfigurationError,
    BasicPitchKedaBurstContractError,
    BurstSettings,
)
from burst_runner import run_burst
from minio_adapter import (
    BasicPitchKedaBurstMinioInfrastructureError,
    MinioBurstAdapter,
    S3Client,
)
from postgresql_adapter import (
    BasicPitchKedaBurstPostgresqlInfrastructureError,
    DatabaseConnection,
    PostgresqlBurstAdapter,
)


def open_database_connection(settings: BurstSettings) -> DatabaseConnection:
    """Open one short-lived, autocommit dictionary-row Psycopg connection.

    The restricted login can execute only the three security-definer functions.
    Autocommit keeps each function call its own database transaction, matching
    the database-owned atomic prepare/cleanup boundaries. The statement timeout
    bounds an individual query; it does not shorten the runner's 300-second
    cross-poll completion wait.
    """

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise BasicPitchKedaBurstPostgresqlInfrastructureError(
            "Pinned PostgreSQL burst-client dependency is unavailable."
        ) from error
    try:
        connection = psycopg.connect(
            host=settings.database_host,
            port=settings.database_port,
            dbname=settings.database_name,
            user=settings.database_username,
            password=settings.database_password,
            connect_timeout=5,
            autocommit=True,
            row_factory=dict_row,
            options="-c statement_timeout=5000",
        )
    except Exception as error:
        # Do not include a driver message: it can contain the Service hostname,
        # a username, or a rendered connection string.
        raise BasicPitchKedaBurstPostgresqlInfrastructureError(
            "Burst PostgreSQL connection is unavailable."
        ) from error
    return cast(DatabaseConnection, connection)


def open_minio_client(settings: BurstSettings) -> S3Client:
    """Create a private, path-style S3 client with no ambient AWS credentials.

    MinIO receives the dedicated access/secret key explicitly. ``region_name``
    is the S3 protocol's required signing-region value, not an AWS resource or
    cloud deployment setting. Path-style addressing matches the in-cluster
    MinIO Service endpoint instead of manufacturing per-bucket hostnames.
    """

    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise BasicPitchKedaBurstMinioInfrastructureError(
            "Pinned S3 burst-client dependency is unavailable."
        ) from error
    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint_url,
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            region_name="us-east-1",
            config=Config(
                connect_timeout=5,
                read_timeout=15,
                retries={"max_attempts": 2, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )
    except Exception as error:
        raise BasicPitchKedaBurstMinioInfrastructureError("Burst MinIO client is unavailable.") from error
    return cast(S3Client, client)


def main() -> int:
    """Run once as the later Kubernetes Job PID 1 and return a safe exit code."""

    connection: DatabaseConnection | None = None
    try:
        settings = BurstSettings.from_environment()
        connection = open_database_connection(settings)
        storage = MinioBurstAdapter(open_minio_client(settings))
        database = PostgresqlBurstAdapter(connection)
        run_burst(database, storage)
        return 0
    except (
        BasicPitchKedaBurstConfigurationError,
        BasicPitchKedaBurstContractError,
        BasicPitchKedaBurstMinioInfrastructureError,
        BasicPitchKedaBurstPostgresqlInfrastructureError,
    ) as error:
        # Every expected boundary supplies a pre-reviewed message. Never print
        # exception chains, settings, tokens, MinIO keys, passwords, DSNs, SQL,
        # endpoints, raw object data, or an SDK response in a Job log.
        print(f"Basic Pitch KEDA burst smoke failed: {error}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.close()  # type: ignore[attr-defined]


if __name__ == "__main__":
    raise SystemExit(main())
