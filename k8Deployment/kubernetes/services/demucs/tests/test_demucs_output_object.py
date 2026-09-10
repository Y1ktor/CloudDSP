"""Unit tests for deterministic private Demucs stem object planning.

These tests use tiny temporary placeholder WAV files.  They make no MinIO/S3
request, run no model, and do not contact PostgreSQL, RabbitMQ, Docker, or a
Kubernetes cluster.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from app.demucs_artifact_hash import hash_validated_demucs_stem_inventory
from app.demucs_artifacts import DEMUCS_STEMS_BY_MODE, validate_demucs_stem_inventory
from app.demucs_command import build_demucs_separation_command
from app.demucs_output_object import (
    DEMUCS_ARTIFACT_CONTENT_TYPE,
    DemucsOutputObjectContractError,
    build_demucs_stem_output_objects,
)
from app.task_lease import DemucsTaskLease


JOB_ID = "11111111-1111-4111-8111-111111111111"
TASK_ID = "22222222-2222-4222-8222-222222222222"
EVENT_ID = "33333333-3333-4333-8333-333333333333"
LEASE_TOKEN = "44444444-4444-4444-8444-444444444444"


def complete_hashed_inventory(root: Path):
    """Create one exact four-stem local output tree and hash it for a test."""

    work_directory = root / "scratch"
    work_directory.mkdir(parents=True)
    source_path = work_directory / "input" / "source.media"
    source_path.parent.mkdir()
    source_path.write_bytes(b"already-preflighted-private-source")
    output_directory = work_directory / "output"
    output_directory.mkdir()
    separation = build_demucs_separation_command(
        stem_mode="4-stems",
        source_path=source_path,
        output_directory=output_directory,
        work_directory=work_directory,
    )
    separation.expected_output_directory.mkdir(parents=True)
    for stem_name in DEMUCS_STEMS_BY_MODE["4-stems"][1]:
        (separation.expected_output_directory / f"{stem_name}.wav").write_bytes(
            f"private-{stem_name}-wav-bytes".encode("ascii")
        )
    return hash_validated_demucs_stem_inventory(validate_demucs_stem_inventory(separation))


def valid_lease(*, stem_mode: str = "4-stems") -> DemucsTaskLease:
    """Return only the minimum durable Demucs task identity needed by this boundary."""

    return DemucsTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/source.mp3",
        stem_mode=stem_mode,
        attempt_count=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )


class DemucsOutputObjectContractTests(unittest.TestCase):
    """Prove only current hash evidence can name fixed private Demucs objects."""

    def test_builds_stable_keys_and_complete_metadata_for_every_expected_stem(self) -> None:
        """A recovery produces the same job-prefix coordinates, not new attempt keys."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            hashed = complete_hashed_inventory(Path(temporary_directory))

            output_objects = build_demucs_stem_output_objects(
                lease=valid_lease(),
                hashed_inventory=hashed,
            )

            self.assertEqual(
                tuple(item.object_key for item in output_objects),
                tuple(f"stems/{JOB_ID}/{stem_name}.wav" for stem_name in DEMUCS_STEMS_BY_MODE["4-stems"][1]),
            )
            for output_object, artifact in zip(output_objects, hashed.artifacts, strict=True):
                self.assertEqual(output_object.bucket, "clouddsp-uploads")
                # The contract deliberately revalidates/hash-checks the path,
                # so it returns an equal current Path value rather than relying
                # on Python object identity from the older hash result.
                self.assertEqual(output_object.local_path, artifact.path)
                self.assertEqual(output_object.content_type, DEMUCS_ARTIFACT_CONTENT_TYPE)
                self.assertEqual(output_object.content_length, artifact.size_bytes)
                self.assertEqual(
                    output_object.s3_metadata,
                    (
                        ("schema-version", "1"),
                        ("producer", "demucs"),
                        ("job-id", JOB_ID),
                        ("task-id", TASK_ID),
                        ("stem-name", artifact.stem_name),
                        ("stem-mode", "4-stems"),
                        ("size-bytes", str(artifact.size_bytes)),
                        ("sha256", artifact.sha256),
                    ),
                )

    def test_rejects_a_lease_with_a_different_stem_mode_or_job_source_prefix(self) -> None:
        """The local output cannot be published under a different durable task identity."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            hashed = complete_hashed_inventory(Path(temporary_directory))
            with self.assertRaises(DemucsOutputObjectContractError):
                build_demucs_stem_output_objects(
                    lease=valid_lease(stem_mode="2-stems"),
                    hashed_inventory=hashed,
                )
            wrong_job_id = "55555555-5555-4555-8555-555555555555"
            with self.assertRaises(DemucsOutputObjectContractError):
                build_demucs_stem_output_objects(
                    lease=replace(
                        valid_lease(),
                        job_id=wrong_job_id,
                        input_object_key=f"uploads/{JOB_ID}/source.mp3",
                    ),
                    hashed_inventory=hashed,
                )
            # Frozen dataclasses are constructible by Python callers, so this
            # boundary must convert malformed fields into its safe category
            # instead of leaking an AttributeError from string operations.
            with self.assertRaises(DemucsOutputObjectContractError):
                build_demucs_stem_output_objects(
                    lease=replace(valid_lease(), input_object_key=object()),
                    hashed_inventory=hashed,
                )

    def test_rejects_same_size_byte_changes_or_forged_hash_evidence(self) -> None:
        """A matching filename and size cannot substitute different bytes for a stem."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            hashed = complete_hashed_inventory(Path(temporary_directory))
            first = hashed.artifacts[0]
            # Keep the exact byte count so inventory validation alone would not
            # notice this replacement; rebuilding SHA-256 evidence must catch it.
            first.path.write_bytes(b"x" * first.size_bytes)
            with self.assertRaises(DemucsOutputObjectContractError):
                build_demucs_stem_output_objects(
                    lease=valid_lease(),
                    hashed_inventory=hashed,
                )

            second = complete_hashed_inventory(Path(temporary_directory) / "second")
            forged_first = replace(second.artifacts[0], sha256="0" * 64)
            with self.assertRaises(DemucsOutputObjectContractError):
                build_demucs_stem_output_objects(
                    lease=valid_lease(),
                    hashed_inventory=replace(
                        second,
                        artifacts=(forged_first, *second.artifacts[1:]),
                    ),
                )


if __name__ == "__main__":
    unittest.main()
