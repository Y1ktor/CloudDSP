"""Offline checks for kubelet-created emptyDir permission repair."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest

from private_volume_setup import PrivateVolumeSetupError, secure_private_volumes


class PrivateVolumeSetupTests(unittest.TestCase):
    """The init step changes only expected, fresh mounts before peers start."""

    def test_secures_kubelet_style_world_writable_mount_roots(self) -> None:
        """World-writable mount roots must become group-only 0770."""

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            paths = tuple(base / f"private-{ordinal}" for ordinal in range(5))
            for path in paths:
                path.mkdir()
                # macOS may clear setgid for an unprivileged test process; the
                # unsafe world-write bit is the behavior under test here.
                path.chmod(0o777)
            outside = base / "outside"
            outside.mkdir()
            outside.chmod(0o777)

            secure_private_volumes(
                paths,
                expected_owner_uid=os.getuid(),
                expected_group_gid=os.getgid(),
            )

            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o770 for path in paths))
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o777)

    def test_stale_file_rejects_all_mounts_before_any_chmod(self) -> None:
        """A partially prepared Pod must never start with a stale handoff."""

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            paths = tuple(base / f"private-{ordinal}" for ordinal in range(5))
            for path in paths:
                path.mkdir()
                path.chmod(0o777)
            (paths[-1] / "unexpected").write_text("stale", encoding="utf-8")

            with self.assertRaisesRegex(PrivateVolumeSetupError, "fresh expected mount"):
                secure_private_volumes(
                    paths,
                    expected_owner_uid=os.getuid(),
                    expected_group_gid=os.getgid(),
                )

            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o777 for path in paths))

    def test_rejects_symlink_and_unexpected_owner_group(self) -> None:
        """The root init step must not chmod an arbitrary filesystem path."""

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / "real"
            real.mkdir()
            link = base / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(PrivateVolumeSetupError, "fresh expected mount"):
                secure_private_volumes(
                    (link,),
                    expected_owner_uid=os.getuid(),
                    expected_group_gid=os.getgid(),
                )
            with self.assertRaisesRegex(PrivateVolumeSetupError, "fresh expected mount"):
                secure_private_volumes(
                    (real,),
                    expected_owner_uid=os.getuid(),
                    expected_group_gid=os.getgid() + 1,
                )


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
