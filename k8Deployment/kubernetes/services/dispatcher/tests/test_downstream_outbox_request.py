"""Unit tests for the post-Demucs durable-outbox-to-AMQP request contract.

These tests exercise only pure standard-library validation and JSON encoding.
They do not open PostgreSQL or RabbitMQ, import Pika, create a queue, publish a
message, use Kubernetes, or access a MinIO object.
"""

from __future__ import annotations

import json
import unittest

from app.downstream_outbox_request import (
    ADTOF_REQUESTED_ROUTING_KEY,
    BASIC_PITCH_REQUESTED_ROUTING_KEY,
    PROCESSING_EXCHANGE,
    DownstreamOutboxRequestContractError,
    build_downstream_amqp_request,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


def payload(*, stem_name: str, **overrides: object) -> dict[str, object]:
    """Return one exact private-WAV v004 payload for the requested stem."""

    value: dict[str, object] = {
        "schema_version": 1,
        "job_id": JOB_ID,
        "stem_name": stem_name,
        "stem": {
            "bucket": "clouddsp-uploads",
            "object_key": f"stems/{JOB_ID}/{stem_name}.wav",
            "content_type": "audio/wav",
            "size_bytes": 1234,
            "sha256": "a" * 64,
        },
    }
    value.update(overrides)
    return value


def build(*, stage: str, stem_name: str, event_type: str, body: object | None = None):
    """Call the public builder with the normal valid identity tuple by default."""

    return build_downstream_amqp_request(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        stage=stage,
        stem_name=stem_name,
        event_type=event_type,
        payload=payload(stem_name=stem_name) if body is None else body,
    )


class DownstreamOutboxRequestTests(unittest.TestCase):
    """Prove route selection, AMQP metadata, and fail-closed durable validation."""

    def test_each_reviewed_non_drum_stem_routes_only_to_basic_pitch(self) -> None:
        """All 2/4/6-stem non-drum outputs keep their own deterministic route."""

        for stem_name in ("vocals", "no_vocals", "bass", "other", "guitar", "piano"):
            with self.subTest(stem_name=stem_name):
                request = build(
                    stage="basic-pitch",
                    stem_name=stem_name,
                    event_type="basic-pitch.requested",
                )
                self.assertEqual(request.exchange, PROCESSING_EXCHANGE)
                self.assertEqual(request.routing_key, BASIC_PITCH_REQUESTED_ROUTING_KEY)
                self.assertEqual(request.message_type, "basic-pitch.requested")
                self.assertEqual(request.message_id, EVENT_ID)
                self.assertEqual(request.correlation_id, JOB_ID)
                self.assertEqual(request.content_type, "application/json")
                self.assertEqual(request.content_encoding, "utf-8")
                self.assertEqual(request.delivery_mode, 2)
                self.assertEqual(json.loads(request.body.decode("utf-8")), payload(stem_name=stem_name))

    def test_drums_route_only_to_adtof(self) -> None:
        """A drum stem cannot accidentally enter the Basic Pitch work class."""

        request = build(
            stage="adtof",
            stem_name="drums",
            event_type="adtof.requested",
        )

        self.assertEqual(request.routing_key, ADTOF_REQUESTED_ROUTING_KEY)
        self.assertEqual(request.message_type, "adtof.requested")
        self.assertEqual(json.loads(request.body.decode("utf-8")), payload(stem_name="drums"))

    def test_stage_stem_and_type_mismatch_never_yield_a_route(self) -> None:
        """The dispatcher refuses a widened or crossed database vocabulary."""

        cases = (
            ("basic-pitch", "drums", "basic-pitch.requested"),
            ("adtof", "vocals", "adtof.requested"),
            ("basic-pitch", "bass", "adtof.requested"),
            ("demucs", "", "demucs.requested"),
        )
        for stage, stem_name, event_type in cases:
            with self.subTest(stage=stage, stem_name=stem_name, event_type=event_type):
                with self.assertRaises(DownstreamOutboxRequestContractError):
                    build(stage=stage, stem_name=stem_name, event_type=event_type)

    def test_private_stem_evidence_must_exactly_match_its_durable_route(self) -> None:
        """No browser data, other key, wrong type, or malformed hash reaches AMQP."""

        invalid_bodies = (
            payload(stem_name="bass", owner_sub="not-an-amqp-field"),
            payload(
                stem_name="bass",
                stem={
                    "bucket": "clouddsp-uploads",
                    "object_key": f"stems/{JOB_ID}/other.wav",
                    "content_type": "audio/wav",
                    "size_bytes": 1234,
                    "sha256": "a" * 64,
                },
            ),
            payload(
                stem_name="bass",
                stem={
                    "bucket": "clouddsp-uploads",
                    "object_key": f"stems/{JOB_ID}/bass.wav",
                    "content_type": "audio/mpeg",
                    "size_bytes": 1234,
                    "sha256": "a" * 64,
                },
            ),
            payload(
                stem_name="bass",
                stem={
                    "bucket": "clouddsp-uploads",
                    "object_key": f"stems/{JOB_ID}/bass.wav",
                    "content_type": "audio/wav",
                    "size_bytes": 1234,
                    "sha256": "invalid",
                },
            ),
        )
        for body in invalid_bodies:
            with self.subTest(body=body):
                with self.assertRaises(DownstreamOutboxRequestContractError):
                    build(
                        stage="basic-pitch",
                        stem_name="bass",
                        event_type="basic-pitch.requested",
                        body=body,
                    )

    def test_noncanonical_event_or_job_identity_is_not_published(self) -> None:
        """AMQP correlation cannot disagree with the canonical durable UUIDs."""

        with self.assertRaises(DownstreamOutboxRequestContractError):
            build_downstream_amqp_request(
                event_id=EVENT_ID.upper(),
                job_id=JOB_ID,
                stage="basic-pitch",
                stem_name="vocals",
                event_type="basic-pitch.requested",
                payload=payload(stem_name="vocals"),
            )
        with self.assertRaises(DownstreamOutboxRequestContractError):
            build_downstream_amqp_request(
                event_id=EVENT_ID,
                job_id=JOB_ID,
                stage="basic-pitch",
                stem_name="vocals",
                event_type="basic-pitch.requested",
                payload=payload(stem_name="vocals", job_id=EVENT_ID),
            )


if __name__ == "__main__":
    unittest.main()
