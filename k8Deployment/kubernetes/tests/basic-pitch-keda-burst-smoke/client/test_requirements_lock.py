"""Source checks for the Basic Pitch KEDA burst client's dependency closure.

This test reads the reviewed lockfile only. It does not install/download a
package, access an index, build an image, load a Secret, or change the cluster.
The explicit pip verification command remains a separate local validation.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_LOCK = Path(__file__).resolve().parent / "requirements.lock"
_HASH_PATTERN = re.compile(r"--hash=sha256:[0-9a-f]{64}")


class RequirementsLockTests(unittest.TestCase):
    """Keep the later smoke image small, hash-verified, and least-privilege."""

    def test_exact_s3_and_postgresql_dependency_closure_is_hash_enforced(self) -> None:
        """Boto3/Psycopg need their reviewed transitive packages, nothing broader."""

        lock = _LOCK.read_text(encoding="utf-8")
        expected = {
            "boto3": "1.43.89",
            "botocore": "1.43.89",
            "jmespath": "1.1.0",
            "psycopg": "3.2.13",
            "psycopg-binary": "3.2.13",
            "python-dateutil": "2.9.0.post0",
            "s3transfer": "0.19.2",
            "six": "1.17.0",
            "typing-extensions": "4.16.0",
            "urllib3": "2.7.0",
        }
        for package, version in expected.items():
            self.assertIn(f"{package}=={version} \\", lock)
        self.assertEqual(len(_HASH_PATTERN.findall(lock)), len(expected))
        for excluded in ("pika==", "kubernetes==", "fastapi==", "tensorflow==", "torch==", "boto3-stubs"):
            self.assertNotIn(excluded, lock)

    def test_lock_records_arm64_binary_wheel_boundary(self) -> None:
        """Another CPU architecture must be deliberately reviewed, never implicit."""

        lock = _LOCK.read_text(encoding="utf-8")
        self.assertIn("Python 3.12 Linux/ARM64", lock)
        self.assertIn("psycopg-binary", lock)
        self.assertIn("--require-hashes", lock)


if __name__ == "__main__":
    unittest.main()
