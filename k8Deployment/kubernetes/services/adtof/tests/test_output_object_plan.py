"""Unit tests for ADTOF's running-lease-to-output-coordinate plan.

These tests create no model output and make no filesystem, MinIO, PostgreSQL,
RabbitMQ, Docker, or Kubernetes request. They prove the planner is only a
deterministic coordinate/provenance boundary before local artifact evidence
exists.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import unittest

from app.artifacts.output_object_plan import (
    ADTOF_MODEL_CONFIGURATION_ID,
    ADTOFOutputObjectPlanContractError,
    build_adtof_output_object_plans,
)
from app.artifacts.stem_download import DownloadedADTOFStem
from app.db.stem_task_start import RunningADTOFStem
from app.db.task_claim import ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
INPUT_SHA256 = "a" * 64


def running_stem(**overrides: object) -> RunningADTOFStem:
    """Return committed-running evidence without creating a scratch file.

    The intentionally nonexistent path proves the planner derives no outcome
    from the temporary WAV itself; its creation, model use, and cleanup remain
    owned by the surrounding running-stem context.
    """

    lease = ADTOFTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        stem_name="drums",
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"stems/{JOB_ID}/drums.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2026, 9, 13, 12, 15, tzinfo=UTC),
    )
    value: dict[str, object] = {
        "lease": lease,
        "stem": DownloadedADTOFStem(
            stem_path=Path("/pod-scratch/nonexistent-adtof-input/stem.wav"),
            size_bytes=1_024,
            sha256=INPUT_SHA256,
        ),
        "started_at": datetime(2026, 9, 13, 12, 20, tzinfo=UTC),
    }
    value.update(overrides)
    return RunningADTOFStem(**value)  # type: ignore[arg-type]


class ADTOFOutputObjectPlanTests(unittest.TestCase):
    """Prove one running drums lease has exactly two non-attempt-specific outputs."""

    def test_builds_fixed_coordinates_and_complete_base_provenance(self) -> None:
        """The eventual artifact verifier starts from unalterable task/input facts."""

        plans = build_adtof_output_object_plans(running=running_stem())

        self.assertEqual(plans.midi.bucket, "clouddsp-uploads")
        self.assertEqual(plans.midi.object_key, f"midi/{JOB_ID}/drums.mid")
        self.assertEqual(plans.midi.content_type, "audio/midi")
        self.assertEqual(plans.tempo_candidate.bucket, "clouddsp-uploads")
        self.assertEqual(plans.tempo_candidate.object_key, f"midi/{JOB_ID}/drums_bpm.json")
        self.assertEqual(plans.tempo_candidate.content_type, "application/json")
        self.assertEqual(
            plans.midi.base_s3_metadata,
            (
                ("schema-version", "1"),
                ("producer", "adtof"),
                ("job-id", JOB_ID),
                ("task-id", TASK_ID),
                ("request-event-id", EVENT_ID),
                ("stem-name", "drums"),
                ("stem-mode", "4-stems"),
                ("artifact-kind", "drum-midi"),
                ("input-stem-sha256", INPUT_SHA256),
                ("model-config-id", ADTOF_MODEL_CONFIGURATION_ID),
            ),
        )
        self.assertEqual(
            plans.tempo_candidate.base_s3_metadata,
            plans.midi.base_s3_metadata[:7]
            + (("artifact-kind", "tempo-candidate"),)
            + plans.midi.base_s3_metadata[8:],
        )
        # Output digest/size cannot be honestly supplied until model bytes have
        # been verified. Their absence is part of this boundary's contract.
        self.assertNotIn("sha256", dict(plans.midi.base_s3_metadata))
        self.assertNotIn("size-bytes", dict(plans.tempo_candidate.base_s3_metadata))

    def test_forged_lease_or_download_evidence_cannot_broaden_output_prefix(self) -> None:
        """Direct frozen dataclasses still require the exact ADTOF drums identity."""

        valid = running_stem()
        invalid_values = (
            replace(valid, lease=replace(valid.lease, stem_name="vocals")),
            replace(valid, lease=replace(valid.lease, input_object_key=f"stems/{JOB_ID}/other.wav")),
            replace(valid, stem=replace(valid.stem, sha256="A" * 64)),
            replace(valid, started_at=datetime(2026, 9, 13, 12, 20)),
        )
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ADTOFOutputObjectPlanContractError):
                    build_adtof_output_object_plans(running=invalid)

    def test_attempt_recovery_targets_the_same_pair_of_private_keys(self) -> None:
        """Task attempt count is evidence, never an unbounded MinIO suffix."""

        first = build_adtof_output_object_plans(running=running_stem())
        retried_running = running_stem()
        retry = build_adtof_output_object_plans(
            running=replace(
                retried_running,
                lease=replace(retried_running.lease, attempt_count=3),
            )
        )

        self.assertEqual(retry.midi.object_key, first.midi.object_key)
        self.assertEqual(retry.tempo_candidate.object_key, first.tempo_candidate.object_key)
        self.assertNotIn("attempt", retry.midi.object_key)


if __name__ == "__main__":
    unittest.main()
