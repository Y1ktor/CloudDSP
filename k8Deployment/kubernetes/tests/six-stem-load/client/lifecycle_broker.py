"""Entrypoint for the capability lifecycle broker of the six-stem load Job.

The broker owns the Keycloak bootstrap-administrator Secret and must run in a
different container from the authenticated load client.  It creates a short-
lived direct-grant user/client pair, passes only that user's configuration via
``lifecycle_handoff``. It revokes the pair only after a separate combined
terminal outcome. Before any observer setup, it reuses that temporary user to
verify three private coordinates through the ordinary Job API. It then
creates and verifies one constrained PostgreSQL observer role/function in one
transaction, and a separate exact-object MinIO user, handing each generated
credential only to its observer-only memory volume. It waits for the observer
to verify the zero-queue/zero-worker baseline before creating any user.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import signal
import sys
import time
from typing import Callable, Protocol

from keycloak_identity_lifecycle import (
    IdentityLifecycleError,
    KeycloakLoadIdentityLifecycle,
    LoadTestIdentity,
)
from owner_bound_job_verifier import (
    OwnerBoundJobVerificationError,
    OwnerBoundJobVerificationSettings,
    VerifiedOwnerBoundLoadJobs,
    verify_owner_bound_load_jobs,
)
from observer_capability_contract import (
    VerifiedLoadObserverCapabilityContract,
    derive_verified_load_observer_capability_contract,
)
from lifecycle_handoff import (
    AuthenticatedLoadCoordinates,
    HandoffError,
    prepare_empty_handoff_directory,
    prepare_empty_postgresql_observer_credentials_directory,
    remove_handoff_files,
    remove_postgresql_observer_credentials,
    wait_for_authenticated_load_coordinates,
    wait_for_client_outcome,
    TemporaryPostgreSQLObserverCredentials,
    write_verified_authenticated_ingress,
    write_postgresql_observer_credentials,
    write_temporary_load_identity,
    write_broker_failure_marker,
)
from postgresql_admin_boundary import (
    PostgreSQLAdminBoundaryError,
    PostgreSQLAdminConnectionSettings,
)
from postgresql_observer_bootstrap import (
    PostgreSQLObserverBootstrap,
    PostgreSQLObserverBootstrapError,
)
from minio_observer_bootstrap import (
    MinIOAdminSettings,
    MinIOObserverBootstrap,
    MinIOObserverBootstrapError,
    TemporaryMinIOObserverCredentials,
    prepare_empty_minio_observer_credentials_directory,
    remove_minio_observer_credentials,
    write_minio_observer_credentials,
)
from observer_start_gate import (
    ObserverStartGateError,
    prepare_observer_start_gate,
    wait_for_observer_ready,
    write_observer_start_request,
)
from postgresql_observer_report import (
    PostgreSQLObserverReport,
    PostgreSQLObserverReportError,
    prepare_empty_postgresql_observer_report_directory,
    wait_for_postgresql_observer_report,
)
from load_test_terminal_outcome import (
    LoadTestTerminalOutcome,
    LoadTestTerminalOutcomeError,
    prepare_empty_load_test_terminal_outcome_directory,
    wait_for_load_test_terminal_outcome,
)


_DEFAULT_WAIT_SECONDS = 45 * 60


class LifecycleBrokerError(RuntimeError):
    """A safe terminal broker error that does not include local credential values."""


class LifecycleBroker(Protocol):
    """The Keycloak identity lifecycle surface needed by this broker."""

    def create(self, *, run_marker: str) -> LoadTestIdentity:
        """Provision one temporary Keycloak client/user pair."""

    def revoke(self, *, identity: LoadTestIdentity) -> None:
        """Revoke that exact temporary client/user pair."""


# Keeping this callback as a small pure boundary lets unit tests fake the
# normal owner-bound HTTP proof. The broker does not receive any database,
# object-store, AMQP, Kubernetes, or observer credential to accomplish it.
CoordinateVerifier = Callable[
    [LoadTestIdentity, AuthenticatedLoadCoordinates], VerifiedOwnerBoundLoadJobs
]


class CoordinateWaiter(Protocol):
    """Match the coordinate reader's keyword-only bounded-wait contract."""

    def __call__(
        self, directory: Path, *, timeout_seconds: float
    ) -> AuthenticatedLoadCoordinates:
        """Read the complete all-or-nothing coordinate handoff."""


class OutcomeWaiter(Protocol):
    """Match the offline lifecycle test's keyword-only timeout contract."""

    def __call__(self, directory: Path, *, timeout_seconds: float) -> str:
        """Wait for one fixed client outcome without exposing private content."""


