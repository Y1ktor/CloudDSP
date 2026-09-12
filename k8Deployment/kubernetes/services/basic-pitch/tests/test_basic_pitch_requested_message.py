"""Unit tests for the pure Basic Pitch request/output contract.

The tests use only JSON bytes and Pika-like simple objects. They do not connect
to RabbitMQ, PostgreSQL, MinIO, Docker, Keycloak, or Kubernetes, and they do
not import or execute the Basic Pitch ML package.
"""

from __future__ import annotations

from dataclasses import replace
import json
import unittest
from types import SimpleNamespace

from app.basic_pitch_requested_message import (
    BASIC_PITCH_REQUESTED_ROUTING_KEY,
    BASIC_PITCH_STEM_NAMES,
    MAX_BASIC_PITCH_REQUEST_BODY_BYTES,
    PROCESSING_EXCHANGE,
    BasicPitchMidiOutput,
    BasicPitchRequestContractError,
    BasicPitchRequestedMessage,
    build_basic_pitch_midi_output,
    parse_basic_pitch_requested_delivery,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
SHA256 = "a" * 64


def valid_payload(**overrides: object) -> dict[str, object]:
    """Return the exact v004 Basic Pitch body with focused override support."""

    payload: dict[str, object] = {
        "schema_version": 1,
        "job_id": JOB_ID,
        "stem_name": "vocals",
        "stem": {
            "bucket": "clouddsp-uploads",
            "object_key": f"stems/{JOB_ID}/vocals.wav",
            "content_type": "audio/wav",
            "size_bytes": 1234,
            "sha256": SHA256,
        },
    }
    payload.update(overrides)
    return payload


def encoded_payload(**overrides: object) -> bytes:
    """Encode a compact valid body unless a test changes one input field."""

    return json.dumps(valid_payload(**overrides), separators=(",", ":")).encode("utf-8")


def valid_properties(**overrides: object) -> SimpleNamespace:
    """Return Pika-like persistent properties without importing Pika itself."""

    properties: dict[str, object] = {
        "content_type": "application/json",
        "content_encoding": "utf-8",
        "delivery_mode": 2,
        "type": "basic-pitch.requested",
        "message_id": EVENT_ID,
        "correlation_id": JOB_ID,
    }
    properties.update(overrides)
    return SimpleNamespace(**properties)


def parse(
    *,
    body: object | None = None,
    properties: object | None = None,
    **envelope: object,
) -> BasicPitchRequestedMessage:
    """Parse valid defaults unless a focused test supplies invalid evidence."""

    return parse_basic_pitch_requested_delivery(
        delivery_exchange=envelope.get("delivery_exchange", PROCESSING_EXCHANGE),
        delivery_routing_key=envelope.get(
            "delivery_routing_key",
            BASIC_PITCH_REQUESTED_ROUTING_KEY,
        ),
        properties=valid_properties() if properties is None else properties,
        body=encoded_payload() if body is None else body,
    )


class BasicPitchRequestedMessageTests(unittest.TestCase):
    """Prove only reviewed v004 Basic Pitch input can reach later worker layers."""

    def test_valid_delivery_returns_only_validated_private_stem_evidence(self) -> None:
        """The parser exposes no raw body or arbitrary AMQP metadata."""

        self.assertEqual(
            parse(),
            BasicPitchRequestedMessage(
                event_id=EVENT_ID,
                job_id=JOB_ID,
                stem_name="vocals",
                stem_bucket="clouddsp-uploads",
                stem_object_key=f"stems/{JOB_ID}/vocals.wav",
                stem_content_length=1234,
                stem_sha256=SHA256,
            ),
        )

    def test_all_and_only_approved_non_drum_stems_are_accepted(self) -> None:
        """Basic Pitch must never accept the drums-only ADTOF route."""

        for stem_name in BASIC_PITCH_STEM_NAMES:
            with self.subTest(stem_name=stem_name):
                stem = valid_payload()["stem"]
                assert isinstance(stem, dict)
                stem["object_key"] = f"stems/{JOB_ID}/{stem_name}.wav"
                self.assertEqual(
                    parse(body=encoded_payload(stem_name=stem_name, stem=stem)).stem_name,
                    stem_name,
                )

        for stem_name in ("drums", "unknown", "", "VOCALS"):
            with self.subTest(rejected_stem_name=stem_name):
                with self.assertRaises(BasicPitchRequestContractError):
                    parse(body=encoded_payload(stem_name=stem_name))

    def test_rejects_wrong_processing_exchange_or_routing_key(self) -> None:
        """A queue delivery must retain the approved direct-exchange provenance."""

        for envelope in (
            {"delivery_exchange": "clouddsp.source-events"},
            {"delivery_routing_key": "adtof.requested"},
        ):
            with self.subTest(envelope=envelope):
                with self.assertRaises(BasicPitchRequestContractError):
                    parse(**envelope)

    def test_rejects_missing_or_incompatible_persistent_amqp_properties(self) -> None:
        """A body alone cannot authorize a private worker read."""

        for override in (
            {"content_type": "text/plain"},
            {"content_encoding": "UTF-8"},
            {"delivery_mode": 1},
            {"delivery_mode": True},
            {"type": "adtof.requested"},
            {"message_id": EVENT_ID.upper()},
            {"correlation_id": EVENT_ID},
        ):
            with self.subTest(override=override):
                with self.assertRaises(BasicPitchRequestContractError):
                    parse(properties=valid_properties(**override))

        with self.assertRaises(BasicPitchRequestContractError):
            parse(properties=SimpleNamespace(content_type="application/json"))

    def test_rejects_non_bytes_invalid_duplicate_and_oversized_json(self) -> None:
        """The parser avoids permissive or unbounded message decoding."""

        duplicate_member_body = (
            b'{"schema_version":1,"job_id":"08ec1d44-3106-4fcb-91c8-5d0c78e7e046",'
            b'"stem_name":"vocals","stem":{"bucket":"clouddsp-uploads",'
            b'"object_key":"stems/08ec1d44-3106-4fcb-91c8-5d0c78e7e046/vocals.wav",'
            b'"content_type":"audio/wav","size_bytes":1,"sha256":"'
            + b"a" * 64
            + b'"},"stem_name":"vocals"}'
        )
        for body in (
            "not-bytes",
            b"",
            b"{",
            b"\xff",
            b"[]",
            b'{"schema_version":NaN}',
            duplicate_member_body,
            b" " * (MAX_BASIC_PITCH_REQUEST_BODY_BYTES + 1),
        ):
            with self.subTest(body_type=type(body).__name__):
                with self.assertRaises(BasicPitchRequestContractError):
                    parse(body=body)

    def test_rejects_extra_missing_or_wrongly_typed_body_fields(self) -> None:
        """Version 1 remains an exact schema rather than a permissive mapping."""

        invalid_overrides = (
            {"extra": "untrusted"},
            {"schema_version": 2},
            {"schema_version": True},
            {"job_id": JOB_ID.upper()},
            {"stem": {"bucket": "clouddsp-uploads"}},
            {
                "stem": {
                    "bucket": "clouddsp-uploads",
                    "object_key": f"stems/{JOB_ID}/vocals.wav",
                    "content_type": "audio/wav",
                    "size_bytes": 1,
                    "sha256": SHA256,
                    "extra": 1,
                }
            },
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(BasicPitchRequestContractError):
                    parse(body=encoded_payload(**overrides))

    def test_rejects_wrong_stem_bucket_key_type_size_or_checksum(self) -> None:
        """Only a verified Demucs WAV coordinate/evidence can enter later MinIO I/O."""

        invalid_stems = (
            {"bucket": "another-bucket", "object_key": f"stems/{JOB_ID}/vocals.wav", "content_type": "audio/wav", "size_bytes": 1, "sha256": SHA256},
            {"bucket": "clouddsp-uploads", "object_key": f"stems/{JOB_ID}/bass.wav", "content_type": "audio/wav", "size_bytes": 1, "sha256": SHA256},
            {"bucket": "clouddsp-uploads", "object_key": f"stems/{JOB_ID}/vocals.wav", "content_type": "audio/flac", "size_bytes": 1, "sha256": SHA256},
            {"bucket": "clouddsp-uploads", "object_key": f"stems/{JOB_ID}/vocals.wav", "content_type": "audio/wav", "size_bytes": True, "sha256": SHA256},
            {"bucket": "clouddsp-uploads", "object_key": f"stems/{JOB_ID}/vocals.wav", "content_type": "audio/wav", "size_bytes": 0, "sha256": SHA256},
            {"bucket": "clouddsp-uploads", "object_key": f"stems/{JOB_ID}/vocals.wav", "content_type": "audio/wav", "size_bytes": 1, "sha256": "A" * 64},
        )
        for stem in invalid_stems:
            with self.subTest(stem=stem):
                with self.assertRaises(BasicPitchRequestContractError):
                    parse(body=encoded_payload(stem=stem))


class BasicPitchMidiOutputTests(unittest.TestCase):
    """Prove all accepted stems map to one deterministic private MIDI key."""

    def test_maps_each_approved_stem_to_cloud_compatible_midi_key(self) -> None:
        """Recovery never creates attempt-specific/browsable output coordinates."""

        for stem_name in BASIC_PITCH_STEM_NAMES:
            with self.subTest(stem_name=stem_name):
                stem = valid_payload()["stem"]
                assert isinstance(stem, dict)
                stem["object_key"] = f"stems/{JOB_ID}/{stem_name}.wav"
                output = build_basic_pitch_midi_output(
                    parse(body=encoded_payload(stem_name=stem_name, stem=stem))
                )
                self.assertEqual(
                    output,
                    BasicPitchMidiOutput(
                        request_event_id=EVENT_ID,
                        job_id=JOB_ID,
                        stem_name=stem_name,
                        bucket="clouddsp-uploads",
                        object_key=f"midi/{JOB_ID}/{stem_name}.mid",
                        content_type="audio/midi",
                        input_stem_sha256=SHA256,
                    ),
                )

    def test_revalidates_a_hand_built_message_before_naming_output(self) -> None:
        """Frozen dataclasses must not become an arbitrary bucket/key capability."""

        valid_message = parse()
        for invalid_message in (
            replace(valid_message, stem_name="drums"),
            replace(valid_message, stem_object_key=f"stems/{JOB_ID}/other.wav"),
            replace(valid_message, stem_bucket="other-bucket"),
            replace(valid_message, stem_content_length=0),
            replace(valid_message, stem_sha256="not-a-sha256"),
        ):
            with self.subTest(invalid_message=invalid_message):
                with self.assertRaises(BasicPitchRequestContractError):
                    build_basic_pitch_midi_output(invalid_message)


if __name__ == "__main__":
    unittest.main()
