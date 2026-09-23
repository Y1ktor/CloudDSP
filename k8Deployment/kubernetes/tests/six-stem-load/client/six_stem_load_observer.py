"""Orchestrate authoritative PostgreSQL, MinIO, RabbitMQ, and KEDA evidence.

This process runs in its own container. It has no test-user or PostgreSQL
administrator password: it receives a marker-scoped SQL function login, an
exact-object read-only MinIO key, RabbitMQ's monitoring credential, and a
projected read-only Kubernetes token. It writes the intermediate PostgreSQL
snapshot and the final success/failure file consumed by the lifecycle broker.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time

from keda_scale_observer import (
    KEDAScaleObserver,
    KEDAScaleObserverError,
    KEDAScaleSnapshot,
    KubernetesReadOnlyClient,
)
from lifecycle_handoff import (
    HandoffError,
    broker_failure_observed,
    read_postgresql_observer_credentials,
)
from load_test_terminal_outcome import (
    LoadTestTerminalOutcome,
    write_load_test_terminal_outcome,
)
from minio_artifact_observer import (
    MinIOArtifactObserver,
    MinIOArtifactObserverError,
    MinIOArtifactVerification,
)
from minio_observer_bootstrap import (
    MinIOObserverBootstrapError,
    read_minio_observer_credentials,
)
from observer_start_gate import (
    ObserverStartGateError,
    wait_for_observer_start_request,
    write_observer_ready,
)
from postgresql_durable_state_observer import (
    PostgreSQLDurableStateObserverError,
    PostgreSQLDurableStateSnapshot,
    PostgreSQLDurableStateObserver,
)
from postgresql_observer_report import (
    PostgreSQLObserverReport,
    write_postgresql_observer_report,
)
from rabbitmq_queue_observer import (
    RabbitMQQueueObserver,
    RabbitMQQueueObserverError,
    RabbitMQQueueSnapshot,
)


_POLL_SECONDS = 2.0
_DATABASE_POLL_SECONDS = 5.0
_QUEUE_POLL_SECONDS = 5.0
_STARTUP_WAIT_SECONDS = 10 * 60
_RUN_WAIT_SECONDS = 45 * 60
_QUEUE_MAIN_NAMES = {
    "demucs": "clouddsp.demucs.requests",
    "basic-pitch": "clouddsp.basic-pitch.requests",
    "adtof": "clouddsp.adtof.requests",
}


class SixStemLoadObserverError(RuntimeError):
    """A safe observer exit category without credentials or workload details."""


class _ScaleEvidence:
    """Accumulate peak replicas and activity without persisting object IDs."""

    def __init__(self) -> None:
        self.peaks = {"demucs": 0, "basic-pitch": 0, "adtof": 0}

    def add(self, snapshot: KEDAScaleSnapshot) -> None:
        for item in snapshot.workers:
            self.peaks[item.stage] = max(
                self.peaks[item.stage],
                item.replicas,
                item.ready_replicas,
                item.hpa_current_replicas,
                item.hpa_desired_replicas,
            )
            if self.peaks[item.stage] > item.maximum_replicas:
                raise KEDAScaleObserverError("KEDA observed replicas above the reviewed cap")


def run_observer(
    *,
    credential_directory: Path,
    minio_credential_directory: Path,
    report_directory: Path,
    rabbitmq_username: str,
    rabbitmq_password: str,
    keda_observer: KEDAScaleObserver | None = None,
    maximum_seconds: float = _RUN_WAIT_SECONDS,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> bool:
    """Run preflight, continuously sample, then publish one terminal decision."""

    if maximum_seconds <= 0:
        raise ValueError("maximum_seconds must be positive")
    run_marker: str | None = None
    postgresql_report_written = False
    terminal_written = False
    readiness_written = False
    evidence = _ScaleEvidence()
    database_observer: PostgreSQLDurableStateObserver | None = None
    minio_observer: MinIOArtifactObserver | None = None
    rabbit_observer = RabbitMQQueueObserver(
        username=rabbitmq_username,
        password=rabbitmq_password,
    )
    keda = keda_observer or KEDAScaleObserver(KubernetesReadOnlyClient())
    try:
        run_marker = wait_for_observer_start_request(
            report_directory, timeout_seconds=_STARTUP_WAIT_SECONDS
        )
        if broker_failure_observed(report_directory):
            raise SixStemLoadObserverError("lifecycle broker ended during observer startup")
        print("Six-stem load observer: checking idle load-test baseline")
        baseline_queue = rabbit_observer.observe_once()
        if not baseline_queue.is_drained:
            write_observer_ready(report_directory, run_marker=run_marker, ready=False)
            readiness_written = True
            _write_terminal(report_directory, run_marker, succeeded=False)
            terminal_written = True
            return False
        # Workers may still be in KEDA's normal cooldown from a prior test. Wait
        # up to the outer Job budget for scale-to-zero before releasing traffic.
        startup_deadline = monotonic() + _STARTUP_WAIT_SECONDS
        while monotonic() < startup_deadline:
            if broker_failure_observed(report_directory):
                raise SixStemLoadObserverError("lifecycle broker ended during baseline wait")
            state = keda.observe_once()
            evidence.add(state)
            if state.is_idle:
                write_observer_ready(report_directory, run_marker=run_marker, ready=True)
                readiness_written = True
                break
            sleep(_POLL_SECONDS)
        else:
            write_observer_ready(report_directory, run_marker=run_marker, ready=False)
            readiness_written = True
            _write_terminal(report_directory, run_marker, succeeded=False)
            terminal_written = True
            return False

        print("Six-stem load observer: baseline is empty and workers are at zero")
        run_deadline = monotonic() + maximum_seconds
        next_database_read = 0.0
        next_queue_read = 0.0
        latest_database: PostgreSQLDurableStateSnapshot | None = None
        latest_queues: RabbitMQQueueSnapshot | None = baseline_queue
        last_verification: MinIOArtifactVerification | None = None
        artifacts_verified = False

        while monotonic() < run_deadline:
            if broker_failure_observed(report_directory):
                raise SixStemLoadObserverError("lifecycle broker ended during observation")
            # Sample KEDA at two-second cadence so short autoscaling bursts are
            # visible even when the durable work completes quickly.
            scale_snapshot = keda.observe_once()
            evidence.add(scale_snapshot)

            now = monotonic()
            if now >= next_queue_read:
                latest_queues = rabbit_observer.observe_once()
                if any(
                    queue.queue_name.endswith(".dlq") and queue.messages > 0
                    for queue in latest_queues.queues
                ):
                    if not postgresql_report_written:
                        _publish_postgresql_snapshot_if_needed(
                            report_directory, run_marker, latest_database
                        )
                        postgresql_report_written = True
                    _write_terminal(report_directory, run_marker, succeeded=False)
                    terminal_written = True
                    return False
                next_queue_read = now + _QUEUE_POLL_SECONDS

            if now >= next_database_read:
                if database_observer is None:
                    try:
                        credentials = read_postgresql_observer_credentials(credential_directory)
                        minio_credentials = read_minio_observer_credentials(
                            minio_credential_directory,
                            expected_run_marker=run_marker,
                        )
                        if credentials.run_marker != run_marker:
                            raise SixStemLoadObserverError("observer credentials belonged to another run")
                        database_observer = PostgreSQLDurableStateObserver(credentials=credentials)
                        minio_observer = MinIOArtifactObserver(credentials=minio_credentials)
                    except (
                        FileNotFoundError,
                        OSError,
                        ValueError,
                        HandoffError,
                        MinIOObserverBootstrapError,
                    ):
                        # The broker only writes these after the authenticated
                        # client submits all three Jobs and proves their owner.
                        pass
                if database_observer is not None:
                    try:
                        latest_database = database_observer.observe_once()
                    except PostgreSQLDurableStateObserverError:
                        # A missing not-yet-created row, connection retry, or
                        # strict partial manifest is retried until the bounded
                        # run deadline; the terminal outcome remains fail-closed.
                        pass
                    else:
                        if not postgresql_report_written:
                            write_postgresql_observer_report(
                                report_directory,
                                report=PostgreSQLObserverReport(
                                    run_marker=run_marker,
                                    status="observed",
                                    snapshot=latest_database,
                                ),
                            )
                            postgresql_report_written = True
                            print("Six-stem load observer: PostgreSQL evidence is available")
                        if _has_terminal_failure(latest_database):
                            _write_terminal(report_directory, run_marker, succeeded=False)
                            terminal_written = True
                            return False

                        if _database_pipeline_succeeded(latest_database) and not artifacts_verified:
                            if minio_observer is None:
                                raise SixStemLoadObserverError("MinIO observer was not initialized")
                            last_verification = minio_observer.verify_all(snapshot=latest_database)
                            artifacts_verified = True
                            print("Six-stem load observer: all source and derived objects passed hash checks")
                if artifacts_verified and latest_queues is not None and last_verification is not None:
                    # Once all durable tasks and object hashes pass, continue
                    # sampling queue/KEDA state through cooldown; do not fetch
                    # the same 42 objects on every subsequent poll.
                    final_queues = latest_queues
                    if now >= next_queue_read:
                        final_queues = rabbit_observer.observe_once()
                        latest_queues = final_queues
                        next_queue_read = now + _QUEUE_POLL_SECONDS
                    if (
                        last_verification.object_count == 42
                        and final_queues.is_drained
                        and _all_dlqs_empty(final_queues)
                        and _has_worker_scale_activity(evidence)
                        and scale_snapshot.is_idle
                    ):
                        _log_final_evidence(last_verification, final_queues, evidence)
                        _write_terminal(report_directory, run_marker, succeeded=True)
                        terminal_written = True
                        return True
                next_database_read = now + _DATABASE_POLL_SECONDS
            sleep(_POLL_SECONDS)

        if run_marker is not None:
            if not readiness_written:
                try:
                    write_observer_ready(report_directory, run_marker=run_marker, ready=False)
                except Exception:
                    pass
            if not postgresql_report_written:
                _publish_postgresql_snapshot_if_needed(
                    report_directory, run_marker, latest_database
                )
                postgresql_report_written = True
            _write_terminal(report_directory, run_marker, succeeded=False)
            terminal_written = True
        return False
    except Exception:
        # Container logs intentionally contain only one stable category. If
        # the broker already waits for a report, wake it with a safe failure
        # record so its finally block revokes temporary Keycloak/DB/S3 users.
        if run_marker is not None:
            if not readiness_written:
                # A queue API or KEDA read may fail before baseline readiness.
                # Publish a negative result so the broker's startup wait ends
                # now, rather than leaving the Job alive for ten minutes.
                try:
                    write_observer_ready(report_directory, run_marker=run_marker, ready=False)
                except Exception:
                    pass
            if not postgresql_report_written:
                try:
                    write_postgresql_observer_report(
                        report_directory,
                        report=PostgreSQLObserverReport(
                            run_marker=run_marker,
                            status="failed",
                            snapshot=None,
                            failure_code="postgresql_observation_failed",
                        ),
                    )
                except Exception:
                    pass
            if not terminal_written:
                try:
                    _write_terminal(report_directory, run_marker, succeeded=False)
                except Exception:
                    pass
        raise SixStemLoadObserverError("six-stem load observation failed") from None


def _database_pipeline_succeeded(snapshot: PostgreSQLDurableStateSnapshot) -> bool:
    """Require the exact three-job, 3/15/3 success shape and 24 published events."""

    if (
        snapshot.observed_job_count != 3
        or snapshot.source_uploaded_count != 3
        or dict(snapshot.job_status_counts) != {"completed": 3}
        or snapshot.demucs_succeeded_count != 3
        or snapshot.basic_pitch_succeeded_count != 15
        or snapshot.adtof_succeeded_count != 3
        or snapshot.task_failure_count != 0
        or snapshot.active_task_lease_count != 0
        or dict(snapshot.task_status_counts) != {
            "demucs:succeeded": 3,
            "basic-pitch:succeeded": 15,
            "adtof:succeeded": 3,
        }
    ):
        return False
    expected_outbox = {
        "demucs:demucs.requested:published": 3,
        "basic-pitch:basic-pitch.requested:published": 15,
        "adtof:adtof.requested:published": 3,
    }
    if dict(snapshot.outbox_delivery_counts) != expected_outbox:
        return False
    return all(
        len(job.stems) == 6 and len(job.midi_keys) == 2
        for job in snapshot.artifact_evidence
    ) and len(snapshot.artifact_evidence) == 3


def _has_terminal_failure(snapshot: PostgreSQLDurableStateSnapshot) -> bool:
    """Stop on durable terminal failures without treating normal pending work as failure."""

    return (
        dict(snapshot.job_status_counts).get("failed", 0) > 0
        or snapshot.task_failure_count > 0
        or any(status.endswith(":failed") and count > 0 for status, count in snapshot.task_status_counts)
        or any(status.endswith(":dead_lettered") and count > 0 for status, count in snapshot.outbox_delivery_counts)
    )


def _all_dlqs_empty(snapshot: RabbitMQQueueSnapshot) -> bool:
    """Require all fixed worker dead-letter queues to remain empty."""

    return all(
        queue.messages == 0
        for queue in snapshot.queues
        if queue.queue_name.endswith(".dlq")
    )


def _has_worker_scale_activity(evidence: _ScaleEvidence) -> bool:
    """Require KEDA to have actually raised each worker stage above zero."""

    return all(evidence.peaks[stage] >= 1 for stage in _QUEUE_MAIN_NAMES)


def _publish_postgresql_snapshot_if_needed(
    report_directory: Path,
    run_marker: str,
    snapshot: PostgreSQLDurableStateSnapshot | None,
) -> None:
    """Wake the broker's first report waiter if DB observation was possible."""

    if snapshot is not None:
        write_postgresql_observer_report(
            report_directory,
            report=PostgreSQLObserverReport(
                run_marker=run_marker,
                status="observed",
                snapshot=snapshot,
            ),
        )
    else:
        write_postgresql_observer_report(
            report_directory,
            report=PostgreSQLObserverReport(
                run_marker=run_marker,
                status="failed",
                snapshot=None,
                failure_code="postgresql_observation_failed",
            ),
        )


