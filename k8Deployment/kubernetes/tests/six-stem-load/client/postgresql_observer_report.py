"""Private file handoff for one PostgreSQL observer snapshot.

The broker and the durable-state observer will share a dedicated, bounded
memory-backed report volume. The authenticated load-client container must not
mount it, because a same-Pod client could otherwise forge the observer's
counts. The PostgreSQL credential volume remains separate and is mounted
read-only in the observer. The private report includes exact artifact keys
and integrity coordinates but no owner subject or credential. A report is
only one database observation—not a pipeline-success marker and never
authorization to delete evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import stat
import time
from typing import Final, Literal

from lifecycle_handoff import (
    HandoffError,
    _atomic_private_json_write,
    _read_private_json,
)
from postgresql_durable_state_observer import (
    PostgreSQLDurableStateObserverError,
    PostgreSQLDurableStateSnapshot,
    snapshot_from_aggregate_row,
)


POSTGRESQL_OBSERVER_REPORT_FILE_NAME: Final[str] = (
    "postgresql-durable-state-observer-report.json"
)
_SCHEMA_VERSION: Final[int] = 1
_RUN_MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]{8,24}$")
_VALID_STATUSES: Final[frozenset[str]] = frozenset({"observed", "failed"})
_SAFE_FAILURE_CODE: Final[str] = "postgresql_observation_failed"
_POLL_INTERVAL_SECONDS: Final[float] = 0.2


class PostgreSQLObserverReportError(RuntimeError):
    """A safe report-handoff failure without credentials or private coordinates."""


@dataclass(frozen=True)
class PostgreSQLObserverReport:
    """One observer attempt, kept distinct from the whole load-test outcome.

    ``observed`` means the aggregate function returned a valid snapshot; it
    does not mean that all workers, objects, queues, or KEDA cooldowns passed.
    ``failed`` means the observer could not make a trustworthy PostgreSQL
    observation. Its public failure code is intentionally fixed and contains
    no database diagnostics.
    """

    run_marker: str
    status: Literal["observed", "failed"]
    snapshot: PostgreSQLDurableStateSnapshot | None
    failure_code: str | None = None


def prepare_empty_postgresql_observer_report_directory(directory: Path) -> None:
    """Preflight the broker/observer-only memory volume before either starts.

    This is a distinct ``emptyDir`` from the load-client handoff and from the
    read-only observer credential volume. Reject stale data before provisioning
    the run so a previous report cannot be mistaken for fresh evidence.
    """

    try:
        metadata = directory.lstat()
    except FileNotFoundError as error:
        raise PostgreSQLObserverReportError(
            "PostgreSQL observer report directory was absent"
        ) from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise PostgreSQLObserverReportError(
            "PostgreSQL observer report path was not a real directory"
        )
    if metadata.st_mode & stat.S_IWOTH:
        raise PostgreSQLObserverReportError(
            "PostgreSQL observer report directory allowed other-user writes"
        )
    for path in (
        directory / POSTGRESQL_OBSERVER_REPORT_FILE_NAME,
        directory / f".{POSTGRESQL_OBSERVER_REPORT_FILE_NAME}.tmp",
    ):
        if path.exists() or path.is_symlink():
            raise PostgreSQLObserverReportError(
                "PostgreSQL observer report directory contained stale data"
            )


def write_postgresql_observer_report(
    directory: Path, *, report: PostgreSQLObserverReport
) -> Path:
    """Atomically publish one fixed-shape aggregate or safe failure record.

    The observer writes this file only after its read attempt finishes. Mode
    ``0600`` and atomic rename prevent partial reads; the dedicated report
    volume keeps the authenticated load client from writing a fake result.
    """

    _validate_report(report)
    destination = directory / POSTGRESQL_OBSERVER_REPORT_FILE_NAME
    if destination.exists() or destination.is_symlink():
        raise PostgreSQLObserverReportError(
            "PostgreSQL observer report already existed"
        )
    _atomic_private_json_write(destination, _report_payload(report))
    return destination


def read_postgresql_observer_report(
    directory: Path, *, expected_run_marker: str
) -> PostgreSQLObserverReport:
    """Read one report and bind it to the broker's current run marker."""

    _validate_run_marker(expected_run_marker)
    try:
        payload = _read_private_json(
            directory / POSTGRESQL_OBSERVER_REPORT_FILE_NAME,
            purpose="PostgreSQL observer report",
        )
        if set(payload) != {
            "schema_version",
            "run_marker",
            "status",
            "snapshot",
            "failure_code",
        }:
            raise PostgreSQLObserverReportError(
                "PostgreSQL observer report schema was invalid"
            )
        version = payload.get("schema_version")
        if type(version) is not int or version != _SCHEMA_VERSION:
            raise PostgreSQLObserverReportError(
                "PostgreSQL observer report version was invalid"
            )
        if payload.get("run_marker") != expected_run_marker:
            raise PostgreSQLObserverReportError(
                "PostgreSQL observer report belonged to another run"
            )
        status = payload.get("status")
        if status not in _VALID_STATUSES:
            raise PostgreSQLObserverReportError(
                "PostgreSQL observer report status was invalid"
            )

        snapshot_payload = payload.get("snapshot")
        failure_code = payload.get("failure_code")
        if status == "observed":
            if not isinstance(snapshot_payload, dict) or failure_code is not None:
                raise PostgreSQLObserverReportError(
                    "PostgreSQL observer snapshot report shape was invalid"
                )
            snapshot = _snapshot_from_payload(
                snapshot_payload, run_marker=expected_run_marker
            )
        else:
            if snapshot_payload is not None or failure_code != _SAFE_FAILURE_CODE:
                raise PostgreSQLObserverReportError(
                    "PostgreSQL observer failure report shape was invalid"
                )
            snapshot = None

        report = PostgreSQLObserverReport(
            run_marker=expected_run_marker,
            status=status,  # type: ignore[arg-type] -- checked against the fixed literal set above.
            snapshot=snapshot,
            failure_code=failure_code,
        )
        _validate_report(report)
        return report
    except PostgreSQLObserverReportError:
        raise
    except (HandoffError, PostgreSQLDurableStateObserverError, TypeError, ValueError):
        # Underlying readers never include contents, but normalize their
        # details at this cross-container boundary as a defense in depth.
        raise PostgreSQLObserverReportError(
            "PostgreSQL observer report could not be validated"
        ) from None


