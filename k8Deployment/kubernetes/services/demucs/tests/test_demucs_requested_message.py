"""Unit tests for the pure Demucs RabbitMQ request parser.

The tests open no broker socket, query no PostgreSQL database, access no MinIO
object, create no Kubernetes resource, and load no ML model. They prove the
first safety boundary before a future consumer can claim durable work.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.messaging.demucs_requested_message import (
    DEMUCS_REQUESTED_ROUTING_KEY,
    MAX_DEMUCS_REQUEST_BODY_BYTES,
    PROCESSING_EXCHANGE,
    DemucsRequestContractError,
    DemucsRequestedMessage,
    parse_demucs_requested_delivery,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


def valid_payload(**overrides: object) -> dict[str, object]:
    """Return one precise version-1 body, with optional invalid-case changes."""

    payload: dict[str, object] = {
        "schema_version": 1,
        "job_id": JOB_ID,
        "source": {
            "bucket": "clouddsp-uploads",
            "object_key": f"uploads/{JOB_ID}/mix.wav",
        },
        "stem_mode": "4-stems",
    }
    payload.update(overrides)
    return payload


def encoded_payload(**overrides: object) -> bytes:
    """Encode a normal test body as compact JSON publisher bytes."""

    return json.dumps(valid_payload(**overrides), separators=(",", ":")).encode("utf-8")


def valid_properties(**overrides: object) -> SimpleNamespace:
    """Return Pika-like AMQP properties without importing the Pika package."""

    properties: dict[str, object] = {
        "content_type": "application/json",
        "content_encoding": "utf-8",
        "delivery_mode": 2,
        "type": "demucs.requested",
        "message_id": EVENT_ID,
        "correlation_id": JOB_ID,
    }
    properties.update(overrides)
    return SimpleNamespace(**properties)


def parse(*, body: object | None = None, properties: object | None = None, **envelope: object) -> DemucsRequestedMessage:
    """Parse valid defaults unless one focused test supplies an invalid part."""

    return parse_demucs_requested_delivery(
        delivery_exchange=envelope.get("delivery_exchange", PROCESSING_EXCHANGE),
        delivery_routing_key=envelope.get("delivery_routing_key", DEMUCS_REQUESTED_ROUTING_KEY),
        properties=valid_properties() if properties is None else properties,
        body=encoded_payload() if body is None else body,
    )


class DemucsRequestedMessageTests(unittest.TestCase):
    """Prove only the frozen version-1 broker contract enters later worker code."""

    def test_valid_delivery_returns_only_validated_durable_identifiers(self) -> None:
        """The result contains no raw body, token, or arbitrary broker metadata."""

        self.assertEqual(
            parse(),
            DemucsRequestedMessage(
                event_id=EVENT_ID,
                job_id=JOB_ID,
                source_bucket="clouddsp-uploads",
                source_object_key=f"uploads/{JOB_ID}/mix.wav",
                stem_mode="4-stems",
            ),
        )

    def test_all_supported_stem_modes_are_accepted(self) -> None:
        """The parser remains aligned with the direct-upload contract."""

        for stem_mode in ("2-stems", "4-stems", "6-stems"):
            with self.subTest(stem_mode=stem_mode):
                self.assertEqual(parse(body=encoded_payload(stem_mode=stem_mode)).stem_mode, stem_mode)

    def test_rejects_wrong_delivery_exchange_or_routing_key(self) -> None:
        """A private queue alone is insufficient: its origin route must match."""

        for envelope in (
            {"delivery_exchange": "clouddsp.source-events"},
            {"delivery_routing_key": "source.upload.created"},
        ):
            with self.subTest(envelope=envelope):
                with self.assertRaises(DemucsRequestContractError):
                    parse(**envelope)

    def test_rejects_missing_or_incompatible_amqp_properties(self) -> None:
        """Wrong broker metadata cannot become valid through permissive defaults."""

        for override in (
            {"content_type": "text/plain"},
            {"content_encoding": "UTF-8"},
            {"delivery_mode": 1},
            {"delivery_mode": True},
            {"type": "different.request"},
            {"message_id": EVENT_ID.upper()},
            {"correlation_id": EVENT_ID},
        ):
            with self.subTest(override=override):
                with self.assertRaises(DemucsRequestContractError):
                    parse(properties=valid_properties(**override))

        with self.assertRaises(DemucsRequestContractError):
            parse(properties=SimpleNamespace(content_type="application/json"))

    def test_rejects_non_bytes_invalid_json_and_oversized_bodies(self) -> None:
        """Metadata parsing never accepts text/object payloads or large uploads."""

        for body in (
            "not-bytes",
            b"",
            b"{",
            b"\xff",
            b"[]",
            b'{"schema_version":NaN}',
            b" " * (MAX_DEMUCS_REQUEST_BODY_BYTES + 1),
        ):
            with self.subTest(body_type=type(body).__name__):
                with self.assertRaises(DemucsRequestContractError):
                    parse(body=body)

    def test_rejects_duplicate_json_members(self) -> None:
        """Two JSON decoders cannot disagree which identity field is authoritative."""

        duplicate_top_level = (
            b'{"schema_version":1,"schema_version":1,'
            b'"job_id":"08ec1d44-3106-4fcb-91c8-5d0c78e7e046",'
            b'"source":{"bucket":"clouddsp-uploads",'
            b'"object_key":"uploads/08ec1d44-3106-4fcb-91c8-5d0c78e7e046/mix.wav"},'
            b'"stem_mode":"4-stems"}'
        )
        duplicate_nested = (
            b'{"schema_version":1,'
            b'"job_id":"08ec1d44-3106-4fcb-91c8-5d0c78e7e046",'
            b'"source":{"bucket":"clouddsp-uploads","bucket":"clouddsp-uploads",'
            b'"object_key":"uploads/08ec1d44-3106-4fcb-91c8-5d0c78e7e046/mix.wav"},'
            b'"stem_mode":"4-stems"}'
        )
        for body in (duplicate_top_level, duplicate_nested):
            with self.subTest(body_prefix=body[:30]):
                with self.assertRaises(DemucsRequestContractError):
                    parse(body=body)

    def test_rejects_extra_missing_or_wrongly_typed_body_fields(self) -> None:
        """Version 1 accepts one narrow schema instead of guessing a future one."""

        for overrides in (
            {"extra": "untrusted"},
            {"schema_version": 2},
            {"schema_version": True},
            {"job_id": JOB_ID.upper()},
            {"job_id": "not-a-uuid"},
            {"source": {"bucket": "clouddsp-uploads"}},
            {"source": {"bucket": "clouddsp-uploads", "object_key": f"uploads/{JOB_ID}/mix.wav", "extra": 1}},
            {"stem_mode": "all-stems"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(DemucsRequestContractError):
                    parse(body=encoded_payload(**overrides))

    def test_rejects_wrong_bucket_or_unsafe_source_prefix(self) -> None:
        """Another job/output key is refused before any MinIO client runs."""

        for source in (
            {"bucket": "another-bucket", "object_key": f"uploads/{JOB_ID}/mix.wav"},
            {"bucket": "clouddsp-uploads", "object_key": f"uploads/{EVENT_ID}/mix.wav"},
            {"bucket": "clouddsp-uploads", "object_key": f"uploads/{JOB_ID}/nested/mix.wav"},
            {"bucket": "clouddsp-uploads", "object_key": f"uploads/{JOB_ID}/"},
            {"bucket": "clouddsp-uploads", "object_key": f"stems/{JOB_ID}/vocals.wav"},
        ):
            with self.subTest(source=source):
                with self.assertRaises(DemucsRequestContractError):
                    parse(body=encoded_payload(source=source))

    def test_rejects_control_text_in_source_fields(self) -> None:
        """Control text cannot become durable private object-coordinate state."""

        for source in (
            {"bucket": "clouddsp-uploads\n", "object_key": f"uploads/{JOB_ID}/mix.wav"},
            {"bucket": "clouddsp-uploads", "object_key": f"uploads/{JOB_ID}/mix\u0000.wav"},
        ):
            with self.subTest(source=source):
                with self.assertRaises(DemucsRequestContractError):
                    parse(body=encoded_payload(source=source))


if __name__ == "__main__":
    unittest.main()
