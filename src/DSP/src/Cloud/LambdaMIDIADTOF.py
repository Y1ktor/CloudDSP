"""Transcribe durable CloudDSP drum stems to MIDI with CPU ADTOF.

The Lambda persists its artifact keys in DynamoDB before it sends a
``job_updated`` WebSocket hint.  It never depends on the WebSocket connection
that existed when the source audio was uploaded.
"""

import json
import os
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Any

import boto3
import librosa
import numpy as np
from adtof_pytorch import transcribe_to_midi
from pretty_midi import PrettyMIDI

from cloud_job_workflow import (
    get_job,
    job_id_from_key,
    record_midi_state,
    send_job_updated,
)


EXTRACTOR = "adtof"
DEFAULT_FPS = 100
DEFAULT_THRESHOLDS = (0.22, 0.24, 0.32, 0.22, 0.30)
MIN_DRUM_EVENT_COUNT = 8
MIN_BEAT_COUNT = 8
MIN_SHORT_CLIP_BEAT_COUNT = 4
MIN_BEAT_INTERVAL_CONSISTENCY = 0.70


def download_from_s3(s3_client, bucket: str, key: str, local_path: Path) -> None:
    print(f"Downloading s3://{bucket}/{key} to {local_path}...")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    s3_client.download_file(bucket, key, str(local_path))


def upload_file_to_s3(s3_client, local_file: Path, bucket: str, key: str) -> None:
    print(f"Uploading {local_file.name} to s3://{bucket}/{key}...")
    s3_client.upload_file(str(local_file), bucket, key)


def parse_thresholds() -> tuple[float, ...]:
    configured = os.environ.get("ADTOF_THRESHOLDS")
    if not configured:
        return DEFAULT_THRESHOLDS
    try:
        thresholds = tuple(float(value.strip()) for value in configured.split(","))
    except ValueError as error:
        raise ValueError("ADTOF_THRESHOLDS must be five comma-separated numbers.") from error
    if len(thresholds) != len(DEFAULT_THRESHOLDS):
        raise ValueError("ADTOF_THRESHOLDS needs kick, snare, tom, hi-hat, and cymbal values.")
    return thresholds


def count_drum_events(midi_path: Path) -> int:
    """Count ADTOF's predicted drum notes before trusting a drum-derived tempo."""
    try:
        midi = PrettyMIDI(str(midi_path))
        return sum(len(instrument.notes) for instrument in midi.instruments)
    except Exception as error:
        print(f"Warning: Could not count ADTOF drum events: {error}")
        return 0


def estimate_tempo_candidate(audio_path: Path, drum_event_count: int) -> dict[str, Any]:
    """Return a drum BPM candidate only when both beats and ADTOF hits support it."""
    print("Estimating BPM from drum stem...")
    try:
        audio, sample_rate = librosa.load(str(audio_path), sr=None, mono=True)
        tempo, beat_times = librosa.beat.beat_track(y=audio, sr=sample_rate, units="time")
        tempo_values = np.asarray(tempo).reshape(-1)
        bpm = float(tempo_values[0]) if tempo_values.size else 0.0
        beats = np.asarray(beat_times, dtype=float).reshape(-1)
        duration_seconds = len(audio) / sample_rate if sample_rate else 0.0
        intervals = np.diff(beats)
        if intervals.size:
            median_interval = float(np.median(intervals))
            tolerance = max(0.04, median_interval * 0.12)
            interval_consistency = float(np.mean(np.abs(intervals - median_interval) <= tolerance))
        else:
            interval_consistency = 0.0
        minimum_beats = MIN_SHORT_CLIP_BEAT_COUNT if duration_seconds < 20 else MIN_BEAT_COUNT
        credible = bool(
            np.isfinite(bpm)
            and bpm > 0
            and drum_event_count >= MIN_DRUM_EVENT_COUNT
            and beats.size >= minimum_beats
            and interval_consistency >= MIN_BEAT_INTERVAL_CONSISTENCY
        )
        candidate = {
            "bpm": round(bpm, 2) if np.isfinite(bpm) and bpm > 0 else None,
            "beat_count": int(beats.size),
            "duration_seconds": round(duration_seconds, 2),
            "interval_consistency": round(interval_consistency, 3),
            "drum_event_count": drum_event_count,
            "credible": credible,
            "confidence": "high" if credible else "low",
            "source": "adtof_drums",
        }
        print(f"ADTOF tempo candidate: {candidate}")
        return candidate
    except Exception as error:
        print(f"Warning: Drum BPM estimation failed: {error}")
        return {
            "bpm": None,
            "beat_count": 0,
            "duration_seconds": 0.0,
            "interval_consistency": 0.0,
            "drum_event_count": drum_event_count,
            "credible": False,
            "confidence": "low",
            "source": "adtof_drums",
        }


