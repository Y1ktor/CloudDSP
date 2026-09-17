"""Offline structural tests for the future ADTOF smoke runner dependency lock.

These tests inspect only the committed lock text. They do not invoke pip,
download archives, import Boto3/Psycopg, create a client, open a socket, build
an image, read a Secret, or change a Kubernetes resource.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


LOCK_PATH = Path(__file__).parent / "requirements.lock"
PACKAGE_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==")
HASH_PATTERN = re.compile(r"--hash=sha256:[0-9a-f]{64}")


def locked_requirements() -> dict[str, str]:
    """Join continued requirement lines and return one entry per normalized package."""

    requirements: dict[str, str] = {}
    pending = ""
    for raw_line in LOCK_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        match = PACKAGE_PATTERN.match(pending)
        if match is None:
            raise AssertionError(f"requirements lock has an invalid requirement: {pending}")
        name = match.group(1).lower()
        if name in requirements:
            raise AssertionError(f"requirements lock duplicates package {name}")
        requirements[name] = pending
        pending = ""
    if pending:
        raise AssertionError("requirements lock ends with an incomplete requirement")
    return requirements


class ADTOFWorkerSmokeRequirementsLockTests(unittest.TestCase):
    """Keep the future runner minimal, complete, hash-enforced, and ARM64-reviewable."""

    def test_lock_is_exact_minimal_s3_postgresql_closure_with_one_hash_per_package(self) -> None:
        """A later `pip --require-hashes` install cannot resolve an unreviewed dependency."""

        requirements = locked_requirements()
        self.assertEqual(
            set(requirements),
            {
                "boto3",
                "botocore",
                "jmespath",
                "psycopg",
                "psycopg-binary",
                "python-dateutil",
                "s3transfer",
                "six",
                "typing-extensions",
                "urllib3",
            },
        )
        self.assertTrue(all(len(HASH_PATTERN.findall(line)) == 1 for line in requirements.values()))

    def test_reviewed_driver_and_client_versions_stay_paired(self) -> None:
        """S3/PostgreSQL imports later match the existing local smoke-client profile."""

        requirements = locked_requirements()
        self.assertTrue(requirements["boto3"].startswith("boto3==1.43.89 "))
        self.assertTrue(requirements["botocore"].startswith("botocore==1.43.89 "))
        self.assertTrue(requirements["psycopg"].startswith("psycopg==3.2.13 "))
        self.assertTrue(requirements["psycopg-binary"].startswith("psycopg-binary==3.2.13 "))
        self.assertIn(
            "--hash=sha256:082579f2ae41bdabe20c82810810f3e290ac2206cccf0cb41cf36b3218f53b3c",
            requirements["psycopg-binary"],
        )

    def test_lock_excludes_broker_web_ml_gpu_and_kubernetes_dependencies(self) -> None:
        """The smoke runner observes the deployed pipeline instead of becoming a worker."""

        requirements = locked_requirements()
        prohibited = {
            "pika",
            "fastapi",
            "uvicorn",
            "keycloak",
            "tensorflow",
            "basic-pitch",
            "torch",
            "demucs",
            "cuda",
            "kubernetes",
        }
        self.assertTrue(prohibited.isdisjoint(requirements))

    def test_lock_source_has_no_index_or_unhashed_direct_url_escape_hatch(self) -> None:
        """A future image build must consume exactly the reviewed artifact closure."""

        source = LOCK_PATH.read_text(encoding="utf-8")
        self.assertNotIn("--index-url", source)
        self.assertNotIn("--extra-index-url", source)
        self.assertNotIn(" @ http", source)
        self.assertNotIn("git+", source)


if __name__ == "__main__":
    unittest.main()
