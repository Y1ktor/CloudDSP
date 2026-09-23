"""Read one bounded PostgreSQL snapshot and exact private object manifest.

This read-only adapter runs in the load Job's separate evidence-observer
container. It reads the broker-created credential document from the separate
observer-only memory volume, connects only to the reviewed in-cluster
PostgreSQL Service, and invokes the one zero-argument function minted for that
run. The observer cannot select application tables directly, choose Job IDs,
write a database row, clean up data, or broaden its own SQL scope. Its fixed
function returns only the verified run's deterministic object keys and hashes
needed by a separate read-only MinIO observer.

This module deliberately returns a neutral database snapshot instead of
declaring the whole load test successful. It includes no owner subject, source
URL, outbox payload, error text, or credential. MinIO byte integrity,
RabbitMQ queue state, and KEDA cooldown remain separate evidence for the
orchestrator to combine before temporary identities are revoked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Callable, Mapping, Sequence

from lifecycle_handoff import (
    TemporaryPostgreSQLObserverCredentials,
    read_postgresql_observer_credentials,
)


# These are service DNS coordinates inside this project's cluster, not user
# configuration. Pinning them prevents a typo or untrusted environment value
# from redirecting the observer's narrowly scoped credential to another host.
_POSTGRESQL_HOST = "clouddsp-postgresql.clouddsp-data.svc"
_POSTGRESQL_PORT = 5432
_POSTGRESQL_DATABASE = "clouddsp_job_api"
_CONNECT_TIMEOUT_SECONDS = 5
_APPLICATION_NAME = "clouddsp-six-stem-durable-state-observer"
_READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"
_AGGREGATE_COLUMNS = (
    "observed_job_count, source_uploaded_count, job_status_counts, "
    "demucs_succeeded_count, basic_pitch_succeeded_count, "
    "adtof_succeeded_count, task_failure_count, active_task_lease_count, "
    "task_status_counts, outbox_delivery_counts, artifact_evidence"
)
_RUN_MARKER_PATTERN = re.compile(r"^[a-z0-9]{8,24}$")
_PASSWORD_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_AGGREGATE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_MAX_DATABASE_COUNT = 2_147_483_647
_EXPECTED_RESULT_COLUMNS = 11
_EXPECTED_STEM_NAMES = ("bass", "drums", "guitar", "other", "piano", "vocals")


class PostgreSQLDurableStateObserverError(RuntimeError):
    """A safe observer failure category without credentials, IDs, or SQL text."""


@dataclass(frozen=True)
class PostgreSQLDurableStateSnapshot:
    """The function's privacy-preserving state for exactly one verified run.

    The function returns no owner subject, source URL, event payload, error
    text, or timestamp. Its separately represented private manifest contains
    only owner-verified Job IDs, deterministic object keys, and integrity
    metadata required by the exact-key MinIO reader. Status maps are converted
    to sorted tuples for deterministic later decisions.
    """

    observed_job_count: int
    source_uploaded_count: int
    job_status_counts: tuple[tuple[str, int], ...]
    demucs_succeeded_count: int
    basic_pitch_succeeded_count: int
    adtof_succeeded_count: int
    task_failure_count: int
    active_task_lease_count: int
    task_status_counts: tuple[tuple[str, int], ...]
    outbox_delivery_counts: tuple[tuple[str, int], ...]
    artifact_evidence: tuple["LoadJobArtifactEvidence", ...] = ()


@dataclass(frozen=True)
class LoadObjectEvidence:
    """One deterministic object key plus trusted metadata from durable state."""

    object_key: str
    content_type: str
    size_bytes: int | None
    sha256: str | None


@dataclass(frozen=True)
class LoadJobArtifactEvidence:
    """The source and whatever deterministic outputs exist for one Job so far."""

    job_id: str = field(repr=False)
    source: LoadObjectEvidence = field(repr=False)
    stems: tuple[tuple[str, LoadObjectEvidence], ...] = field(repr=False)
    midi_keys: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class PostgreSQLDurableStateObserver:
    """A single-use, read-only client for one broker-minted observer function."""

    credentials: TemporaryPostgreSQLObserverCredentials = field(repr=False)
    # Injection keeps unit tests fully offline; production lazily imports the
    # pinned Psycopg dependency only when this observer actually connects.
    connection_factory: Callable[..., object] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Reject hand-built credential values before importing a DB driver."""

        _validate_credentials(self.credentials)

    def observe_once(self) -> PostgreSQLDurableStateSnapshot:
        """Call the fixed no-argument observer function exactly once.

        The SQL identifier is accepted only after checking that the schema,
        role, and function names are derived from the opaque broker run marker.
        Read-only transaction mode is enabled at connection startup as a
        second guard behind the temporary role's lack of table/write grants.
        """

        try:
            connect = self.connection_factory or _psycopg_connect
            with connect(
                host=_POSTGRESQL_HOST,
                port=_POSTGRESQL_PORT,
                dbname=_POSTGRESQL_DATABASE,
                user=self.credentials.username,
                password=self.credentials.password,
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                application_name=_APPLICATION_NAME,
                options=_READ_ONLY_OPTIONS,
            ) as connection:
                query = (
                    f'SELECT {_AGGREGATE_COLUMNS} FROM "{self.credentials.function_schema}".'
                    f'"{self.credentials.function_name}"()'
                )
                cursor = connection.execute(query)
                rows = cursor.fetchall()
            return _snapshot_from_rows(rows, run_marker=self.credentials.run_marker)
        except PostgreSQLDurableStateObserverError:
            raise
        except Exception:
            # Driver diagnostics can contain SQL, endpoint, or credential
            # details. Keep only a stable category in the container log.
            raise PostgreSQLDurableStateObserverError(
                "PostgreSQL durable-state observation failed"
            ) from None


