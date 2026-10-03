"""Create the same bounded per-stem BPM evidence used by cloud Basic Pitch.

The MIDI model does not itself define a reliable song-wide tempo. CloudDSP's
cloud worker therefore runs librosa's beat tracker on the input stem, stores
the result as a *candidate*, and lets the durable job workflow decide whether
that evidence is credible enough to influence the final BPM. This local
adapter follows that contract without making a database, object-store, broker,
or Kubernetes request.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any


MIN_BEAT_COUNT = 8
MIN_SHORT_CLIP_BEAT_COUNT = 4
MIN_BEAT_INTERVAL_CONSISTENCY = 0.70


@dataclass(frozen=True)
class BasicPitchTempoCandidate:
    """Small serializable estimate plus evidence needed by job-level voting."""

    bpm: float | None
    beat_count: int
    duration_seconds: float
    interval_consistency: float
    credible: bool
    confidence: str
    source: str = "librosa_stem"

    def as_payload(self) -> dict[str, object]:
        """Return the exact JSON object persisted beside this stem's MIDI row."""

        return {
            "bpm": self.bpm,
            "beat_count": self.beat_count,
            "duration_seconds": self.duration_seconds,
            "interval_consistency": self.interval_consistency,
            "credible": self.credible,
            "confidence": self.confidence,
            "source": self.source,
        }


def _finite_float(value: object) -> float | None:
    """Convert ordinary/numpy scalar values without accepting NaN or infinity."""

    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def candidate_from_measurements(
    *,
    bpm: object,
    beat_times: object,
    duration_seconds: object,
) -> BasicPitchTempoCandidate:
    """Apply cloud's beat-count and interval-consistency credibility checks.

    This pure function is kept separate from librosa so the exact eligibility
    rules can be reviewed independently from audio decoding/model execution.
    The optional array conversion uses ``tolist`` when provided by numpy, then
    treats the resulting sequence exactly like an ordinary Python list.
    """

    tempo = _finite_float(bpm)
    duration = _finite_float(duration_seconds)
    if duration is None or duration < 0:
        duration = 0.0
    if hasattr(beat_times, "tolist"):
        beat_times = beat_times.tolist()
    if not isinstance(beat_times, (list, tuple)):
        beat_times = (beat_times,)

    beats = [_finite_float(value) for value in beat_times]
    valid_beats = [value for value in beats if value is not None]
    # A non-finite beat marker means the analysis evidence itself is suspect;
    # retaining the candidate as non-credible mirrors the cloud fallback path.
    all_beats_finite = len(valid_beats) == len(beats)
    intervals = [right - left for left, right in zip(valid_beats, valid_beats[1:])]
    if intervals:
        median_interval = median(intervals)
        tolerance = max(0.04, median_interval * 0.12)
        consistency = sum(abs(interval - median_interval) <= tolerance for interval in intervals) / len(intervals)
    else:
        consistency = 0.0

    beat_count = len(valid_beats)
    has_valid_tempo = tempo is not None and tempo > 0
    minimum_beats = MIN_SHORT_CLIP_BEAT_COUNT if duration < 20 else MIN_BEAT_COUNT
    credible = bool(
        all_beats_finite
        and has_valid_tempo
        and beat_count >= minimum_beats
        and consistency >= MIN_BEAT_INTERVAL_CONSISTENCY
    )
    return BasicPitchTempoCandidate(
        bpm=round(tempo, 2) if has_valid_tempo else None,
        beat_count=beat_count,
        duration_seconds=round(duration, 2),
        interval_consistency=round(consistency, 3),
        credible=credible,
        confidence="medium" if credible else "low",
    )


def estimate_basic_pitch_tempo_candidate(audio_path: Path) -> BasicPitchTempoCandidate:
    """Run librosa on the already-verified temporary stem and return evidence.

    BPM analysis is deliberately best-effort: a decoder/beat-tracker failure
    must not turn a successful MIDI extraction into a failed processing task.
    Instead it yields an explicitly non-credible candidate, allowing another
    stem (or the preferred ADTOF drum estimate) to determine song tempo.
    Imports happen only when used so worker startup does not load librosa
    before a MIDI task arrives.
    """

    if not isinstance(audio_path, Path):
        raise TypeError("audio_path must be pathlib.Path.")
    try:
        import librosa

        audio, sample_rate = librosa.load(str(audio_path))
        estimated_tempo, beat_times = librosa.beat.beat_track(
            y=audio,
            sr=sample_rate,
            units="time",
        )
        if hasattr(estimated_tempo, "reshape"):
            tempo_values = estimated_tempo.reshape(-1).tolist()
        elif isinstance(estimated_tempo, (list, tuple)):
            tempo_values = estimated_tempo
        else:
            tempo_values = [estimated_tempo]
        resolved_tempo = tempo_values[0] if tempo_values else None
        if hasattr(beat_times, "reshape"):
            beat_times = beat_times.reshape(-1)
        sample_count = len(audio)
        duration = sample_count / sample_rate if sample_rate else 0.0
        return candidate_from_measurements(
            bpm=resolved_tempo,
            beat_times=beat_times,
            duration_seconds=duration,
        )
    except Exception:
        # Keep vendor/decoder diagnostics out of durable job state. The worker
        # supervisor still sees MIDI extraction as successful; the selected
        # top-level BPM records this candidate as ineligible evidence.
        return candidate_from_measurements(
            bpm=None,
            beat_times=(),
            duration_seconds=0.0,
        )


def validate_basic_pitch_tempo_candidate(value: object) -> BasicPitchTempoCandidate:
    """Recheck the exact candidate shape before it enters the SQL call."""

    if not isinstance(value, BasicPitchTempoCandidate):
        raise TypeError("tempo_candidate must be BasicPitchTempoCandidate.")
    bpm = value.bpm
    if bpm is not None and (type(bpm) not in (float, int) or not math.isfinite(float(bpm)) or bpm <= 0):
        raise ValueError("tempo candidate BPM is invalid.")
    if (
        type(value.beat_count) is not int
        or value.beat_count < 0
        or type(value.duration_seconds) not in (float, int)
        or not math.isfinite(float(value.duration_seconds))
        or value.duration_seconds < 0
        or type(value.interval_consistency) not in (float, int)
        or not math.isfinite(float(value.interval_consistency))
        or not 0 <= value.interval_consistency <= 1
        or type(value.credible) is not bool
        or value.confidence not in {"low", "medium"}
        or value.source != "librosa_stem"
    ):
        raise ValueError("tempo candidate evidence is invalid.")
    minimum_beats = MIN_SHORT_CLIP_BEAT_COUNT if value.duration_seconds < 20 else MIN_BEAT_COUNT
    expected_credible = bool(
        bpm is not None
        and value.beat_count >= minimum_beats
        and value.interval_consistency >= MIN_BEAT_INTERVAL_CONSISTENCY
    )
    if value.credible is not expected_credible or value.confidence != ("medium" if expected_credible else "low"):
        raise ValueError("tempo candidate credibility does not match its evidence.")
    return value
