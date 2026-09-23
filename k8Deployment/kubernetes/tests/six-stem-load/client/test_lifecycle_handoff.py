"""Unit tests for the private load-client handoff and Keycloak lifecycle broker."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from keycloak_identity_lifecycle import LoadTestIdentity
from lifecycle_broker import LifecycleBrokerError, main as broker_main, run_lifecycle_broker
from lifecycle_handoff import (
    BROKER_FAILURE_FILE_NAME,
    CLIENT_RESULT_FILE_NAME,
    COORDINATES_FILE_NAME,
    IDENTITY_FILE_NAME,
    VERIFIED_INGRESS_FILE_NAME,
    AuthenticatedLoadCoordinates,
    HandoffError,
    SubmittedLoadJobCoordinate,
    TemporaryPostgreSQLObserverCredentials,
    broker_failure_observed,
    prepare_empty_handoff_directory,
    read_authenticated_load_coordinates,
    read_verified_authenticated_ingress,
    read_temporary_load_identity,
    wait_for_authenticated_load_coordinates,
    wait_for_client_outcome,
    wait_for_temporary_load_identity,
    write_authenticated_load_coordinates,
    write_client_outcome,
    write_broker_failure_marker,
    write_verified_authenticated_ingress,
    write_temporary_load_identity,
)
from load_test_terminal_outcome import LoadTestTerminalOutcome
from owner_bound_job_verifier import VerifiedOwnerBoundLoadJobs
from postgresql_durable_state_observer import PostgreSQLDurableStateSnapshot
from postgresql_observer_report import PostgreSQLObserverReport


_CLIENT_UUID = "11111111-1111-4111-8111-111111111111"
_USER_UUID = "22222222-2222-4222-8222-222222222222"
_SUBJECT_UUID = "33333333-3333-4333-8333-333333333333"
_JOB_IDS = (
    "44444444-4444-4444-8444-444444444444",
    "55555555-5555-4555-8555-555555555555",
    "66666666-6666-4666-8666-666666666666",
)


def test_identity() -> LoadTestIdentity:
    """Return a fixed fake identity; its password remains absent from assertion output."""

    return LoadTestIdentity(
        run_marker="loadrun01",
        client_id="clouddsp-six-stem-load-loadrun01",
        client_uuid=_CLIENT_UUID,
        user_id=_USER_UUID,
        username="six-stem-load-loadrun01",
        email="six-stem-load-loadrun01@clouddsp.test",
        password="temporary-password",
    )


def test_coordinates() -> AuthenticatedLoadCoordinates:
    """Return fixed complete ingress evidence without an object key or credential."""

    return AuthenticatedLoadCoordinates(
        run_marker="loadrun01",
        subject=_SUBJECT_UUID,
        stem_mode="6-stems",
        jobs=tuple(
            SubmittedLoadJobCoordinate(
                ordinal=ordinal,
                job_id=job_id,
                source_filename=f"six-stem-load-loadrun01-{ordinal}.wav",
                source_size_bytes=352844,
                source_sha256=f"{ordinal:x}" * 64,
            )
            for ordinal, job_id in enumerate(_JOB_IDS, start=1)
        ),
    )


class FakeLifecycle:
    """Record lifecycle calls without creating a Keycloak user during unit tests."""

    def __init__(self) -> None:
        self.create_markers: list[str] = []
        self.revoked: list[LoadTestIdentity] = []

    def create(self, *, run_marker: str) -> LoadTestIdentity:
        self.create_markers.append(run_marker)
        return test_identity()

    def revoke(self, *, identity: LoadTestIdentity) -> None:
        self.revoked.append(identity)


class FakePostgreSQLObserverProvisioner:
    """Track only the temporary database identity lifecycle in offline tests."""

    def __init__(self) -> None:
        self.provisioned: list[object] = []
        self.revoked: list[TemporaryPostgreSQLObserverCredentials] = []

    def provision(self, *, capability: object) -> TemporaryPostgreSQLObserverCredentials:
        self.provisioned.append(capability)
        return TemporaryPostgreSQLObserverCredentials(
            run_marker="loadrun01",
            username="clouddsp_six_stem_observer_loadrun01",
            password="a" * 40,
            function_schema="public",
            function_name="clouddsp_six_stem_observe_loadrun01",
        )

    def revoke(self, *, credentials: TemporaryPostgreSQLObserverCredentials) -> None:
        self.revoked.append(credentials)


class LifecycleHandoffTests(unittest.TestCase):
    """Prove the client receives no administrator identifiers and cleanup is exact."""

    def test_identity_handoff_is_private_and_omits_administrator_resource_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            prepare_empty_handoff_directory(directory)

            destination = write_temporary_load_identity(directory, test_identity())
            received = read_temporary_load_identity(directory)

            self.assertEqual(destination.name, IDENTITY_FILE_NAME)
            self.assertEqual(received.client_id, test_identity().client_id)
            self.assertEqual(received.username, test_identity().username)
            self.assertEqual(received.password, test_identity().password)
            serialized = destination.read_text(encoding="utf-8")
            self.assertNotIn(_CLIENT_UUID, serialized)
            self.assertNotIn(_USER_UUID, serialized)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_stale_control_file_prevents_identity_provisioning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            (directory / IDENTITY_FILE_NAME).write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(HandoffError, "stale control data"):
                prepare_empty_handoff_directory(directory)

    def test_kubernetes_default_read_execute_directory_mode_is_safe_when_files_are_private(self) -> None:
        """A 0755 directory is safe, while the live 03777 emptyDir needs init chmod."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            # A kubelet-created volume may be 0755. It permits discovery of a
            # fixed filename but not writing/replacing it, and the 0600 file
            # itself remains unreadable to another UID.
            os.chmod(directory, 0o755)

            prepare_empty_handoff_directory(directory)
            destination = write_temporary_load_identity(directory, test_identity())

            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_world_writable_emptydir_is_rejected_before_identity_creation(self) -> None:
        """The observed kubelet 03777 mode must fail closed until init repairs it."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o3777)
            with self.assertRaisesRegex(HandoffError, "other-user writes"):
                prepare_empty_handoff_directory(directory)

    def test_broker_failure_marker_wakes_the_client_without_a_twelve_minute_wait(self) -> None:
        """No temporary user can arrive after broker startup has failed."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            self.assertFalse(broker_failure_observed(directory))
            marker = write_broker_failure_marker(directory)

            self.assertEqual(marker.name, BROKER_FAILURE_FILE_NAME)
            self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                marker.read_text(encoding="utf-8"),
                '{"schema_version":1,"status":"failed"}',
            )
            with self.assertRaisesRegex(HandoffError, "broker failed"):
                wait_for_temporary_load_identity(directory, timeout_seconds=1)

    def test_broker_entrypoint_signals_both_waiting_containers_on_early_failure(self) -> None:
        """An invalid startup setting wakes client and observer without credentials."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            handoff = base / "client"
            reports = base / "observer"
            handoff.mkdir(mode=0o700)
            reports.mkdir(mode=0o700)
            with patch.dict(
                os.environ,
                {
                    "SIX_STEM_LOAD_HANDOFF_DIR": str(handoff),
                    "SIX_STEM_LOAD_OBSERVER_REPORT_DIR": str(reports),
                },
                clear=True,
            ):
                self.assertEqual(broker_main(), 1)

            self.assertTrue(broker_failure_observed(handoff))
            self.assertTrue(broker_failure_observed(reports))

    def test_outcome_requires_one_strict_terminal_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            prepare_empty_handoff_directory(directory)
            write_client_outcome(directory, outcome="succeeded")

            self.assertEqual(wait_for_client_outcome(directory, timeout_seconds=1), "succeeded")
            self.assertEqual((directory / CLIENT_RESULT_FILE_NAME).stat().st_mode & 0o777, 0o600)

    def test_client_waits_for_the_broker_identity_handoff(self) -> None:
        """Parallel Pod containers coordinate by a private file, not start order."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            prepare_empty_handoff_directory(directory)
            write_temporary_load_identity(directory, test_identity())

            self.assertEqual(
                wait_for_temporary_load_identity(directory, timeout_seconds=1),
                read_temporary_load_identity(directory),
            )

    def test_broker_is_woken_by_a_failed_client_before_coordinates_exist(self) -> None:
        """A failed upload does not make the broker sleep through its full run budget."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            prepare_empty_handoff_directory(directory)
            write_client_outcome(directory, outcome="failed")

            with self.assertRaisesRegex(HandoffError, "failed before publishing"):
                wait_for_authenticated_load_coordinates(directory, timeout_seconds=1)

    def test_complete_coordinates_are_private_and_validate_the_fixed_three_job_shape(self) -> None:
        """The broker receives no URL, token, object prefix, or mutable job count."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            prepare_empty_handoff_directory(directory)

            destination = write_authenticated_load_coordinates(
                directory, coordinates=test_coordinates()
            )
            received = read_authenticated_load_coordinates(directory)

            self.assertEqual(destination.name, COORDINATES_FILE_NAME)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(received, test_coordinates())
            serialized = destination.read_text(encoding="utf-8")
            self.assertNotIn("temporary-password", serialized)
            self.assertNotIn("uploads/", serialized)

    def test_coordinate_reader_rejects_noncanonical_or_partial_claims(self) -> None:
        """A broker must fail closed before it can derive any observer policy."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            prepare_empty_handoff_directory(directory)
            write_authenticated_load_coordinates(directory, coordinates=test_coordinates())
            coordinate_path = directory / COORDINATES_FILE_NAME
            coordinate_path.write_text(
                '{"schema_version":1,"run_marker":"loadrun01","subject":"33333333-3333-4333-8333-333333333333","stem_mode":"6-stems","jobs":[]}',
                encoding="utf-8",
            )
            os.chmod(coordinate_path, 0o600)

            with self.assertRaisesRegex(HandoffError, "coordinates were invalid"):
                read_authenticated_load_coordinates(directory)

    def test_coordinate_waiter_and_verified_marker_keep_the_two_states_distinct(self) -> None:
        """A complete claim is not proof until the broker writes its minimal marker."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            prepare_empty_handoff_directory(directory)
            write_authenticated_load_coordinates(directory, coordinates=test_coordinates())

            received = wait_for_authenticated_load_coordinates(directory, timeout_seconds=1)
            marker_path = write_verified_authenticated_ingress(directory, coordinates=received)
            marker = read_verified_authenticated_ingress(directory)

            self.assertEqual(marker_path.name, VERIFIED_INGRESS_FILE_NAME)
            self.assertEqual(marker_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(marker.run_marker, "loadrun01")
            self.assertEqual(marker.stem_mode, "6-stems")
            self.assertEqual(marker.job_count, 3)
            serialized = marker_path.read_text(encoding="utf-8")
            for private_value in (_SUBJECT_UUID, *_JOB_IDS, "temporary-password"):
                self.assertNotIn(private_value, serialized)

    def test_broker_verifies_coordinates_before_outcome_and_removes_all_handoff_files(self) -> None:
        """The durable lifecycle cannot skip the owner-bound proof phase."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            lifecycle = FakeLifecycle()
            verified: list[tuple[LoadTestIdentity, AuthenticatedLoadCoordinates]] = []

            def coordinate_waiter(
                wait_directory: Path, *, timeout_seconds: float
            ) -> AuthenticatedLoadCoordinates:
                self.assertGreater(timeout_seconds, 0)
                self.assertTrue((wait_directory / IDENTITY_FILE_NAME).is_file())
                write_authenticated_load_coordinates(
                    wait_directory, coordinates=test_coordinates()
                )
                return read_authenticated_load_coordinates(wait_directory)

            def coordinate_verifier(
                received_identity: LoadTestIdentity, coordinates: AuthenticatedLoadCoordinates
            ) -> object:
                verified.append((received_identity, coordinates))
                return object()

            def successful_outcome_waiter(
                wait_directory: Path, *, timeout_seconds: float
            ) -> str:
                self.assertGreater(timeout_seconds, 0)
                marker = read_verified_authenticated_ingress(wait_directory)
                self.assertEqual(marker.run_marker, "loadrun01")
                write_client_outcome(wait_directory, outcome="succeeded")
                return "succeeded"

            outcome = run_lifecycle_broker(
                lifecycle=lifecycle,
                handoff_directory=directory,
                wait_seconds=60,
                run_marker="loadrun01",
                coordinate_waiter=coordinate_waiter,
                coordinate_verifier=coordinate_verifier,
                outcome_waiter=successful_outcome_waiter,
            )

            self.assertEqual(outcome, "succeeded")
            self.assertEqual(lifecycle.create_markers, ["loadrun01"])
            self.assertEqual(lifecycle.revoked, [test_identity()])
            self.assertEqual(verified, [(test_identity(), test_coordinates())])
            self.assertFalse((directory / IDENTITY_FILE_NAME).exists())
            self.assertFalse((directory / COORDINATES_FILE_NAME).exists())
            self.assertFalse((directory / VERIFIED_INGRESS_FILE_NAME).exists())
            self.assertFalse((directory / CLIENT_RESULT_FILE_NAME).exists())

    def test_database_snapshot_does_not_revoke_capabilities_before_terminal_outcome(self) -> None:
        """The broker keeps temporary identities until combined observer evidence is final."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            handoff_directory = root / "client-handoff"
            credentials_directory = root / "observer-credentials"
            report_directory = root / "observer-reports"
            for directory in (handoff_directory, credentials_directory, report_directory):
                directory.mkdir(mode=0o700)
                os.chmod(directory, 0o700)

            lifecycle = FakeLifecycle()
            provisioner = FakePostgreSQLObserverProvisioner()
            saw_database_snapshot = False

            def coordinate_waiter(
                wait_directory: Path, *, timeout_seconds: float
            ) -> AuthenticatedLoadCoordinates:
                self.assertGreater(timeout_seconds, 0)
                write_authenticated_load_coordinates(
                    wait_directory, coordinates=test_coordinates()
                )
                return read_authenticated_load_coordinates(wait_directory)

            def verified_proof(
                _identity: LoadTestIdentity, coordinates: AuthenticatedLoadCoordinates
            ) -> VerifiedOwnerBoundLoadJobs:
                return VerifiedOwnerBoundLoadJobs(
                    subject=coordinates.subject,
                    coordinates=coordinates,
                )

            def snapshot_waiter(
                _directory: Path, *, expected_run_marker: str, timeout_seconds: float
            ) -> PostgreSQLObserverReport:
                nonlocal saw_database_snapshot
                self.assertEqual(expected_run_marker, "loadrun01")
                self.assertGreater(timeout_seconds, 0)
                saw_database_snapshot = True
                return PostgreSQLObserverReport(
                    run_marker=expected_run_marker,
                    status="observed",
                    snapshot=PostgreSQLDurableStateSnapshot(
                        observed_job_count=3,
                        source_uploaded_count=3,
                        job_status_counts=(("source_uploaded", 3),),
                        demucs_succeeded_count=0,
                        basic_pitch_succeeded_count=0,
                        adtof_succeeded_count=0,
                        task_failure_count=0,
                        active_task_lease_count=0,
                        task_status_counts=(("demucs:queued", 3),),
                        outbox_delivery_counts=(("demucs:requested:published", 3),),
                    ),
                )

            def terminal_waiter(
                _directory: Path, *, expected_run_marker: str, timeout_seconds: float
            ) -> LoadTestTerminalOutcome:
                self.assertTrue(saw_database_snapshot)
                self.assertEqual(expected_run_marker, "loadrun01")
                self.assertGreater(timeout_seconds, 0)
                # This callback is entered immediately after the one DB
                # snapshot. Neither identity is revoked until it returns a
                # separate terminal status.
                self.assertEqual(lifecycle.revoked, [])
                self.assertEqual(provisioner.revoked, [])
                return LoadTestTerminalOutcome(expected_run_marker, "succeeded")

            outcome = run_lifecycle_broker(
                lifecycle=lifecycle,
                handoff_directory=handoff_directory,
                wait_seconds=60,
                run_marker="loadrun01",
                coordinate_waiter=coordinate_waiter,
                coordinate_verifier=verified_proof,
                postgresql_observer_provisioner=provisioner,
                observer_credential_directory=credentials_directory,
                observer_report_directory=report_directory,
                postgresql_observer_report_waiter=snapshot_waiter,
                terminal_outcome_waiter=terminal_waiter,
            )

            self.assertEqual(outcome, "succeeded")
            self.assertTrue(saw_database_snapshot)
            self.assertEqual(lifecycle.revoked, [test_identity()])
            self.assertEqual(len(provisioner.revoked), 1)
            self.assertFalse(
                (credentials_directory / "postgresql-observer-credentials.json").exists()
            )
            self.assertEqual(list(handoff_directory.iterdir()), [])

    def test_broker_still_revokes_identity_when_outcome_wait_fails(self) -> None:
        """Failure after verification retains no temporary identity handoff."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            lifecycle = FakeLifecycle()

            def coordinate_waiter(
                wait_directory: Path, *, timeout_seconds: float
            ) -> AuthenticatedLoadCoordinates:
                self.assertGreater(timeout_seconds, 0)
                write_authenticated_load_coordinates(wait_directory, coordinates=test_coordinates())
                return read_authenticated_load_coordinates(wait_directory)

            def failing_waiter(_directory: Path, *, timeout_seconds: float) -> str:
                self.assertGreater(timeout_seconds, 0)
                raise HandoffError("client did not report")

            with self.assertRaisesRegex(LifecycleBrokerError, "lifecycle broker failed"):
                run_lifecycle_broker(
                    lifecycle=lifecycle,
                    handoff_directory=directory,
                    wait_seconds=60,
                    run_marker="loadrun01",
                    coordinate_waiter=coordinate_waiter,
                    coordinate_verifier=lambda _identity, _coordinates: object(),
                    outcome_waiter=failing_waiter,
                )

            self.assertEqual(lifecycle.revoked, [test_identity()])
            self.assertFalse((directory / IDENTITY_FILE_NAME).exists())
            self.assertFalse((directory / COORDINATES_FILE_NAME).exists())
            self.assertFalse((directory / VERIFIED_INGRESS_FILE_NAME).exists())

    def test_broker_revokes_identity_when_owner_bound_verification_rejects_coordinates(self) -> None:
        """A forged coordinate claim cannot leave a direct-grant identity behind."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            lifecycle = FakeLifecycle()

            def coordinate_waiter(
                wait_directory: Path, *, timeout_seconds: float
            ) -> AuthenticatedLoadCoordinates:
                self.assertGreater(timeout_seconds, 0)
                write_authenticated_load_coordinates(wait_directory, coordinates=test_coordinates())
                return read_authenticated_load_coordinates(wait_directory)

            def rejected_verifier(_identity: LoadTestIdentity, _coordinates: AuthenticatedLoadCoordinates) -> object:
                raise LifecycleBrokerError("fake verifier rejected the claim")

            with self.assertRaisesRegex(LifecycleBrokerError, "lifecycle broker failed"):
                run_lifecycle_broker(
                    lifecycle=lifecycle,
                    handoff_directory=directory,
                    wait_seconds=60,
                    run_marker="loadrun01",
                    coordinate_waiter=coordinate_waiter,
                    coordinate_verifier=rejected_verifier,
                )

            self.assertEqual(lifecycle.revoked, [test_identity()])
            self.assertFalse((directory / IDENTITY_FILE_NAME).exists())
            self.assertFalse((directory / COORDINATES_FILE_NAME).exists())
            self.assertFalse((directory / VERIFIED_INGRESS_FILE_NAME).exists())


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