class PostgreSQLObserverProvisioner(Protocol):
    """The broker-only PostgreSQL observer lifecycle surface."""

    def provision(
        self, *, capability: VerifiedLoadObserverCapabilityContract
    ) -> TemporaryPostgreSQLObserverCredentials:
        """Create and verify one marker-scoped observer transactionally."""

    def revoke(self, *, credentials: TemporaryPostgreSQLObserverCredentials) -> None:
        """Drop only that observer's function, grants, and temporary role."""


class MinIOObserverProvisioner(Protocol):
    """The broker-only exact-object MinIO user lifecycle surface."""

    def provision(
        self, *, capability: VerifiedLoadObserverCapabilityContract
    ) -> TemporaryMinIOObserverCredentials:
        """Create and verify one run-scoped read-only user."""

    def revoke(self, *, credentials: TemporaryMinIOObserverCredentials) -> None:
        """Remove only this run's user and policy after observation ends."""


def run_lifecycle_broker(
    *,
    lifecycle: LifecycleBroker,
    handoff_directory: Path,
    wait_seconds: float,
    coordinate_verifier: CoordinateVerifier,
    postgresql_observer_provisioner: PostgreSQLObserverProvisioner | None = None,
    observer_credential_directory: Path | None = None,
    observer_report_directory: Path | None = None,
    minio_observer_provisioner: MinIOObserverProvisioner | None = None,
    minio_observer_credential_directory: Path | None = None,
    run_marker: str | None = None,
    coordinate_waiter: CoordinateWaiter = (
        wait_for_authenticated_load_coordinates
    ),
    outcome_waiter: OutcomeWaiter = wait_for_client_outcome,
    postgresql_observer_report_waiter: Callable[..., PostgreSQLObserverReport] = (
        wait_for_postgresql_observer_report
    ),
    terminal_outcome_waiter: Callable[..., LoadTestTerminalOutcome] = (
        wait_for_load_test_terminal_outcome
    ),
    observer_ready_waiter: Callable[..., bool] = wait_for_observer_ready,
    observer_start_writer: Callable[..., None] = write_observer_start_request,
) -> str:
    """Run identity, authenticated-ingress, snapshot, and terminal-result phases.

    The broker waits for the authenticated client's all-or-nothing coordinate
    file, proves every claimed Job through the temporary user's normal API
    route, and derives the observer scope only from that in-memory proof. The
    production entrypoint supplies a PostgreSQL provisioner, credential
    directory, and report directory. It waits for a run-matched snapshot and
    then a distinct terminal report before revoking temporary identities.
    The legacy client-outcome callback remains only for offline lifecycle
    tests and is not the production success authority.
    The writable verification marker is never accepted as authority.
    """

    if wait_seconds <= 0:
        raise ValueError("wait_seconds must be positive")
    marker = run_marker or secrets.token_hex(8)
    identity: LoadTestIdentity | None = None
    observer_credentials: TemporaryPostgreSQLObserverCredentials | None = None
    minio_observer_credentials: TemporaryMinIOObserverCredentials | None = None
    handoff_directory_prepared = False
    observer_directory_prepared = False
    primary_error: BaseException | None = None
    outcome: str | None = None
    deadline = time.monotonic() + wait_seconds

    try:
        prepare_empty_handoff_directory(handoff_directory)
        handoff_directory_prepared = True
        if (
            (postgresql_observer_provisioner is None)
            != (observer_credential_directory is None)
            or (postgresql_observer_provisioner is None)
            != (observer_report_directory is None)
        ):
            raise LifecycleBrokerError("PostgreSQL observer bootstrap boundary was incomplete")
        if (
            (minio_observer_provisioner is None)
            != (minio_observer_credential_directory is None)
            or (
                minio_observer_provisioner is not None
                and postgresql_observer_provisioner is None
            )
        ):
            raise LifecycleBrokerError("MinIO observer bootstrap boundary was incomplete")
        if observer_credential_directory is not None:
            prepare_empty_postgresql_observer_credentials_directory(
                observer_credential_directory
            )
            observer_directory_prepared = True
        if observer_report_directory is not None:
            # Reject stale reports before creating a temporary identity or
            # PostgreSQL role; both contracts protect separate filenames.
            prepare_empty_postgresql_observer_report_directory(observer_report_directory)
            prepare_empty_load_test_terminal_outcome_directory(observer_report_directory)
        if minio_observer_credential_directory is not None:
            prepare_empty_minio_observer_credentials_directory(
                minio_observer_credential_directory
            )
            if observer_report_directory is None:
                raise LifecycleBrokerError("observer report directory was absent")
            # The observer checks that shared queues are empty and KEDA has
            # returned all three workloads to zero before the test begins.
            # This makes queue/scaling evidence attributable to this run.
            prepare_observer_start_gate(observer_report_directory)
            observer_start_writer(observer_report_directory, run_marker=marker)
            print("Six-stem load lifecycle broker: waiting for observer baseline readiness")
            if not observer_ready_waiter(
                observer_report_directory,
                run_marker=marker,
                timeout_seconds=min(600.0, _remaining_seconds(deadline)),
            ):
                raise LifecycleBrokerError("observer baseline was not ready")
            print("Six-stem load lifecycle broker: observer baseline is ready")
        print("Six-stem load lifecycle broker: provisioning disposable Keycloak identity")
        identity = lifecycle.create(run_marker=marker)
        write_temporary_load_identity(handoff_directory, identity)
        print("Six-stem load lifecycle broker: temporary user handoff is ready")
        # The production waiter deliberately makes its timeout keyword-only.
        # Keep that contract at this boundary: passing the budget positionally
        # raises TypeError immediately after the client finishes its uploads,
        # which used to revoke the temporary identity before any observer was
        # provisioned and made the load Job look like a processing failure.
        coordinates = coordinate_waiter(
            handoff_directory, timeout_seconds=_remaining_seconds(deadline)
        )
        print("Six-stem load lifecycle broker: authenticated ingress coordinates are ready")
        proof = coordinate_verifier(identity, coordinates)
        write_verified_authenticated_ingress(handoff_directory, coordinates=coordinates)
        print("Six-stem load lifecycle broker: authenticated ingress ownership is verified")
        if postgresql_observer_provisioner is not None:
            if not isinstance(proof, VerifiedOwnerBoundLoadJobs):
                raise LifecycleBrokerError("owner-bound verifier did not return its verified proof")
            if observer_credential_directory is None:
                raise LifecycleBrokerError("PostgreSQL observer credential directory was absent")
            capability = derive_verified_load_observer_capability_contract(proof=proof)
            print("Six-stem load lifecycle broker: creating restricted PostgreSQL observer")
            observer_credentials = postgresql_observer_provisioner.provision(
                capability=capability
            )
            # Keep the committed role's cleanup handle before writing the
            # password file. If the atomic handoff fails, finally still drops
            # the exact generated role and function.
            write_postgresql_observer_credentials(
                observer_credential_directory,
                credentials=observer_credentials,
            )
            if minio_observer_provisioner is not None:
                if minio_observer_credential_directory is None:
                    raise LifecycleBrokerError("MinIO observer credential directory was absent")
                print("Six-stem load lifecycle broker: creating exact-object MinIO observer")
                minio_observer_credentials = minio_observer_provisioner.provision(
                    capability=capability
                )
                write_minio_observer_credentials(
                    minio_observer_credential_directory,
                    credentials=minio_observer_credentials,
                )
                print("Six-stem load lifecycle broker: restricted data observers are ready")
        if postgresql_observer_provisioner is not None:
            if observer_report_directory is None:
                raise LifecycleBrokerError("observer report directory was absent")
            print("Six-stem load lifecycle broker: waiting for PostgreSQL snapshot report")
            snapshot_report = postgresql_observer_report_waiter(
                observer_report_directory,
                expected_run_marker=marker,
                timeout_seconds=_remaining_seconds(deadline),
            )
            if snapshot_report.status != "observed":
                raise LifecycleBrokerError(
                    "PostgreSQL observer did not produce a valid snapshot"
                )
            print(
                "Six-stem load lifecycle broker: PostgreSQL snapshot observed; run is not complete"
            )
            print("Six-stem load lifecycle broker: waiting for combined terminal outcome")
            terminal_report = terminal_outcome_waiter(
                observer_report_directory,
                expected_run_marker=marker,
                timeout_seconds=_remaining_seconds(deadline),
            )
            outcome = terminal_report.status
            print(f"Six-stem load lifecycle broker: combined terminal outcome is {outcome}")
        else:
            outcome = outcome_waiter(
                handoff_directory, timeout_seconds=_remaining_seconds(deadline)
            )
            print("Six-stem load lifecycle broker: offline client outcome callback completed")
    except BaseException as error:
        # Store only the exception object locally for control flow.  No error
        # text is printed here because a future library could include a URL,
        # JSON body, or header in an unexpected error message.
        primary_error = error
    finally:
        cleanup_error: BaseException | None = None
        if minio_observer_credentials is not None and minio_observer_provisioner is not None:
            try:
                print("Six-stem load lifecycle broker: revoking temporary MinIO observer")
                minio_observer_provisioner.revoke(credentials=minio_observer_credentials)
                print("Six-stem load lifecycle broker: MinIO observer revoked")
            except BaseException as error:
                cleanup_error = error
        if minio_observer_credential_directory is not None:
            try:
                remove_minio_observer_credentials(minio_observer_credential_directory)
            except BaseException as error:
                cleanup_error = cleanup_error or error
        if observer_credentials is not None and postgresql_observer_provisioner is not None:
            try:
                print("Six-stem load lifecycle broker: revoking temporary PostgreSQL observer")
                postgresql_observer_provisioner.revoke(credentials=observer_credentials)
                print("Six-stem load lifecycle broker: PostgreSQL observer revoked")
            except BaseException as error:
                cleanup_error = error
        if observer_credential_directory is not None and observer_directory_prepared:
            try:
                remove_postgresql_observer_credentials(observer_credential_directory)
            except BaseException as error:
                cleanup_error = cleanup_error or error
        if identity is not None:
            try:
                print("Six-stem load lifecycle broker: revoking disposable Keycloak identity")
                lifecycle.revoke(identity=identity)
                print("Six-stem load lifecycle broker: Keycloak identity revoked")
            except BaseException as error:
                cleanup_error = error
        if handoff_directory_prepared:
            try:
                remove_handoff_files(handoff_directory)
            except BaseException as error:
                cleanup_error = cleanup_error or error

        if cleanup_error is not None:
            raise LifecycleBrokerError("six-stem load lifecycle cleanup failed") from None
        if primary_error is not None:
            if isinstance(
                primary_error,
                (
                    HandoffError,
                    IdentityLifecycleError,
                    LifecycleBrokerError,
                    OwnerBoundJobVerificationError,
                    PostgreSQLAdminBoundaryError,
                    PostgreSQLObserverBootstrapError,
                    MinIOObserverBootstrapError,
                    PostgreSQLObserverReportError,
                    LoadTestTerminalOutcomeError,
                    ObserverStartGateError,
                ),
            ):
                raise LifecycleBrokerError("six-stem load lifecycle broker failed") from None
            # SIGTERM/KeyboardInterrupt still reaches this branch after the
            # identity revocation attempt.  Preserve process termination while
            # never printing a potentially sensitive exception string.
            raise primary_error

    if outcome is None:
        raise LifecycleBrokerError("six-stem load lifecycle produced no client outcome")
    return outcome


