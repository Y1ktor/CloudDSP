"""Assemble the future ADTOF worker-smoke facade from existing narrow components.

This source-only builder is deliberately not a runnable entrypoint. It validates
the pure state-machine timeout, creates the lazy PostgreSQL connection factory,
constructs one local Boto3 MinIO client, wires that client into the existing
input/output/cleanup adapters, and returns the one-step composition facade.

It does not open the returned PostgreSQL connection, call MinIO, sleep, poll,
run the facade, print a report, construct a broker client, build an image, or
create a Kubernetes Job. A later focused entrypoint task must own process
arguments, environment loading, monotonic time, bounded polling cadence, exit
status, and non-sensitive user-facing progress output.
"""

from __future__ import annotations

from adtof_worker_smoke_composition import ADTOFWorkerSmokeCompositionFacade
from adtof_worker_smoke_contract import (
    DEFAULT_OBSERVATION_TIMEOUT_SECONDS,
    ADTOFWorkerSmokeSettings,
)
from adtof_worker_smoke_database import FixedFunctionADTOFWorkerSmokeDatabaseAdapter
from adtof_worker_smoke_minio import create_boto3_adtof_worker_smoke_minio_client
from adtof_worker_smoke_minio_cleanup import SuccessfulOnlyADTOFWorkerSmokeMinIOCleanupAdapter
from adtof_worker_smoke_minio_input import FixedKeyADTOFWorkerSmokeMinIOInputAdapter
from adtof_worker_smoke_minio_outputs import FixedKeyADTOFWorkerSmokeMinIOOutputAdapter
from adtof_worker_smoke_orchestration import ADTOFWorkerSmokeStateMachine
from adtof_worker_smoke_postgresql import create_psycopg_adtof_worker_smoke_connection_factory


def build_adtof_worker_smoke_composition(
    settings: ADTOFWorkerSmokeSettings,
    *,
    observation_timeout_seconds: int = DEFAULT_OBSERVATION_TIMEOUT_SECONDS,
) -> ADTOFWorkerSmokeCompositionFacade:
    """Return a fully wired but not-yet-executing fixed-coordinate smoke facade.

    Validation happens before the S3 client is constructed: a bad polling
    deadline or PostgreSQL setting cannot cause even local SDK configuration.
    The PostgreSQL factory remains lazy after this function returns. Boto3
    client construction has no object request; all future network I/O starts
    only when an outer entrypoint invokes `facade.advance_one()`.
    """

    # `start()` is side-effect free and owns the contract's finite timeout
    # validation. The facade creates the real initial machine after every
    # factory has been safely assembled, so discard this preflight value.
    ADTOFWorkerSmokeStateMachine.start(
        observation_timeout_seconds=observation_timeout_seconds
    )
    database_connection_factory = create_psycopg_adtof_worker_smoke_connection_factory(settings)
    minio_client = create_boto3_adtof_worker_smoke_minio_client(settings)

    # One policy-restricted client is intentionally injected into three focused
    # wrappers. The wrappers, not this assembly code, prevent generic key/list
    # access and constrain their respective input/read/cleanup actions.
    return ADTOFWorkerSmokeCompositionFacade(
        input_boundary=FixedKeyADTOFWorkerSmokeMinIOInputAdapter(minio_client),
        database=FixedFunctionADTOFWorkerSmokeDatabaseAdapter(database_connection_factory),
        output_boundary=FixedKeyADTOFWorkerSmokeMinIOOutputAdapter(minio_client),
        object_cleanup=SuccessfulOnlyADTOFWorkerSmokeMinIOCleanupAdapter(minio_client),
        observation_timeout_seconds=observation_timeout_seconds,
    )
