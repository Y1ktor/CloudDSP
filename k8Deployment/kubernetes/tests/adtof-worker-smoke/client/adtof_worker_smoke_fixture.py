"""Define the fixed, source-only ADTOF worker-smoke input fixture.

This module deliberately has no Boto3, Psycopg, RabbitMQ, ADTOF-model, Docker,
or Kubernetes dependency. A later one-shot smoke client will import these
constants and helpers to upload one contract-valid synthetic Demucs drums WAV
*before* it asks a scoped PostgreSQL function to create durable work.

Keeping the fixture pure gives the future worker smoke one repeatable input
without accepting a browser file, listing MinIO, or widening a credential to
arbitrary Jobs or object prefixes.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import struct
import wave
from dataclasses import dataclass
from uuid import UUID


# These reserved UUIDs identify only the ADTOF smoke fixture. They are
# intentionally unrelated to Basic Pitch's smoke coordinates and must never be
# accepted from an environment variable, a browser request, or a RabbitMQ body.
# Fixed coordinates let future database and MinIO smoke identities grant access
# to exactly three keys/rows instead of a broad production prefix.
SMOKE_JOB_ID = "8d94ca4d-8f27-4d78-a01f-73ee0fb8c6bc"
SMOKE_EVENT_ID = "0b37b59f-3634-47de-97f0-5a1f3e27ea2f"
SYNTHETIC_DEMUCS_TASK_ID = "c0a24de2-01d0-4d51-a24d-87f76c30f47c"
SMOKE_OWNER_SUB = "clouddsp-adtof-worker-smoke"

# These are the exact durable coordinates expected by the deployed worker's
# parser, first-claim SQL, MinIO verifier, output planner, and completion path.
# The worker-created ADTOF processing-task ID remains deliberately dynamic: it
# is created only after the real worker has claimed the published event.
UPLOADS_BUCKET = "clouddsp-uploads"
STAGE = "adtof"
STEM_NAME = "drums"
STEM_MODE = "4-stems"
STEM_KEY = f"stems/{SMOKE_JOB_ID}/{STEM_NAME}.wav"
SOURCE_KEY = f"uploads/{SMOKE_JOB_ID}/adtof-worker-smoke.wav"
MIDI_KEY = f"midi/{SMOKE_JOB_ID}/drums.mid"
TEMPO_KEY = f"midi/{SMOKE_JOB_ID}/drums_bpm.json"
WAV_CONTENT_TYPE = "audio/wav"
MIDI_CONTENT_TYPE = "audio/midi"
TEMPO_CONTENT_TYPE = "application/json"
FIXED_OBJECT_KEYS = frozenset({STEM_KEY, MIDI_KEY, TEMPO_KEY})

# A four-second, 44.1 kHz mono PCM pattern contains eight quarter-note kicks,
# four snares, and sixteen hi-hat-like clicks at 120 BPM. It is long enough to
# exercise ADTOF's short-clip tempo path (which requires at least four beats)
# and remains below this smoke test's one-mebibyte self-limit. The later test
# proves durable handling and valid output formats; it intentionally does not
# assert the model's musical note count or exact BPM as an accuracy benchmark.
WAV_SAMPLE_RATE = 44_100
WAV_DURATION_SECONDS = 4
WAV_CHANNEL_COUNT = 1
WAV_SAMPLE_WIDTH_BYTES = 2
WAV_BEATS_PER_MINUTE = 120
WAV_SIXTEENTH_NOTE_SECONDS = 0.25
MAX_SMOKE_WAV_BYTES = 1 * 1024 * 1024

_DEMUCS_STEM_METADATA_NAMES = frozenset(
    {
        "schema-version",
        "producer",
        "job-id",
        "task-id",
        "stem-name",
        "stem-mode",
        "size-bytes",
        "sha256",
    }
)


@dataclass(frozen=True)
class ControlledDrumWav:
    """One deterministic byte sequence plus the only durable input evidence.

    The raw bytes remain a future smoke client's temporary upload body. The
    size, digest, and metadata bind the private object to this fixed Job and
    synthetic upstream Demucs-task provenance; no URL, credential, or model
    result is represented here.
    """

    wav_bytes: bytes
    size_bytes: int
    sha256: str
    metadata: dict[str, str]


def _require_canonical_uuid(value: str) -> None:
    """Fail fast if an edited fixture constant loses canonical UUID spelling."""

    try:
        parsed = str(UUID(value))
    except (TypeError, ValueError) as error:
        raise RuntimeError("ADTOF smoke fixture UUID is invalid.") from error
    if parsed != value:
        raise RuntimeError("ADTOF smoke fixture UUID is invalid.")


def _drum_sample(*, frame_index: int) -> float:
    """Return one deterministic, bounded synthetic drum-pattern sample.

    This is not a drum synthesizer or an attempt to imitate a Demucs result.
    It provides a repeatable, percussive waveform with a clear 120-BPM pulse so
    the real ADTOF model sees a valid WAV rather than silence, user media, or a
    tone that is unrelated to the worker's drums-only contract.
    """

    step_frames = int(WAV_SAMPLE_RATE * WAV_SIXTEENTH_NOTE_SECONDS)
    step_index, offset_frames = divmod(frame_index, step_frames)
    elapsed_seconds = offset_frames / WAV_SAMPLE_RATE
    value = 0.0

    # Every second sixteenth is a quarter-note kick. Its falling frequency and
    # exponential envelope make a compact low-frequency transient.
    if step_index % 2 == 0 and elapsed_seconds < 0.10:
        envelope = math.exp(-26.0 * elapsed_seconds)
        phase = 2.0 * math.pi * (110.0 * elapsed_seconds - 32.0 * elapsed_seconds**2)
        value += 0.70 * envelope * math.sin(phase)

    # The third sixteenth of each beat is a deterministic high-frequency snare
    # surrogate. Several fixed sines avoid randomness, preserving byte-for-byte
    # reproducibility while still differing from the kick's low-frequency cue.
    if step_index % 4 == 2 and elapsed_seconds < 0.08:
        envelope = math.exp(-34.0 * elapsed_seconds)
        snare = (
            math.sin(2.0 * math.pi * 1_800.0 * elapsed_seconds)
            + math.sin(2.0 * math.pi * 2_300.0 * elapsed_seconds)
            + math.sin(2.0 * math.pi * 3_300.0 * elapsed_seconds)
        ) / 3.0
        value += 0.38 * envelope * snare

    # Every sixteenth gets a very short click. This supplies sixteen evenly
    # spaced transients over four seconds without adding a non-deterministic
    # noise generator or requiring an audio fixture file in Git.
    if elapsed_seconds < 0.025:
        envelope = math.exp(-85.0 * elapsed_seconds)
        click = (
            math.sin(2.0 * math.pi * 5_000.0 * elapsed_seconds)
            + math.sin(2.0 * math.pi * 7_500.0 * elapsed_seconds)
        ) / 2.0
        value += 0.20 * envelope * click

    # The three deterministic layers can overlap at an onset. Clamp before
    # converting to signed 16-bit PCM so fixture generation never overflows.
    return max(-1.0, min(1.0, value))


def build_controlled_drum_wav() -> bytes:
    """Build one valid, bounded, deterministic mono PCM WAV in memory.

    No filesystem path, input parameter, random seed, network call, or model
    dependency participates in construction. A later smoke client can upload
    these bytes directly to the one fixed MinIO key after its absence checks.
    """

    total_frames = WAV_SAMPLE_RATE * WAV_DURATION_SECONDS
    pcm_frames = bytearray()
    for frame_index in range(total_frames):
        sample = int(_drum_sample(frame_index=frame_index) * 32_767)
        pcm_frames.extend(struct.pack("<h", sample))

    destination = io.BytesIO()
    with wave.open(destination, "wb") as writer:
        writer.setnchannels(WAV_CHANNEL_COUNT)
        writer.setsampwidth(WAV_SAMPLE_WIDTH_BYTES)
        writer.setframerate(WAV_SAMPLE_RATE)
        writer.writeframes(bytes(pcm_frames))
    result = destination.getvalue()
    if (
        not 44 <= len(result) <= MAX_SMOKE_WAV_BYTES
        or result[:4] != b"RIFF"
        or result[8:12] != b"WAVE"
    ):
        raise RuntimeError("ADTOF controlled drum WAV construction is invalid.")
    return result


def controlled_stem_metadata(*, size_bytes: int, sha256: str) -> dict[str, str]:
    """Return exactly the Demucs provenance that ADTOF validates before download."""

    if not 1 <= size_bytes <= MAX_SMOKE_WAV_BYTES or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("ADTOF controlled drum evidence is invalid.")
    for identifier in (SMOKE_JOB_ID, SMOKE_EVENT_ID, SYNTHETIC_DEMUCS_TASK_ID):
        _require_canonical_uuid(identifier)
    return {
        "schema-version": "1",
        "producer": "demucs",
        "job-id": SMOKE_JOB_ID,
        "task-id": SYNTHETIC_DEMUCS_TASK_ID,
        "stem-name": STEM_NAME,
        "stem-mode": STEM_MODE,
        "size-bytes": str(size_bytes),
        "sha256": sha256,
    }


def build_controlled_drum_fixture() -> ControlledDrumWav:
    """Return bytes and matching fixed-coordinate evidence for a later smoke client."""

    wav_bytes = build_controlled_drum_wav()
    sha256 = hashlib.sha256(wav_bytes).hexdigest()
    metadata = controlled_stem_metadata(size_bytes=len(wav_bytes), sha256=sha256)
    if set(metadata) != _DEMUCS_STEM_METADATA_NAMES:
        raise RuntimeError("ADTOF controlled drum metadata is incomplete.")
    return ControlledDrumWav(
        wav_bytes=wav_bytes,
        size_bytes=len(wav_bytes),
        sha256=sha256,
        metadata=metadata,
    )