def observe_postgresql_durable_state_once(
    *, credential_directory: Path,
    connection_factory: Callable[..., object] | None = None,
) -> PostgreSQLDurableStateSnapshot:
    """Read the broker's private credential handoff and collect one snapshot.

    The Kubernetes Job mounts this directory read-only into the
    observer container. It must be a different ``emptyDir`` from the
    authenticated client handoff; the client must never receive this login.
    """

    try:
        credentials = read_postgresql_observer_credentials(credential_directory)
        observer = PostgreSQLDurableStateObserver(
            credentials=credentials,
            connection_factory=connection_factory,
        )
        return observer.observe_once()
    except PostgreSQLDurableStateObserverError:
        raise
    except Exception:
        # Handoff parsing errors are also safe to report only by category.
        raise PostgreSQLDurableStateObserverError(
            "PostgreSQL durable-state observer setup failed"
        ) from None


def _snapshot_from_rows(
    rows: object, *, run_marker: str
) -> PostgreSQLDurableStateSnapshot:
    """Validate the exact one-row function response before it leaves the adapter."""

    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or len(rows) != 1:
        raise PostgreSQLDurableStateObserverError(
            "PostgreSQL observer function did not return exactly one aggregate row"
        )
    return snapshot_from_aggregate_row(rows[0], run_marker=run_marker)


def snapshot_from_aggregate_row(
    row: object, *, run_marker: str | None = None
) -> PostgreSQLDurableStateSnapshot:
    """Validate one fixed eleven-column row from Psycopg or a private report.

    The separate report handoff serializes the observer's counters and exact
    private artifact manifest for the lifecycle broker. Reusing this validator
    keeps both boundaries subject to the same count, map, and object-key rules.
    """

    if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) != _EXPECTED_RESULT_COLUMNS:
        raise PostgreSQLDurableStateObserverError(
            "PostgreSQL observer aggregate shape was invalid"
        )

    counts = tuple(
        _count(row[index], purpose="aggregate count")
        for index in (0, 1, 3, 4, 5, 6, 7)
    )
    return PostgreSQLDurableStateSnapshot(
        observed_job_count=counts[0],
        source_uploaded_count=counts[1],
        job_status_counts=_status_counts(row[2], purpose="job status map"),
        demucs_succeeded_count=counts[2],
        basic_pitch_succeeded_count=counts[3],
        adtof_succeeded_count=counts[4],
        task_failure_count=counts[5],
        active_task_lease_count=counts[6],
        task_status_counts=_status_counts(row[8], purpose="task status map"),
        outbox_delivery_counts=_status_counts(row[9], purpose="outbox status map"),
        artifact_evidence=_artifact_evidence(row[10], run_marker=run_marker),
    )


