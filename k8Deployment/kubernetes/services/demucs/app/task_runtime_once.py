"""Execute one already-acknowledged Demucs task through its bounded local path.

This module is the first outer composition for one *existing* manual-ack result.
It does not receive an AMQP frame, open a RabbitMQ connection, loop, sleep,
retry, recover due work, or make a broker acknowledgement decision. Its only
valid happy-path ordering is:

``acknowledged lease -> source preflight -> guarded running -> CPU separation
-> exact stems -> hashes -> private plans -> uploads -> guarded DB completion``

Every context is nested so temporary source/output directories are removed when
any later boundary raises. PostgreSQL locks are opened only by short
start/renewal/completion adapters; they are never held across MinIO, FFprobe,
CPU, or file work. A later worker supervisor will classify exceptions and
decide retry, lease recovery, process lifetime, and the next AMQP receive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

from app.acknowledged_lease_preflight import opened_acknowledged_demucs_source_workspace
from app.amqp_manual_ack import DemucsConsumeOneResult
from app.complete_stem_upload_commit import upload_and_commit_demucs_stem_set
from app.demucs_artifact_upload import DemucsPutObjectClient
from app.demucs_process import DemucsProcessRunner
from app.demucs_task_completion import CommittedDemucsStemSet
from app.ffprobe_process import DemucsFFprobeRunner
from app.running_source_workspace import opened_running_demucs_source_workspace
from app.source_preflight import DemucsSourcePreflightClient
from app.stem_output_plan_workspace import opened_demucs_stem_output_plan_workspace
from app.validated_stem_inventory_workspace import opened_validated_demucs_stem_inventory_workspace
from app.hashed_stem_inventory_workspace import opened_hashed_demucs_stem_inventory_workspace
from app.executed_separation_workspace import opened_executed_demucs_separation_workspace
from app.planned_stem_upload import DemucsPlannedStemUploader
from app.preflight_task_start import DemucsTaskStartDatabase


def _runtime_dependencies_or_raise(
    *,
    source_client: object,
    artifact_client: object,
    database: object,
    event_id_factory: object,
) -> None:
    """Reject a missing durable/storage capability before source/model work.

    This is intentionally structural: concrete Boto3 and Psycopg adapters do
    not share a useful runtime base class. Checking their narrow methods here
    avoids spending CPU or writing a private object when this task could never
    complete its required durable transaction.
    """

    if not (
        callable(getattr(source_client, "head_object", None))
        and callable(getattr(source_client, "get_object", None))
    ):
        raise TypeError("source_client must provide head_object and get_object.")
    if not callable(getattr(artifact_client, "put_object", None)):
        raise TypeError("artifact_client must provide put_object.")
    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    if not callable(event_id_factory):
        raise TypeError("event_id_factory must be callable.")


def execute_acknowledged_demucs_task_once(
    *,
    receive_result: DemucsConsumeOneResult,
    source_client: DemucsSourcePreflightClient,
    artifact_client: DemucsPutObjectClient,
    database: DemucsTaskStartDatabase,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
    demucs_runner: DemucsProcessRunner | None = None,
    uploader: DemucsPlannedStemUploader | None = None,
    event_id_factory: Callable[[], UUID] = uuid4,
) -> CommittedDemucsStemSet | None:
    """Run one previously acknowledged task, returning durable completion or loss.

    An `ACKNOWLEDGED_LEASE` is the only receive outcome permitted through the
    first nested context. If PostgreSQL rejects the later ``leased -> running``
    guard, this returns ``None`` before a model command. If the final guarded
    completion loses the lease, it also returns ``None`` after private uploads;
    both outcomes deliberately expose no broker publication. All other safe
    exceptions propagate to a future supervisor, whose retry policy must use
    lease-token-guarded task state rather than this function inventing a result.
    """

    _runtime_dependencies_or_raise(
        source_client=source_client,
        artifact_client=artifact_client,
        database=database,
        event_id_factory=event_id_factory,
    )
    # Each `with` statement adds one narrow capability only after its prior
    # predecessor succeeded. Unwinding reverses this order, deleting private
    # temporary output/source data even when any model, MinIO, or DB boundary
    # raises. No context owns a database transaction across external work.
    with opened_acknowledged_demucs_source_workspace(
        receive_result,
        source_client,
        work_directory=work_directory,
        ffprobe_runner=ffprobe_runner,
    ) as acknowledged_workspace:
        with opened_running_demucs_source_workspace(
            database=database,
            workspace=acknowledged_workspace,
        ) as running_workspace:
            if running_workspace is None:
                return None
            with opened_executed_demucs_separation_workspace(
                running_workspace,
                work_directory=work_directory,
                runner=demucs_runner,
                # The execution workspace owns the actual model child, so it
                # is the only nested boundary that can safely renew the active
                # lease and stop that child before reporting ownership loss.
                # It opens independent short transactions; no database lock is
                # held across source, CPU, or artifact work.
                renewal_database=database,
            ) as executed_workspace:
                with opened_validated_demucs_stem_inventory_workspace(
                    executed_workspace,
                ) as validated_workspace:
                    with opened_hashed_demucs_stem_inventory_workspace(
                        validated_workspace,
                    ) as hashed_workspace:
                        with opened_demucs_stem_output_plan_workspace(
                            hashed_workspace,
                        ) as plan_workspace:
                            return upload_and_commit_demucs_stem_set(
                                workspace=plan_workspace,
                                client=artifact_client,
                                database=database,
                                uploader=uploader,
                                event_id_factory=event_id_factory,
                            )
