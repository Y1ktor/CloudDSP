"""Offline tests for the capability contract following broker ownership proof."""

from __future__ import annotations

from dataclasses import replace
import unittest

from lifecycle_handoff import AuthenticatedLoadCoordinates, SubmittedLoadJobCoordinate
from observer_capability_contract import (
    ObserverCapabilityContractError,
    derive_verified_load_observer_capability_contract,
)
from owner_bound_job_verifier import VerifiedOwnerBoundLoadJobs


_SUBJECT = "11111111-1111-4111-8111-111111111111"
_JOB_IDS = (
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
    "44444444-4444-4444-8444-444444444444",
)


def coordinates() -> AuthenticatedLoadCoordinates:
    """Return the same bounded shape the owner-bound verifier has already proven."""

    return AuthenticatedLoadCoordinates(
        run_marker="loadrun01",
        subject=_SUBJECT,
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


def proof() -> VerifiedOwnerBoundLoadJobs:
    """Model a successful in-memory API proof without a service call or credential."""

    return VerifiedOwnerBoundLoadJobs(subject=_SUBJECT, coordinates=coordinates())


class ObserverCapabilityContractTests(unittest.TestCase):
    """Future provisioning receives only exact coordinates, never broad access."""

    def test_derives_exact_source_keys_and_job_specific_artifact_prefixes(self) -> None:
        """The contract cannot name another bucket, arbitrary prefix, or fourth Job."""

        contract = derive_verified_load_observer_capability_contract(proof=proof())

        self.assertEqual(contract.run_marker, "loadrun01")
        self.assertEqual(contract.stem_mode, "6-stems")
        self.assertEqual(contract.job_ids, _JOB_IDS)
        self.assertEqual(len(contract.object_scopes), 3)
        for ordinal, scope in enumerate(contract.object_scopes, start=1):
            job_id = _JOB_IDS[ordinal - 1]
            self.assertEqual(scope.bucket, "clouddsp-uploads")
            self.assertEqual(scope.source_object_key, f"uploads/{job_id}/six-stem-load-loadrun01-{ordinal}.wav")
            self.assertEqual(scope.stems_prefix, f"stems/{job_id}/")
            self.assertEqual(scope.midi_prefix, f"midi/{job_id}/")
        # Default representations are safe for the broker's fixed progress
        # logging: no owner identifier, job ID, source key, or test password.
        safe_representation = repr(contract)
        for private_value in (_SUBJECT, *_JOB_IDS, "temporary-password"):
            self.assertNotIn(private_value, safe_representation)

    def test_refuses_a_proof_whose_owner_and_coordinate_subject_disagree(self) -> None:
        """A marker or hand-built dataclass cannot cross the proof boundary."""

        mismatched_proof = replace(
            proof(), subject="55555555-5555-4555-8555-555555555555"
        )

        with self.assertRaisesRegex(ObserverCapabilityContractError, "did not match"):
            derive_verified_load_observer_capability_contract(proof=mismatched_proof)

    def test_refuses_a_noncanonical_or_broadened_coordinate_shape(self) -> None:
        """Later policy code cannot derive a path from an arbitrary filename."""

        malformed_job = replace(
            coordinates().jobs[0], source_filename="../../another-prefix.wav"
        )
        malformed_coordinates = replace(
            coordinates(), jobs=(malformed_job, *coordinates().jobs[1:])
        )
        malformed_proof = replace(proof(), coordinates=malformed_coordinates)

        with self.assertRaisesRegex(ObserverCapabilityContractError, "filename"):
            derive_verified_load_observer_capability_contract(proof=malformed_proof)

    def test_refuses_any_value_other_than_the_in_memory_verified_proof_type(self) -> None:
        """The writable handoff marker is intentionally not a capability input."""

        with self.assertRaisesRegex(ObserverCapabilityContractError, "not an ownership proof"):
            derive_verified_load_observer_capability_contract(proof=object())  # type: ignore[arg-type]


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