def _write_terminal(directory: Path, run_marker: str, *, succeeded: bool) -> None:
    """Write exactly one constant-shape terminal result for the broker."""

    write_load_test_terminal_outcome(
        directory,
        outcome=LoadTestTerminalOutcome(
            run_marker=run_marker,
            status="succeeded" if succeeded else "failed",
            failure_code=None if succeeded else "load_test_validation_failed",
        ),
    )


def _log_final_evidence(
    verification: MinIOArtifactVerification,
    queues: RabbitMQQueueSnapshot,
    evidence: _ScaleEvidence,
) -> None:
    """Print only safe counts and peak replicas, never object keys or messages."""

    print(
        "Six-stem load observer: verified "
        f"{verification.object_count} MinIO objects "
        f"({verification.source_object_count} sources, {verification.stem_object_count} stems, "
        f"{verification.midi_object_count} MIDI/tempo outputs)"
    )
    print(
        "Six-stem load observer: RabbitMQ drained "
        f"{len(queues.queues)} main/retry/DLQ queues; "
        f"peak replicas Demucs={evidence.peaks['demucs']}, "
        f"Basic Pitch={evidence.peaks['basic-pitch']}, ADTOF={evidence.peaks['adtof']}"
    )


def main() -> int:
    """Read container-only paths and credentials, then run the observer once."""

    try:
        success = run_observer(
            credential_directory=Path(os.environ["SIX_STEM_LOAD_POSTGRESQL_OBSERVER_HANDOFF_DIR"]),
            minio_credential_directory=Path(os.environ["SIX_STEM_LOAD_MINIO_OBSERVER_HANDOFF_DIR"]),
            report_directory=Path(os.environ["SIX_STEM_LOAD_OBSERVER_REPORT_DIR"]),
            rabbitmq_username=os.environ["RABBITMQ_KEDA_SCALER_USERNAME"],
            rabbitmq_password=os.environ["RABBITMQ_KEDA_SCALER_PASSWORD"],
        )
    except (KeyError, OSError, SixStemLoadObserverError):
        print("Six-stem load observer failed", file=sys.stderr)
        return 1
    print(f"Six-stem load observer: terminal result {'succeeded' if success else 'failed'}")
    return 0 if success else 1


if __name__ == "__main__":  # pragma: no cover - entrypoint is the observer container.
    raise SystemExit(main())