def _remaining_seconds(deadline: float) -> float:
    """Share one lifecycle deadline across coordinate and outcome phases.

    Giving each phase the full outer timeout would silently double the maximum
    lifetime of a Pod. A later observer phase must consume the same remaining
    budget rather than extending it through a new independent wait.
    """

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LifecycleBrokerError("six-stem load lifecycle deadline elapsed")
    return remaining


def _required_environment(name: str) -> str:
    """Load one required broker-only setting without including its value in an error."""

    value = os.environ.get(name)
    if not value:
        raise LifecycleBrokerError(f"required lifecycle broker setting {name} was absent")
    return value


def _wait_seconds_from_environment() -> int:
    """Parse one bounded lifecycle deadline so a stalled sidecar cannot live forever."""

    raw_value = os.environ.get("SIX_STEM_LOAD_LIFECYCLE_WAIT_SECONDS", str(_DEFAULT_WAIT_SECONDS))
    try:
        seconds = int(raw_value)
    except ValueError as error:
        raise LifecycleBrokerError("lifecycle broker wait setting was invalid") from error
    if seconds < 60 or seconds > 2 * 60 * 60:
        raise LifecycleBrokerError("lifecycle broker wait setting was outside 60-7200 seconds")
    return seconds


def _termination_requested(_signal_number: int, _frame: object) -> None:
    """Turn Kubernetes' catchable SIGTERM into normal broker cleanup control flow.

    Kubernetes sends SIGTERM before its termination grace period expires.  By
    raising a safe local exception, the broker's ``finally`` block revokes the
    disposable Keycloak user/client and removes the shared password handoff.
    SIGKILL remains uncatchable, so the later recovery procedure must still be
    able to find interrupted identities by their controlled prefix.
    """

    raise LifecycleBrokerError("lifecycle broker received a termination request")


