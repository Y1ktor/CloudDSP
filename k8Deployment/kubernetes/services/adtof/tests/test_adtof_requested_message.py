"""Unit tests for the pure ADTOF RabbitMQ request contract.

These tests use only JSON bytes and simple Pika-like property objects. They do
not start a Pod, connect to RabbitMQ/PostgreSQL/MinIO, import ADTOF, or run ML.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.messaging.adtof_requested_message import (
    ADTOF_REQUESTED_ROUTING_KEY,
    MAX_ADTOF_REQUEST_BODY_BYTES,
    PROCESSING_EXCHANGE,
    ADTOFRequestContractError,
    ADTOFRequestedMessage,
    parse_adtof_requested_delivery,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
SHA256 = "a" * 64


def valid_payload(**overrides: object) -> dict[str, object]:
    """Return the exact v004 drums request with focused override support."""

    payload: dict[str, object] = {
        "schema_version": 1,
        "job_id": JOB_ID,
        "stem_name": "drums",
        "stem": {
            "bucket": "clouddsp-uploads",
            "object_key": f"stems/{JOB_ID}/drums.wav",
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
    """Return Pika-like persistent AMQP properties without importing Pika."""

    properties: dict[str, object] = {
        "content_type": "application/json",
        "content_encoding": "utf-8",
        "delivery_mode": 2,
        "type": "adtof.requested",
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
) -> ADTOFRequestedMessage:
    """Parse valid defaults unless a focused test supplies bad evidence."""

    return parse_adtof_requested_delivery(
        delivery_exchange=envelope.get("delivery_exchange", PROCESSING_EXCHANGE),
        delivery_routing_key=envelope.get(
            "delivery_routing_key",
            ADTOF_REQUESTED_ROUTING_KEY,
        ),
        properties=valid_properties() if properties is None else properties,
        body=encoded_payload() if body is None else body,
    )


class ADTOFRequestedMessageTests(unittest.TestCase):
    """Prove only one reviewed drums delivery can enter later worker layers."""

    def test_valid_delivery_returns_validated_private_drums_evidence(self) -> None:
        """No raw body or arbitrary AMQP properties leak across this boundary."""

        self.assertEqual(
            parse(),
            ADTOFRequestedMessage(
                event_id=EVENT_ID,
                job_id=JOB_ID,
                stem_name="drums",
                stem_bucket="clouddsp-uploads",
                stem_object_key=f"stems/{JOB_ID}/drums.wav",
                stem_content_length=1234,
                stem_sha256=SHA256,
            ),
        )

    def test_rejects_wrong_processing_exchange_or_routing_key(self) -> None:
        """Queue delivery must retain the dispatcher-selected route provenance."""

        for envelope in (
            {"delivery_exchange": "clouddsp.source-events"},
            {"delivery_routing_key": "basic-pitch.requested"},
        ):
            with self.subTest(envelope=envelope):
                with self.assertRaises(ADTOFRequestContractError):
                    parse(**envelope)

    def test_rejects_missing_or_incompatible_persistent_amqp_properties(self) -> None:
        """A JSON body alone must not authorize private worker access."""

        for override in (
            {"content_type": "text/plain"},
            {"content_encoding": "UTF-8"},
            {"delivery_mode": 1},
            {"delivery_mode": True},
            {"type": "basic-pitch.requested"},
            {"message_id": EVENT_ID.upper()},
            {"correlation_id": EVENT_ID},
        ):
            with self.subTest(override=override):
                with self.assertRaises(ADTOFRequestContractError):
                    parse(properties=valid_properties(**override))

        with self.assertRaises(ADTOFRequestContractError):
            parse(properties=SimpleNamespace(content_type="application/json"))

    def test_rejects_non_bytes_invalid_duplicate_and_oversized_json(self) -> None:
        """The parser rejects permissive and unbounded body representations."""

        duplicate_member_body = (
            b'{"schema_version":1,"job_id":"08ec1d44-3106-4fcb-91c8-5d0c78e7e046",'
            b'"stem_name":"drums","stem":{"bucket":"clouddsp-uploads",'
            b'"object_key":"stems/08ec1d44-3106-4fcb-91c8-5d0c78e7e046/drums.wav",'
            b'"content_type":"audio/wav","size_bytes":1,"sha256":"'
            + b"a" * 64
            + b'"},"stem_name":"drums"}'
        )
        for body in (
            "not-bytes",
            b"",
            b"{",
            b"\xff",
            b"[]",
            b'{"schema_version":NaN}',
            duplicate_member_body,
            b" " * (MAX_ADTOF_REQUEST_BODY_BYTES + 1),
        ):
            with self.subTest(body_type=type(body).__name__):
                with self.assertRaises(ADTOFRequestContractError):
                    parse(body=body)

    def test_rejects_extra_missing_or_wrongly_typed_body_fields(self) -> None:
        """Version 1 is an exact schema, not a permissive mapping."""

        invalid_overrides = (
            {"extra": "untrusted"},
            {"schema_version": 2},
            {"schema_version": True},
            {"job_id": JOB_ID.upper()},
            {"stem_name": "vocals"},
            {"stem": {"bucket": "clouddsp-uploads"}},
            {
                "stem": {
                    "bucket": "clouddsp-uploads",
                    "object_key": f"stems/{JOB_ID}/drums.wav",
                    "content_type": "audio/wav",
                    "size_bytes": 1,
                    "sha256": SHA256,
                    "extra": 1,
                }
            },
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ADTOFRequestContractError):
                    parse(body=encoded_payload(**overrides))

    def test_rejects_wrong_private_stem_coordinate_or_integrity_evidence(self) -> None:
        """Only a verified Demucs drums WAV coordinate reaches later MinIO I/O."""

        invalid_stems = (
            {"bucket": "another-bucket", "object_key": f"stems/{JOB_ID}/drums.wav", "content_type": "audio/wav", "size_bytes": 1, "sha256": SHA256},
            {"bucket": "clouddsp-uploads", "object_key": f"stems/{JOB_ID}/vocals.wav", "content_type": "audio/wav", "size_bytes": 1, "sha256": SHA256},
            {"bucket": "clouddsp-uploads", "object_key": f"stems/{JOB_ID}/drums.wav", "content_type": "audio/flac", "size_bytes": 1, "sha256": SHA256},
            {"bucket": "clouddsp-uploads", "object_key": f"stems/{JOB_ID}/drums.wav", "content_type": "audio/wav", "size_bytes": True, "sha256": SHA256},
            {"bucket": "clouddsp-uploads", "object_key": f"stems/{JOB_ID}/drums.wav", "content_type": "audio/wav", "size_bytes": 0, "sha256": SHA256},
            {"bucket": "clouddsp-uploads", "object_key": f"stems/{JOB_ID}/drums.wav", "content_type": "audio/wav", "size_bytes": 1, "sha256": "A" * 64},
        )
        for stem in invalid_stems:
            with self.subTest(stem=stem):
                with self.assertRaises(ADTOFRequestContractError):
                    parse(body=encoded_payload(stem=stem))


if __name__ == "__main__":
    unittest.main()