def extract_midi_cloud(
    input_bucket: str,
    output_bucket: str,
    file_key: str,
    job_id: str,
    stem_name: str,
) -> dict[str, Any]:
    s3_client = boto3.client("s3")
    base_tmp_dir = Path("/tmp/clouddsp-adtof") / job_id / stem_name
    shutil.rmtree(base_tmp_dir, ignore_errors=True)
    local_input_path = base_tmp_dir / "input" / Path(file_key).name
    local_output_dir = base_tmp_dir / "output"
    local_midi_path = local_output_dir / f"{stem_name}.mid"
    local_bpm_path = local_output_dir / f"{stem_name}_bpm.json"
    download_from_s3(s3_client, input_bucket, file_key, local_input_path)

    thresholds = parse_thresholds()
    fps = int(os.environ.get("ADTOF_FPS", DEFAULT_FPS))
    if fps <= 0:
        raise ValueError("ADTOF_FPS must be positive.")
    print(f"Running CPU ADTOF for job {job_id}, stem {stem_name}; FPS={fps}, thresholds={thresholds}.")
    started_at = time.monotonic()
    transcribe_to_midi(local_input_path, local_midi_path, thresholds=thresholds, fps=fps, device="cpu")
    print(f"ADTOF inference completed in {time.monotonic() - started_at:.2f}s.")

    drum_event_count = count_drum_events(local_midi_path)
    tempo_candidate = estimate_tempo_candidate(local_input_path, drum_event_count)
    local_bpm_path.parent.mkdir(parents=True, exist_ok=True)
    local_bpm_path.write_text(json.dumps({"extractor": EXTRACTOR, **tempo_candidate}))
    midi_key = f"midi/{job_id}/{stem_name}.mid"
    bpm_key = f"midi/{job_id}/{stem_name}_bpm.json"
    upload_file_to_s3(s3_client, local_midi_path, output_bucket, midi_key)
    upload_file_to_s3(s3_client, local_bpm_path, output_bucket, bpm_key)
    shutil.rmtree(base_tmp_dir, ignore_errors=True)
    return {
        "midi_key": midi_key,
        "bpm_key": bpm_key,
        "tempo_candidate": tempo_candidate,
    }


def process_stem(bucket: str, output_bucket: str, key: str, event: dict[str, Any]) -> None:
    job_id = event.get("job_id") or job_id_from_key(key, "stems")
    stem_name = event.get("stem_name") or Path(key).stem
    if stem_name.lower() != "drums":
        raise ValueError("ADTOF accepts only the drums stem.")

    jobs_table = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE_NAME"])
    connections_table = boto3.resource("dynamodb").Table(os.environ["CONNECTIONS_TABLE_NAME"])
    job = get_job(jobs_table, job_id)
    stem_record = (job.get("stems") or {}).get(stem_name)
    if not stem_record or stem_record.get("s3_key") != key:
        raise ValueError(f"Stem key does not match durable job {job_id}/{stem_name}.")

    item = record_midi_state(
        jobs_table, job_id, stem_name, {"status": "processing", "extractor": EXTRACTOR}
    )
    send_job_updated(connections_table, item, job_id, item.get("revision", 0))
    try:
        artifacts = extract_midi_cloud(bucket, output_bucket, key, job_id, stem_name)
        item = record_midi_state(
            jobs_table,
            job_id,
            stem_name,
            {
                "status": "ready",
                "extractor": EXTRACTOR,
                "s3_key": artifacts["midi_key"],
                "bpm_key": artifacts["bpm_key"],
                "tempo_candidate": artifacts["tempo_candidate"],
            },
        )
        send_job_updated(connections_table, item, job_id, item.get("revision", 0))
        print(f"ADTOF completed job {job_id}, stem {stem_name}.")
    except Exception as error:
        item = record_midi_state(
            jobs_table,
            job_id,
            stem_name,
            {"status": "failed", "extractor": EXTRACTOR, "error": str(error)[:1000]},
        )
        send_job_updated(connections_table, item, job_id, item.get("revision", 0))
        print(f"ADTOF failed for job {job_id}, stem {stem_name}: {error}")
        raise


def event_stems(event: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    if event.get("bucket_name") and event.get("file_key"):
        return [(event["bucket_name"], event["file_key"], event)]
    stems: list[tuple[str, str, dict[str, Any]]] = []
    for record in event.get("Records", []):
        wrapped = json.loads(record["body"]) if "body" in record else {"Records": [record]}
        for s3_record in wrapped.get("Records", []):
            if "s3" in s3_record:
                stems.append((
                    s3_record["s3"]["bucket"]["name"],
                    urllib.parse.unquote_plus(s3_record["s3"]["object"]["key"]),
                    wrapped,
                ))
    return stems


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    print("Received ADTOF event:", json.dumps(event))
    output_bucket = os.environ.get("OUTPUT_BUCKET")
    stems = event_stems(event)
    if not stems:
        raise ValueError("No supported stem records were supplied.")
    for bucket, key, stem_event in stems:
        process_stem(bucket, output_bucket or bucket, key, stem_event)
    return {"statusCode": 200, "body": json.dumps("Processed ADTOF drum stem(s).")}