def _wake_peers_after_broker_failure() -> None:
    """Signal both shared volumes after broker cleanup without exposing why.

    The client sees the marker in its identity handoff, while the observer sees
    the same fixed marker in its report volume. The two containers do not share
    each other's volume. A missing or unsafe mount must not prevent the broker
    from trying the other one or from exiting with its original failure.
    """

    for environment_name in (
        "SIX_STEM_LOAD_HANDOFF_DIR",
        "SIX_STEM_LOAD_OBSERVER_REPORT_DIR",
    ):
        raw_path = os.environ.get(environment_name)
        if not raw_path:
            continue
        try:
            write_broker_failure_marker(Path(raw_path))
        except (HandoffError, OSError, ValueError):
            pass


def main() -> int:
    """Run the broker using the future manifest's container-specific environment."""

    # Install the handler only in the actual container entrypoint.  Unit tests
    # call ``run_lifecycle_broker`` directly and must not alter the test
    # process's own signal behaviour.
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _termination_requested)
    try:
        lifecycle = KeycloakLoadIdentityLifecycle(
            keycloak_internal_base_url=_required_environment("KEYCLOAK_INTERNAL_BASE_URL"),
            administrator_realm=_required_environment("KEYCLOAK_ADMIN_REALM"),
            application_realm=_required_environment("CLOUDDSP_REALM"),
            job_api_client_id=_required_environment("JOB_API_CLIENT_ID"),
            bootstrap_admin_username=_required_environment("KC_BOOTSTRAP_ADMIN_USERNAME"),
            bootstrap_admin_password=_required_environment("KC_BOOTSTRAP_ADMIN_PASSWORD"),
        )
        verification_settings = OwnerBoundJobVerificationSettings(
            keycloak_internal_base_url=_required_environment("KEYCLOAK_INTERNAL_BASE_URL"),
            application_realm=_required_environment("CLOUDDSP_REALM"),
            job_api_internal_base_url=_required_environment("JOB_API_INTERNAL_BASE_URL"),
        )

        def verify_coordinates(
            identity: LoadTestIdentity, coordinates: AuthenticatedLoadCoordinates
        ) -> VerifiedOwnerBoundLoadJobs:
            """Use the temporary user—not the broker admin—to prove ownership."""

            return verify_owner_bound_load_jobs(
                settings=verification_settings,
                identity=identity,
                coordinates=coordinates,
            )

        postgresql_settings = PostgreSQLAdminConnectionSettings.from_environment()
        postgresql_observer_provisioner = PostgreSQLObserverBootstrap(
            settings=postgresql_settings
        )
        minio_observer_provisioner = MinIOObserverBootstrap(
            settings=MinIOAdminSettings(
                root_username=_required_environment("MINIO_ROOT_USER"),
                root_password=_required_environment("MINIO_ROOT_PASSWORD"),
                work_directory=Path(
                    _required_environment("SIX_STEM_LOAD_MINIO_ADMIN_WORK_DIR")
                ),
            )
        )
        outcome = run_lifecycle_broker(
            lifecycle=lifecycle,
            handoff_directory=Path(_required_environment("SIX_STEM_LOAD_HANDOFF_DIR")),
            wait_seconds=_wait_seconds_from_environment(),
            coordinate_verifier=verify_coordinates,
            postgresql_observer_provisioner=postgresql_observer_provisioner,
            observer_credential_directory=Path(
                _required_environment("SIX_STEM_LOAD_POSTGRESQL_OBSERVER_HANDOFF_DIR")
            ),
            observer_report_directory=Path(
                _required_environment("SIX_STEM_LOAD_OBSERVER_REPORT_DIR")
            ),
            minio_observer_provisioner=minio_observer_provisioner,
            minio_observer_credential_directory=Path(
                _required_environment("SIX_STEM_LOAD_MINIO_OBSERVER_HANDOFF_DIR")
            ),
        )
        # The outcome is one of two fixed values; it cannot contain a
        # password, JWT, Keycloak ID, or received service response.
        print(f"Six-stem load lifecycle broker: terminal outcome {outcome}")
        return 0 if outcome == "succeeded" else 1
    except Exception:
        # run_lifecycle_broker has already attempted to revoke every identity
        # it created. Even an unexpected startup exception must stop the two
        # waiting peers quickly without logging a credential-bearing traceback.
        _wake_peers_after_broker_failure()
        print("Six-stem load lifecycle broker failed", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)


if __name__ == "__main__":  # pragma: no cover - exercised by the future container command.
    raise SystemExit(main())
