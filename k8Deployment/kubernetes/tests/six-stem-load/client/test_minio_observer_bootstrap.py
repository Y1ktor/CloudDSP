"""Offline tests for exact-object MinIO observer identity provisioning."""

from __future__ import annotations

import json
from contextlib import redirect_stderr
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from minio_observer_bootstrap import (
    MinIOAdminSettings,
    MinIOObserverBootstrap,
    MinIOObserverBootstrapError,
    TemporaryMinIOObserverCredentials,
    read_minio_observer_credentials,
    remove_minio_observer_credentials,
    write_minio_observer_credentials,
)
from observer_capability_contract import derive_verified_load_observer_capability_contract
from test_observer_capability_contract import proof


class MinIOObserverBootstrapTests(unittest.TestCase):
    """Keep root credentials broker-only and the runtime policy exact-key read-only."""

    def test_mc_config_secures_its_child_without_chmodding_root_owned_emptydir(self) -> None:
        """The non-root broker may write within, but does not own, the mount root."""

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / "mc-work"
            work.mkdir(mode=0o770)
            original_chmod = os.chmod

            def deny_chmod_of_mount_root(path, mode, *args, **kwargs):
                if Path(path) == work:
                    raise PermissionError("root-owned mount")
                return original_chmod(path, mode, *args, **kwargs)

            adapter = MinIOObserverBootstrap(
                settings=MinIOAdminSettings(
                    root_username="root-user",
                    root_password="root-password",
                    work_directory=work,
                )
            )
            with patch(
                "minio_observer_bootstrap.os.chmod",
                side_effect=deny_chmod_of_mount_root,
            ):
                environment = adapter._prepare_mc_environment()

            config_directory = Path(environment["MC_CONFIG_DIR"])
            self.assertEqual(config_directory.parent, work)
            self.assertEqual(config_directory.stat().st_mode & 0o777, 0o700)

    def test_provisions_42_exact_getobject_resources_and_returns_private_key(self) -> None:
        """Three jobs yield 42 fixed reads and no ListBucket/Delete capability."""

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            mc_work = work / "mc"
            mc_work.mkdir(mode=0o770)
            commands: list[tuple[str, ...]] = []
            policies: list[dict[str, object]] = []

            def runner(arguments, _environment, _timeout):
                commands.append(arguments)
                if arguments[1:3] == ("admin", "policy") and arguments[3] == "create":
                    policies.append(json.loads(Path(arguments[-1]).read_text(encoding="utf-8")))

            adapter = MinIOObserverBootstrap(
                settings=MinIOAdminSettings(
                    root_username="root-user",
                    root_password="root-password-is-not-logged",
                    work_directory=mc_work,
                ),
                runner=runner,
            )
            capability = derive_verified_load_observer_capability_contract(proof=proof())
            credentials = adapter.provision(capability=capability)

            policy_commands = [command for command in commands if "policy" in command]
            self.assertEqual(len(policy_commands), 2)
            create_command = next(command for command in policy_commands if "create" in command)
            policy = policies[0]
            statement = policy["Statement"][0]
            self.assertEqual(statement["Action"], "s3:GetObject")
            self.assertEqual(len(statement["Resource"]), 42)
            self.assertEqual(len(set(statement["Resource"])), 42)
            self.assertTrue(all(resource.startswith("arn:aws:s3:::clouddsp-uploads/") for resource in statement["Resource"]))
            self.assertFalse(any("*" in resource for resource in statement["Resource"]))
            self.assertEqual(commands[0][1:3], ("alias", "set"))
            self.assertNotIn(credentials.secret_key, repr(credentials))
            self.assertFalse(Path(create_command[-1]).exists())

    def test_handoff_is_run_bound_private_and_removable_by_exact_filename(self) -> None:
        """The S3 reader's generated credential is mode 0600 and marker-bound."""

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            credentials = TemporaryMinIOObserverCredentials(
                run_marker="loadrun01",
                access_key="s6mloadrun01",
                secret_key="x" * 48,
            )
            write_minio_observer_credentials(directory, credentials=credentials)
            path = directory / "minio-observer-credentials.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                read_minio_observer_credentials(directory, expected_run_marker="loadrun01"),
                credentials,
            )
            with self.assertRaisesRegex(MinIOObserverBootstrapError, "another run"):
                read_minio_observer_credentials(directory, expected_run_marker="loadrun02")
            remove_minio_observer_credentials(directory)
            self.assertFalse(path.exists())

    def test_partial_provisioning_revokes_only_generated_minio_resources(self) -> None:
        """A policy-attach failure removes the temporary user and unique policy."""

        with tempfile.TemporaryDirectory() as temporary:
            calls: list[tuple[str, ...]] = []
            mc_work = Path(temporary) / "mc"
            mc_work.mkdir(mode=0o770)

            def runner(arguments, _environment, _timeout):
                calls.append(arguments)
                if "attach" in arguments:
                    raise RuntimeError("private fake failure")

            adapter = MinIOObserverBootstrap(
                settings=MinIOAdminSettings(
                    root_username="root-user",
                    root_password="root-password",
                    work_directory=mc_work,
                ),
                runner=runner,
            )
            capability = derive_verified_load_observer_capability_contract(proof=proof())
            failure_log = StringIO()
            with redirect_stderr(failure_log):
                with self.assertRaisesRegex(MinIOObserverBootstrapError, "provisioning failed"):
                    adapter.provision(capability=capability)
            self.assertIn("failed at stage: attach exact-object policy", failure_log.getvalue())
            self.assertNotIn("private fake failure", failure_log.getvalue())
            self.assertTrue(any("user" in call and "remove" in call for call in calls))
            self.assertTrue(any("policy" in call and "rm" in call for call in calls))

    def test_refuses_a_hand_built_invalid_credential(self) -> None:
        """A malformed marker cannot select an arbitrary alias user or policy."""

        from minio_observer_bootstrap import _validate_credentials

        with self.assertRaises(MinIOObserverBootstrapError):
            credentials = TemporaryMinIOObserverCredentials(
                run_marker="../not-safe", access_key="unsafe", secret_key="x" * 48
            )
            _validate_credentials(credentials)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