def wait_for_postgresql_observer_report(
    directory: Path, *, expected_run_marker: str, timeout_seconds: float
) -> PostgreSQLObserverReport:
    """Wait for one atomically published observer report under a finite budget."""

    _validate_run_marker(expected_run_marker)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    report_path = directory / POSTGRESQL_OBSERVER_REPORT_FILE_NAME
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if report_path.exists() or report_path.is_symlink():
            return read_postgresql_observer_report(
                directory,
                expected_run_marker=expected_run_marker,
            )
        time.sleep(min(_POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
    raise PostgreSQLObserverReportError(
        "PostgreSQL observer did not publish a report before the deadline"
    )


def _validate_report(report: PostgreSQLObserverReport) -> None:
    """Keep both writer and reader pinned to one run and two report states."""

    if not isinstance(report, PostgreSQLObserverReport):
        raise PostgreSQLObserverReportError("PostgreSQL observer report was invalid")
    _validate_run_marker(report.run_marker)
    if report.status == "observed":
        if (
            not isinstance(report.snapshot, PostgreSQLDurableStateSnapshot)
            or report.failure_code is not None
        ):
            raise PostgreSQLObserverReportError(
                "PostgreSQL observer snapshot report was incomplete"
            )
        # Re-validate even typed in-memory snapshots before serializing them.
        _snapshot_from_payload(
            _snapshot_payload(report.snapshot), run_marker=report.run_marker
        )
    elif report.status == "failed":
        if report.snapshot is not None or report.failure_code != _SAFE_FAILURE_CODE:
            raise PostgreSQLObserverReportError(
                "PostgreSQL observer failure report was invalid"
            )
    else:
        raise PostgreSQLObserverReportError("PostgreSQL observer report status was invalid")


def _report_payload(report: PostgreSQLObserverReport) -> dict[str, object]:
    """Build a bounded private document without owner or credential values."""

    return {
        "schema_version": _SCHEMA_VERSION,
        "run_marker": report.run_marker,
        "status": report.status,
        "snapshot": (
            _snapshot_payload(report.snapshot)
            if report.snapshot is not None
            else None
        ),
        "failure_code": report.failure_code,
    }


def _snapshot_payload(snapshot: PostgreSQLDurableStateSnapshot) -> dict[str, object]:
    """Serialize counters and the exact private object manifest for observers."""

    return {
        "observed_job_count": snapshot.observed_job_count,
        "source_uploaded_count": snapshot.source_uploaded_count,
        "job_status_counts": dict(snapshot.job_status_counts),
        "demucs_succeeded_count": snapshot.demucs_succeeded_count,
        "basic_pitch_succeeded_count": snapshot.basic_pitch_succeeded_count,
        "adtof_succeeded_count": snapshot.adtof_succeeded_count,
        "task_failure_count": snapshot.task_failure_count,
        "active_task_lease_count": snapshot.active_task_lease_count,
        "task_status_counts": dict(snapshot.task_status_counts),
        "outbox_delivery_counts": dict(snapshot.outbox_delivery_counts),
        "artifact_evidence": [
            {
                "job_id": evidence.job_id,
                "source": {
                    "object_key": evidence.source.object_key,
                    "content_type": evidence.source.content_type,
                    "size_bytes": evidence.source.size_bytes,
                    "sha256": evidence.source.sha256,
                },
                "stems": {
                    stem_name: {
                        "object_key": artifact.object_key,
                        "content_type": artifact.content_type,
                        "size_bytes": artifact.size_bytes,
                        "sha256": artifact.sha256,
                    }
                    for stem_name, artifact in evidence.stems
                },
                "midi_keys": list(evidence.midi_keys),
            }
            for evidence in snapshot.artifact_evidence
        ],
    }


def _snapshot_from_payload(
    payload: dict[str, object], *, run_marker: str
) -> PostgreSQLDurableStateSnapshot:
    """Re-run database-row validation against serialized aggregate fields."""

    if set(payload) != {
        "observed_job_count",
        "source_uploaded_count",
        "job_status_counts",
        "demucs_succeeded_count",
        "basic_pitch_succeeded_count",
        "adtof_succeeded_count",
        "task_failure_count",
        "active_task_lease_count",
        "task_status_counts",
        "outbox_delivery_counts",
        "artifact_evidence",
    }:
        raise PostgreSQLObserverReportError(
            "PostgreSQL observer snapshot fields were invalid"
        )
    row = (
        payload["observed_job_count"],
        payload["source_uploaded_count"],
        payload["job_status_counts"],
        payload["demucs_succeeded_count"],
        payload["basic_pitch_succeeded_count"],
        payload["adtof_succeeded_count"],
        payload["task_failure_count"],
        payload["active_task_lease_count"],
        payload["task_status_counts"],
        payload["outbox_delivery_counts"],
        _aggregate_row_from_artifact_report(
            payload["artifact_evidence"], run_marker=run_marker
        ),
    )
    try:
        return snapshot_from_aggregate_row(row, run_marker=run_marker)
    except PostgreSQLDurableStateObserverError:
        raise PostgreSQLObserverReportError(
            "PostgreSQL observer snapshot values were invalid"
        ) from None


def _aggregate_row_from_artifact_report(
    value: object, *, run_marker: str
) -> object:
    """Reconstruct only the DB function's validated object evidence input.

    The public report stores a compact typed representation rather than raw
    PostgreSQL JSON. This helper maps it back through the same strict manifest
    validator used for the original aggregate row.
    """

    if not isinstance(value, list) or len(value) not in {0, 3}:
        raise PostgreSQLObserverReportError(
            "PostgreSQL observer artifact report was invalid"
        )
    rows: list[dict[str, object]] = []
    for ordinal, entry in enumerate(value, start=1):
        if not isinstance(entry, dict) or set(entry) != {
            "job_id", "source", "stems", "midi_keys",
        }:
            raise PostgreSQLObserverReportError(
                "PostgreSQL observer artifact report was invalid"
            )
        source = entry.get("source")
        stems = entry.get("stems")
        midi_keys = entry.get("midi_keys")
        if (
            not isinstance(source, dict)
            or set(source) != {"object_key", "content_type", "size_bytes", "sha256"}
            or not isinstance(stems, dict)
            or not isinstance(midi_keys, list)
            or len(midi_keys) not in {0, 2}
        ):
            raise PostgreSQLObserverReportError(
                "PostgreSQL observer artifact report was invalid"
            )
        job_id = entry.get("job_id")
        filename = f"six-stem-load-{run_marker}-{ordinal}.wav"
        rows.append({
            "job_id": job_id,
            "input_bucket": "clouddsp-uploads",
            "input_object_key": f"uploads/{job_id}/{filename}",
            "source_filename": filename,
            "source_content_type": source.get("content_type"),
            "source_size_bytes": source.get("size_bytes"),
            "source_sha256": source.get("sha256"),
            "load_ordinal": ordinal,
            "stem_mode": "6-stems",
            "stems": {
                name: {
                    "bucket": "clouddsp-uploads",
                    "object_key": artifact.get("object_key"),
                    "content_type": artifact.get("content_type"),
                    "size_bytes": artifact.get("size_bytes"),
                    "sha256": artifact.get("sha256"),
                }
                for name, artifact in stems.items()
                if isinstance(artifact, dict)
            },
            "midi": ({
                "drums": {
                    "status": "ready",
                    "extractor": "adtof",
                    "s3_key": midi_keys[0],
                    "bpm_key": midi_keys[1],
                }
            } if midi_keys else {}),
        })
    return rows


def _validate_run_marker(run_marker: object) -> None:
    """Reject malformed or caller-selected markers without echoing the value."""

    if not isinstance(run_marker, str) or not _RUN_MARKER_PATTERN.fullmatch(run_marker):
        raise PostgreSQLObserverReportError(
            "PostgreSQL observer run marker was invalid"
        )