def _artifact_evidence(
    value: object, *, run_marker: str | None
) -> tuple[LoadJobArtifactEvidence, ...]:
    """Validate only the exact three-job manifest returned by the SQL function.

    This controlled manifest is deliberately richer than the aggregate maps:
    MinIO verification needs each known object key and the durable SHA-256 for
    source audio and Demucs stems. The observer still cannot choose IDs or
    query application tables; the zero-argument SECURITY DEFINER function
    embeds the owner and three verified IDs before this JSON can be returned.
    """

    if not isinstance(value, list) or len(value) > 3:
        raise PostgreSQLDurableStateObserverError(
            "PostgreSQL observer artifact manifest shape was invalid"
        )
    output: list[LoadJobArtifactEvidence] = []
    seen_job_ids: set[str] = set()
    for ordinal, item in enumerate(value, start=1):
        if not isinstance(item, Mapping) or set(item) != {
            "job_id", "input_bucket", "input_object_key", "source_filename",
            "source_content_type", "source_size_bytes", "source_sha256",
            "load_ordinal", "stem_mode", "stems", "midi",
        }:
            raise PostgreSQLDurableStateObserverError(
                "PostgreSQL observer artifact manifest entry was invalid"
            )
        job_id = _canonical_uuid(item.get("job_id"), purpose="artifact Job identifier")
        expected_filename = f"six-stem-load-{run_marker}-{ordinal}.wav" if run_marker else None
        if (
            job_id in seen_job_ids
            or item.get("load_ordinal") != ordinal
            or item.get("input_bucket") != "clouddsp-uploads"
            or item.get("source_filename") != expected_filename
            or item.get("input_object_key") != f"uploads/{job_id}/{expected_filename}"
            or item.get("source_content_type") != "audio/wav"
            or item.get("stem_mode") != "6-stems"
        ):
            raise PostgreSQLDurableStateObserverError(
                "PostgreSQL observer artifact coordinates were invalid"
            )
        seen_job_ids.add(job_id)
        source_size = _count(item.get("source_size_bytes"), purpose="source size")
        source_sha = _sha256(item.get("source_sha256"), purpose="source checksum")
        if not 1 <= source_size <= 256 * 1024 * 1024:
            raise PostgreSQLDurableStateObserverError(
                "PostgreSQL observer source size was invalid"
            )
        stems_value = item.get("stems")
        if (
            not isinstance(stems_value, Mapping)
            or not set(stems_value).issubset(set(_EXPECTED_STEM_NAMES))
        ):
            raise PostgreSQLDurableStateObserverError(
                "PostgreSQL observer stem manifest was incomplete"
            )
        stems: list[tuple[str, LoadObjectEvidence]] = []
        for stem_name in _EXPECTED_STEM_NAMES:
            stem = stems_value.get(stem_name)
            if not isinstance(stem, Mapping) or set(stem) != {
                "bucket", "object_key", "content_type", "size_bytes", "sha256",
            }:
                raise PostgreSQLDurableStateObserverError(
                    "PostgreSQL observer stem entry was invalid"
                )
            stem_size = _count(stem.get("size_bytes"), purpose="stem size")
            stem_sha = _sha256(stem.get("sha256"), purpose="stem checksum")
            expected_key = f"stems/{job_id}/{stem_name}.wav"
            if (
                stem.get("bucket") != "clouddsp-uploads"
                or stem.get("object_key") != expected_key
                or stem.get("content_type") != "audio/wav"
                or stem_size < 1
            ):
                raise PostgreSQLDurableStateObserverError(
                    "PostgreSQL observer stem coordinates were invalid"
                )
            stems.append((stem_name, LoadObjectEvidence(
                object_key=expected_key,
                content_type="audio/wav",
                size_bytes=stem_size,
                sha256=stem_sha,
            )))
        midi = item.get("midi")
        if not isinstance(midi, Mapping) or not set(midi).issubset({"drums"}):
            raise PostgreSQLDurableStateObserverError("PostgreSQL observer MIDI result was invalid")
        drums = midi.get("drums")
        midi_keys: tuple[str, ...] = ()
        if drums is not None:
            if (
                not isinstance(drums, Mapping)
                or set(drums) != {"status", "extractor", "s3_key", "bpm_key"}
                or drums.get("status") != "ready"
                or drums.get("extractor") != "adtof"
                or drums.get("s3_key") != f"midi/{job_id}/drums.mid"
                or drums.get("bpm_key") != f"midi/{job_id}/drums_bpm.json"
            ):
                raise PostgreSQLDurableStateObserverError(
                    "PostgreSQL observer ADTOF artifact keys were invalid"
                )
            midi_keys = (str(drums["s3_key"]), str(drums["bpm_key"]))
        output.append(LoadJobArtifactEvidence(
            job_id=job_id,
            source=LoadObjectEvidence(
                object_key=f"uploads/{job_id}/{expected_filename}",
                content_type="audio/wav",
                size_bytes=source_size,
                sha256=source_sha,
            ),
            stems=tuple(stems),
            midi_keys=midi_keys,
        ))
    return tuple(output)


