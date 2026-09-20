"""Narrow, injectable PostgreSQL function adapter for the KEDA burst smoke.

The future image supplies a hash-pinned Psycopg connection. This module only
uses a cursor-like protocol and names the three reviewed security-definer
functions. It deliberately contains no direct ``jobs``, ``outbox_events``, or
``processing_tasks`` SQL, keeping the test role's table denial meaningful.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from basic_pitch_keda_burst_smoke import (
    BURST_COORDINATES,
    CLEANUP_SQL,
    OBSERVE_SQL,
    PREPARE_SQL,
    BasicPitchKedaBurstContractError,
    ControlledWav,
    prepare_function_parameters,
)


class BasicPitchKedaBurstPostgresqlInfrastructureError(RuntimeError):
    """Hide PostgreSQL driver/endpoint detail from one-shot Job output."""


class DatabaseCursor(Protocol):
    """The dictionary-row cursor surface the three fixed functions require."""

    def execute(self, query: str, params: tuple[object, ...] = ()) -> object: ...

    def fetchall(self) -> list[Mapping[str, object]]: ...

    def __enter__(self) -> "DatabaseCursor": ...

    def __exit__(self, *args: object) -> object: ...


class DatabaseConnection(Protocol):
    """Connection protocol kept free of a concrete Psycopg import for tests."""

    def cursor(self) -> DatabaseCursor: ...


@dataclass(frozen=True)
class BurstTaskObservation:
    """The compact, fixed-coordinate row returned by ``observe()``."""

    request_label: str
    publication_status: str
    published_at: datetime | None
    task_id: str | None
    task_status: str | None
    task_attempt_count: int | None
    task_lease_is_clear: bool
    task_completed_at: datetime | None
    job_status: str


def _canonical_uuid(value: object, *, optional: bool = False) -> str | None:
    """Normalize a driver UUID/text value without accepting another spelling."""

    if value is None and optional:
        return None
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        raise BasicPitchKedaBurstContractError("Burst database returned an invalid UUID field.")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise BasicPitchKedaBurstContractError("Burst database returned an invalid UUID field.") from error
    if str(parsed) != value:
        raise BasicPitchKedaBurstContractError("Burst database returned a noncanonical UUID field.")
    return value


def _rows(cursor: DatabaseCursor, *, missing_message: str) -> list[Mapping[str, object]]:
    """Require dictionary-shaped function results rather than opaque driver tuples."""

    rows = cursor.fetchall()
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise BasicPitchKedaBurstContractError(missing_message)
    return rows


class PostgresqlBurstAdapter:
    """Call exactly prepare/observe/cleanup through the restricted test role."""

    def __init__(self, connection: DatabaseConnection) -> None:
        """Accept a later injected connection; no DSN or admin authority is accepted."""

        self._connection = connection

    def assert_coordinates_clean(self) -> None:
        """Reject retained durable evidence before the client uploads any WAV."""

        observations = self.read_observations()
        if observations:
            raise BasicPitchKedaBurstContractError("Burst database coordinates are not clean.")

    def prepare_durable_burst(self, wavs: tuple[ControlledWav, ...]) -> None:
        """Atomically request exactly three durable events using size/hash evidence only."""

        parameters = prepare_function_parameters(wavs)
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(PREPARE_SQL, parameters)
                rows = _rows(cursor, missing_message="Burst database prepare returned no fixed events.")
        except BasicPitchKedaBurstContractError:
            raise
        except Exception as error:
            raise BasicPitchKedaBurstPostgresqlInfrastructureError("Burst database prepare is unavailable.") from error
        expected = {(item.label, item.job_id, item.event_id) for item in BURST_COORDINATES}
        returned: set[tuple[str, str, str]] = set()
        for row in rows:
            label = row.get("request_label")
            job_id = _canonical_uuid(row.get("smoke_job_id"))
            event_id = _canonical_uuid(row.get("smoke_event_id"))
            if not isinstance(label, str):
                raise BasicPitchKedaBurstContractError("Burst database prepare returned invalid coordinates.")
            returned.add((label, job_id, event_id))
        if returned != expected or len(rows) != 3:
            raise BasicPitchKedaBurstContractError("Burst database prepare returned unexpected coordinates.")

    def read_observations(self) -> tuple[BurstTaskObservation, ...]:
        """Read only the restricted three-coordinate observation projection."""

        try:
            with self._connection.cursor() as cursor:
                cursor.execute(OBSERVE_SQL)
                rows = _rows(cursor, missing_message="Burst database observation is invalid.")
        except BasicPitchKedaBurstContractError:
            raise
        except Exception as error:
            raise BasicPitchKedaBurstPostgresqlInfrastructureError("Burst database observation is unavailable.") from error
        if not rows:
            return ()
        expected_labels = {coordinate.label for coordinate in BURST_COORDINATES}
        observations = tuple(_observation_from_row(row) for row in rows)
        if len(observations) != 3 or {item.request_label for item in observations} != expected_labels:
            raise BasicPitchKedaBurstContractError("Burst database observation has unexpected coordinates.")
        return tuple(sorted(observations, key=lambda item: item.request_label))

    def cleanup_successful_burst(self) -> None:
        """Ask the database to delete only a normal, fully completed burst."""

        try:
            with self._connection.cursor() as cursor:
                cursor.execute(CLEANUP_SQL)
                rows = _rows(cursor, missing_message="Burst database cleanup returned no result.")
        except BasicPitchKedaBurstContractError:
            raise
        except Exception as error:
            raise BasicPitchKedaBurstPostgresqlInfrastructureError("Burst database cleanup is unavailable.") from error
        if len(rows) != 1 or rows[0].get("cleaned") is not True:
            raise BasicPitchKedaBurstContractError("Burst database refused successful cleanup.")


def _observation_from_row(row: Mapping[str, object]) -> BurstTaskObservation:
    """Validate the compact database projection before it informs a success decision."""

    expected_names = {
        "request_label",
        "publication_status",
        "published_at",
        "task_id",
        "task_status",
        "task_attempt_count",
        "task_lease_is_clear",
        "task_completed_at",
        "job_status",
    }
    if set(row) != expected_names:
        raise BasicPitchKedaBurstContractError("Burst database observation has an incompatible shape.")
    request_label = row.get("request_label")
    publication_status = row.get("publication_status")
    job_status = row.get("job_status")
    task_status = row.get("task_status")
    task_attempt_count = row.get("task_attempt_count")
    if (
        not isinstance(request_label, str)
        or not isinstance(publication_status, str)
        or not isinstance(job_status, str)
        or (task_status is not None and not isinstance(task_status, str))
        or (task_attempt_count is not None and (type(task_attempt_count) is not int or task_attempt_count < 0))
        or type(row.get("task_lease_is_clear")) is not bool
    ):
        raise BasicPitchKedaBurstContractError("Burst database observation is invalid.")
    return BurstTaskObservation(
        request_label=request_label,
        publication_status=publication_status,
        published_at=row.get("published_at") if isinstance(row.get("published_at"), datetime) else None,
        task_id=_canonical_uuid(row.get("task_id"), optional=True),
        task_status=task_status,
        task_attempt_count=task_attempt_count,
        task_lease_is_clear=row["task_lease_is_clear"],
        task_completed_at=row.get("task_completed_at") if isinstance(row.get("task_completed_at"), datetime) else None,
        job_status=job_status,
    )
