"""Structural tests for ADTOF's CPU/Linux-ARM64 dependency lock.

These tests inspect only committed text. They do not invoke pip, download an
archive, build an image, import ADTOF/PyTorch, read a Secret, or change a
Kubernetes resource. The Dockerfile task will validate Linux installation.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


LOCK_PATH = Path(__file__).resolve().parents[1] / "requirements.lock"
PACKAGE_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:==|\s+@\s+)")
HASH_PATTERN = re.compile(r"--hash=sha256:[0-9a-f]{64}")


def lock_requirements() -> dict[str, str]:
    """Return each logical requirement joined across trailing backslashes."""

    requirements: dict[str, str] = {}
    pending = ""
    for raw_line in LOCK_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending = f"{pending} {stripped}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        match = PACKAGE_PATTERN.match(pending)
        if match is None:
            raise AssertionError(f"requirements lock has an invalid requirement: {pending}")
        requirements[match.group(1).lower()] = pending
        pending = ""
    if pending:
        raise AssertionError("requirements lock ends with an incomplete requirement")
    return requirements


class ADTOFRequirementsLockTests(unittest.TestCase):
    """Keep source, package, and interpreter pins from regressing later."""

    def test_all_requirements_are_hash_pinned_and_complete(self) -> None:
        """Every source/wheel archive stays explicit for `--no-deps` installs."""

        requirements = lock_requirements()
        expected = {
            "adtof-pytorch", "pretty-midi", "torch", "boto3", "pika",
            "psycopg", "psycopg-binary", "librosa", "numpy", "scipy",
            "numba", "llvmlite", "soundfile", "soxr", "scikit-learn",
            "mido", "setuptools", "wheel",
        }
        self.assertTrue(expected.issubset(requirements))
        self.assertTrue(all(HASH_PATTERN.search(line) for line in requirements.values()))

    def test_model_source_is_fixed_to_the_reviewed_commit_archive(self) -> None:
        """A build may not resolve a moving branch, tag, or VCS checkout."""

        requirement = lock_requirements()["adtof-pytorch"]
        self.assertIn(
            "https://codeload.github.com/xavriley/ADTOF-pytorch/tar.gz/"
            "85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9",
            requirement,
        )
        self.assertIn(
            "--hash=sha256:28602a3bd89836240d519396b566966c52b6439e2f3cda61d8a674433b350b56",
            requirement,
        )

    def test_arm64_numba_wheel_hash_matches_the_reviewed_cpu_artifact(self) -> None:
        """A one-character lock typo must fail source validation before an image build."""

        self.assertIn(
            "--hash=sha256:5f4fde652ea604ea3c86508a3fb31556a6157b2c76c8b51b1d45eb40c8598703",
            lock_requirements()["numba"],
        )

    def test_setuptools_retains_pretty_midi_runtime_pkg_resources_compatibility(self) -> None:
        """The legacy MIDI package must not lose its import module in a rebuild."""

        self.assertEqual(
            lock_requirements()["setuptools"],
            "setuptools==79.0.1 "
            "--hash=sha256:e147c0549f27767ba362f9da434eab9c5dc0045d5304feb602a0af001089fc51",
        )

    def test_python_311_lock_excludes_python_313_only_dead_batteries(self) -> None:
        """Cross-version resolver output cannot add unused Python-3.13 packages."""

        requirements = lock_requirements()
        self.assertNotIn("standard-aifc", requirements)
        self.assertNotIn("standard-sunau", requirements)


if __name__ == "__main__":
    unittest.main()