def _canonical_uuid(value: object, *, purpose: str) -> str:
    """Accept only canonical UUID text and hide received values in diagnostics."""

    from uuid import UUID

    if not isinstance(value, str):
        raise PostgreSQLDurableStateObserverError(
            f"PostgreSQL observer {purpose} was invalid"
        )
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError):
        raise PostgreSQLDurableStateObserverError(
            f"PostgreSQL observer {purpose} was invalid"
        ) from None
    if value != canonical:
        raise PostgreSQLDurableStateObserverError(
            f"PostgreSQL observer {purpose} was invalid"
        )
    return canonical


def _sha256(value: object, *, purpose: str) -> str:
    """Require one lowercase hexadecimal SHA-256 without returning the digest."""

    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise PostgreSQLDurableStateObserverError(
            f"PostgreSQL observer {purpose} was invalid"
        )
    return value


def _count(value: object, *, purpose: str) -> int:
    """Accept only bounded non-negative PostgreSQL integer counters."""

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_DATABASE_COUNT
    ):
        raise PostgreSQLDurableStateObserverError(f"PostgreSQL observer {purpose} was invalid")
    return value


def _status_counts(value: object, *, purpose: str) -> tuple[tuple[str, int], ...]:
    """Validate JSONB aggregate maps and normalize them without raw DB values."""

    if not isinstance(value, Mapping):
        raise PostgreSQLDurableStateObserverError(f"PostgreSQL observer {purpose} was invalid")
    normalized: list[tuple[str, int]] = []
    for key, count in value.items():
        if not isinstance(key, str) or not _AGGREGATE_KEY_PATTERN.fullmatch(key):
            raise PostgreSQLDurableStateObserverError(
                f"PostgreSQL observer {purpose} contained an invalid category"
            )
        normalized.append((key, _count(count, purpose=purpose)))
    return tuple(sorted(normalized))


def _validate_credentials(
    credentials: TemporaryPostgreSQLObserverCredentials,
) -> None:
    """Require the broker's fixed marker-derived identity/function tuple."""

    if (
        not isinstance(credentials, TemporaryPostgreSQLObserverCredentials)
        or not isinstance(credentials.run_marker, str)
        or not _RUN_MARKER_PATTERN.fullmatch(credentials.run_marker)
        or credentials.username != f"clouddsp_six_stem_observer_{credentials.run_marker}"
        or not isinstance(credentials.password, str)
        or not _PASSWORD_PATTERN.fullmatch(credentials.password)
        or credentials.function_schema != "public"
        or credentials.function_name
        != f"clouddsp_six_stem_observe_{credentials.run_marker}"
    ):
        raise PostgreSQLDurableStateObserverError(
            "PostgreSQL observer credential contract was invalid"
        )


def _psycopg_connect(**connection_options: object) -> object:
    """Load the image's hash-pinned PostgreSQL driver only at point of use."""

    try:
        import psycopg
    except ImportError as error:  # pragma: no cover - depends on runtime packaging.
        raise PostgreSQLDurableStateObserverError(
            "PostgreSQL observer driver was unavailable"
        ) from error
    return psycopg.connect(**connection_options)
