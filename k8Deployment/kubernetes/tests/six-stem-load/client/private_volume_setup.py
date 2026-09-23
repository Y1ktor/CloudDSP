"""Prepare the load Job's five private memory volumes before any app container.

Kubernetes creates these ``emptyDir`` mount roots as root-owned directories.
On the local k3d profile their observed mode is 03777, even with a Pod
``fsGroup``. The broker correctly refuses a world-writable password handoff.
This one init container runs before the client, broker, and observer, receives
no Secret or API token, and changes only the five fixed empty mount roots to
group-writable 0770. The ordinary containers remain non-root UID/GID 10012.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
from typing import Iterable


_VOLUME_PATHS = (
    Path("/var/run/clouddsp-six-stem-load"),
    Path("/var/run/clouddsp-six-stem-postgresql-observer"),
    Path("/var/run/clouddsp-six-stem-minio-observer"),
    Path("/var/run/clouddsp-six-stem-minio-admin"),
    Path("/var/run/clouddsp-six-stem-observer-reports"),
)
_APPLICATION_GID = 10012
_PRIVATE_DIRECTORY_MODE = 0o770


class PrivateVolumeSetupError(RuntimeError):
    """A safe setup failure that never reports files or credential values."""


def secure_private_volumes(
    paths: Iterable[Path] = _VOLUME_PATHS,
    *,
    expected_owner_uid: int = 0,
    expected_group_gid: int = _APPLICATION_GID,
) -> None:
    """Validate every fresh mount root, then remove world access from each.

    Validation happens before the first chmod so an unexpected mount, symlink,
    ownership change, or stale file cannot lead to a partially prepared Pod.
    The init container is the only process running at this point, so no peer
    can race its lstat/chmod sequence. The trusted app processes later share
    GID 10012 and can write their own 0600 handoff files in these directories.
    """

    selected = tuple(paths)
    if len(selected) != len(set(selected)) or not selected:
        raise PrivateVolumeSetupError("private volume path list was invalid")
    for path in selected:
        try:
            metadata = path.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != expected_owner_uid
                or metadata.st_gid != expected_group_gid
                or any(path.iterdir())
            ):
                raise PrivateVolumeSetupError("private volume was not a fresh expected mount")
        except OSError:
            raise PrivateVolumeSetupError("private volume could not be inspected") from None

    for path in selected:
        try:
            os.chmod(path, _PRIVATE_DIRECTORY_MODE)
            if stat.S_IMODE(path.lstat().st_mode) != _PRIVATE_DIRECTORY_MODE:
                raise PrivateVolumeSetupError("private volume mode did not become private")
        except OSError:
            raise PrivateVolumeSetupError("private volume could not be secured") from None


def main() -> int:
    """Run only in the root init container, before any credential is mounted."""

    try:
        if os.geteuid() != 0:
            raise PrivateVolumeSetupError("private volume setup did not run as mount owner")
        secure_private_volumes()
    except PrivateVolumeSetupError:
        print("Six-stem load private volume setup failed", file=sys.stderr)
        return 1
    print("Six-stem load private volumes are ready")
    return 0


if __name__ == "__main__":  # pragma: no cover - executed by the Job init container.
    raise SystemExit(main())
