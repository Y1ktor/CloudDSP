"""Unit tests for the pure Basic Pitch KEDA burst client contract.

These tests create only in-memory controlled WAV bytes. They do not load
Secrets, import Boto3/Psycopg, contact MinIO/PostgreSQL/RabbitMQ, build an
image, start a Kubernetes Pod, or alter the local cluster.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from basic_pitch_keda_burst_smoke import (
    BURST_COORDINATES,
    COMPLETION_TIMEOUT_SECONDS,
    DATABASE_HOST,
    EXPECTED_WAV_SIZE_BYTES,
    FIXED_OBJECT_KEYS,
    INPUT_METADATA_NAMES,
    MINIO_ENDPOINT_URL,
    PREPARE_SQL,
    BasicPitchKedaBurstConfigurationError,
    BurstSettings,
    assert_fixed_object_key,
    build_all_controlled_wavs,
    build_controlled_wav,
    prepare_function_parameters,
)


class ContractCoordinateTests(unittest.TestCase):
    """Prove all durable/object coordinates stay fixed and internally aligned."""

    def test_contract_has_three_unique_coordinates_and_six_literal_object_keys(self) -> None:
        """Three work items are needed to observe the zero-to-three policy."""

        self.assertEqual([item.label for item in BURST_COORDINATES], ["vocals", "bass", "other"])
        self.assertEqual(len({item.job_id for item in BURST_COORDINATES}), 3)
        self.assertEqual(len({item.event_id for item in BURST_COORDINATES}), 3)
        self.assertEqual(len({item.synthetic_demucs_task_id for item in BURST_COORDINATES}), 3)
        self.assertEqual(len(FIXED_OBJECT_KEYS), 6)
        for coordinate in BURST_COORDINATES:
            self.assertIn(coordinate.stem_key, FIXED_OBJECT_KEYS)
            self.assertIn(coordinate.midi_key, FIXED_OBJECT_KEYS)
            self.assertEqual(coordinate.stem_key, f"stems/{coordinate.job_id}/{coordinate.stem_name}.wav")
            self.assertEqual(coordinate.midi_key, f"midi/{coordinate.job_id}/{coordinate.stem_name}.mid")

    def test_only_a_literal_contract_key_is_accepted(self) -> None:
        """A typo or prefix broadening fails before a future S3 request exists."""

        assert_fixed_object_key(BURST_COORDINATES[0].stem_key)
        with self.assertRaises(BasicPitchKedaBurstConfigurationError):
            assert_fixed_object_key("stems/another-job/vocals.wav")


class ControlledWavTests(unittest.TestCase):
    """Prove each generated input is deterministic, real audio, and provenance-bound."""

    def test_each_wav_is_valid_deterministic_and_has_exact_provenance(self) -> None:
        """No host clock, random input, or caller data can alter the fixture."""

        first_pass = build_all_controlled_wavs()
        second_pass = build_all_controlled_wavs()
        self.assertEqual([item.body for item in first_pass], [item.body for item in second_pass])
        self.assertEqual([item.size_bytes for item in first_pass], [EXPECTED_WAV_SIZE_BYTES] * 3)
        self.assertEqual(len({item.sha256 for item in first_pass}), 3)
        for wav in first_pass:
            self.assertEqual(wav.body[:4], b"RIFF")
            self.assertEqual(wav.body[8:12], b"WAVE")
            metadata = wav.metadata()
            self.assertEqual(set(metadata), INPUT_METADATA_NAMES)
            self.assertEqual(metadata["job-id"], wav.coordinate.job_id)
            self.assertEqual(metadata["task-id"], wav.coordinate.synthetic_demucs_task_id)
            self.assertEqual(metadata["stem-name"], wav.coordinate.stem_name)
            self.assertEqual(metadata["size-bytes"], str(EXPECTED_WAV_SIZE_BYTES))
            self.assertEqual(metadata["sha256"], wav.sha256)

    def test_prepare_parameters_are_three_size_hash_pairs_in_contract_order(self) -> None:
        """The later adapter gets evidence, never authority to choose coordinates."""

        wavs = build_all_controlled_wavs()
        parameters = prepare_function_parameters(wavs)
        self.assertEqual(len(parameters), 6)
        self.assertEqual(parameters[::2], (EXPECTED_WAV_SIZE_BYTES,) * 3)
        self.assertEqual(parameters[1::2], tuple(wav.sha256 for wav in wavs))
        self.assertIn("clouddsp_basic_pitch_keda_burst_smoke_prepare", PREPARE_SQL)
        self.assertNotIn("public.jobs", PREPARE_SQL)
        with self.assertRaises(BasicPitchKedaBurstConfigurationError):
            prepare_function_parameters(tuple(reversed(wavs)))

    def test_unknown_coordinate_cannot_generate_a_wav(self) -> None:
        """Coordinates must come from the frozen tuple, not untrusted input."""

        with self.assertRaises(BasicPitchKedaBurstConfigurationError):
            build_controlled_wav(
                type(BURST_COORDINATES[0])(
                    label="vocals",
                    job_id="00000000-0000-4000-8000-000000000000",
                    event_id=BURST_COORDINATES[0].event_id,
                    synthetic_demucs_task_id=BURST_COORDINATES[0].synthetic_demucs_task_id,
                    stem_name="vocals",
                    frequency_hz=440.0,
                )
            )


class SettingsTests(unittest.TestCase):
    """Prove environment input cannot redirect a restricted fixture outward."""

    def test_settings_accept_only_private_service_routes_and_dedicated_identity(self) -> None:
        """Credential values are present only to prove non-secret shape validation."""

        environment = {
            "BASIC_PITCH_KEDA_BURST_SMOKE_DB_NAME": "clouddsp_job_api",
            "BASIC_PITCH_KEDA_BURST_SMOKE_DB_USERNAME": "clouddsp-basic-pitch-keda-burst-smoke",
            "BASIC_PITCH_KEDA_BURST_SMOKE_DB_PASSWORD": "test-password-not-logged",
            "BASIC_PITCH_KEDA_BURST_SMOKE_S3_ACCESS_KEY": "clouddsp-basic-pitch-keda-burst-smoke",
            "BASIC_PITCH_KEDA_BURST_SMOKE_S3_SECRET_KEY": "test-secret-not-logged",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = BurstSettings.from_environment()
        self.assertEqual(settings.database_host, DATABASE_HOST)
        self.assertEqual(settings.minio_endpoint_url, MINIO_ENDPOINT_URL)
        self.assertEqual(settings.completion_timeout_seconds, COMPLETION_TIMEOUT_SECONDS)
        self.assertNotIn("test-password-not-logged", repr(settings))
        self.assertNotIn("test-secret-not-logged", repr(settings))

    def test_rejects_a_database_or_minio_route_override(self) -> None:
        """The test must remain wholly inside the local private Service network."""

        environment = {
            "BASIC_PITCH_KEDA_BURST_SMOKE_DB_NAME": "clouddsp_job_api",
            "BASIC_PITCH_KEDA_BURST_SMOKE_DB_USERNAME": "clouddsp-basic-pitch-keda-burst-smoke",
            "BASIC_PITCH_KEDA_BURST_SMOKE_DB_PASSWORD": "test-password-not-logged",
            "BASIC_PITCH_KEDA_BURST_SMOKE_S3_ACCESS_KEY": "clouddsp-basic-pitch-keda-burst-smoke",
            "BASIC_PITCH_KEDA_BURST_SMOKE_S3_SECRET_KEY": "test-secret-not-logged",
            "BASIC_PITCH_KEDA_BURST_SMOKE_DB_HOST": "postgres.example.invalid",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(BasicPitchKedaBurstConfigurationError):
                BurstSettings.from_environment()
        environment["BASIC_PITCH_KEDA_BURST_SMOKE_DB_HOST"] = DATABASE_HOST
        environment["BASIC_PITCH_KEDA_BURST_SMOKE_MINIO_ENDPOINT_URL"] = "http://minio.example.invalid:9000"
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(BasicPitchKedaBurstConfigurationError):
                BurstSettings.from_environment()


if __name__ == "__main__":
    unittest.main()
