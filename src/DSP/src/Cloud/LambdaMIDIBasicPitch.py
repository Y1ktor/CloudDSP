"""Extract pitched MIDI for a durable CloudDSP job with Basic Pitch.

This Lambda receives a stem key under ``stems/{job_id}/``.  It persists MIDI
artifact keys to the job record before posting ``job_updated`` to subscribed
browser sockets.  Clients fetch fresh presigned URLs from the Job API.
"""

import json
import os
import shutil
import urllib.parse
from pathlib import Path
from typing import Any

import boto3
import numpy as np

from cloud_job_workflow import (
    get_job,
    job_id_from_key,
    record_midi_state,
    send_job_updated,
)

try:
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict_and_save
    import librosa
except ImportError as error:
    raise RuntimeError(f"Basic Pitch dependencies are unavailable: {error}") from error


EXTRACTOR = "basic_pitch"
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


def estimate_tempo_candidate(audio_path: Path) -> dict[str, Any]:
    """Estimate a BPM candidate and retain evidence for backend consensus."""
    try:
        print("Estimating BPM with librosa...")
        audio, sample_rate = librosa.load(str(audio_path))
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
            and beats.size >= minimum_beats
            and interval_consistency >= MIN_BEAT_INTERVAL_CONSISTENCY
        )
        candidate = {
            "bpm": round(bpm, 2) if np.isfinite(bpm) and bpm > 0 else None,
            "beat_count": int(beats.size),
            "duration_seconds": round(duration_seconds, 2),
            "interval_consistency": round(interval_consistency, 3),
            "credible": credible,
            "confidence": "medium" if credible else "low",
            "source": "librosa_stem",
        }
        print(f"Basic Pitch tempo candidate: {candidate}")
        return candidate
    except Exception as error:
        print(f"Warning: BPM estimation failed: {error}")
        return {
            "bpm": None,
            "beat_count": 0,
            "duration_seconds": 0.0,
            "interval_consistency": 0.0,
            "credible": False,
            "confidence": "low",
            "source": "librosa_stem",
        }


def extract_midi_cloud(
    input_bucket: str,
    output_bucket: str,
    file_key: str,
    job_id: str,
    stem_name: str,
) -> dict[str, Any]:
    """Run Basic Pitch and upload MIDI/BPM artifacts using the job prefix."""
    s3_client = boto3.client("s3")
    base_tmp_dir = Path("/tmp/clouddsp-basic-pitch") / job_id / stem_name
    shutil.rmtree(base_tmp_dir, ignore_errors=True)
    local_input_path = base_tmp_dir / "input" / Path(file_key).name
    local_output_dir = base_tmp_dir / "output"
    local_output_dir.mkdir(parents=True, exist_ok=True)

    download_from_s3(s3_client, input_bucket, file_key, local_input_path)
    print(f"Running Basic Pitch for job {job_id}, stem {stem_name}...")
    predict_and_save(
        audio_path_list=[str(local_input_path)],
        output_directory=str(local_output_dir),
        save_midi=True,
        sonify_midi=False,
        save_model_outputs=False,
        save_notes=False,
        model_or_model_path=ICASSP_2022_MODEL_PATH,
    )

    midi_path = local_output_dir / f"{local_input_path.stem}_basic_pitch.mid"
    if not midi_path.exists():
        generated_files = list(local_output_dir.glob("*.mid"))
        if not generated_files:
            raise FileNotFoundError("Basic Pitch did not generate a MIDI file.")
        midi_path = generated_files[0]

    midi_key = f"midi/{job_id}/{stem_name}.mid"
    upload_file_to_s3(s3_client, midi_path, output_bucket, midi_key)

    tempo_candidate = estimate_tempo_candidate(local_input_path)
    bpm_path = local_output_dir / f"{stem_name}_bpm.json"
    bpm_path.write_text(json.dumps({"extractor": EXTRACTOR, **tempo_candidate}))
    bpm_key = f"midi/{job_id}/{stem_name}_bpm.json"
    upload_file_to_s3(s3_client, bpm_path, output_bucket, bpm_key)

    shutil.rmtree(base_tmp_dir, ignore_errors=True)
    return {
        "midi_key": midi_key,
        "bpm_key": bpm_key,
        "tempo_candidate": tempo_candidate,
    }


def process_stem(bucket: str, output_bucket: str, key: str, event: dict[str, Any]) -> None:
    """Persist state before and after inference, then notify subscribers."""
    job_id = event.get("job_id") or job_id_from_key(key, "stems")
    stem_name = event.get("stem_name") or Path(key).stem
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("job_id is required.")
    if not isinstance(stem_name, str) or not stem_name:
        raise ValueError("stem_name is required.")

    jobs_table = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE_NAME"])
    connections_table = boto3.resource("dynamodb").Table(os.environ["CONNECTIONS_TABLE_NAME"])
    job = get_job(jobs_table, job_id)
    stem_record = (job.get("stems") or {}).get(stem_name)
    if not stem_record or stem_record.get("s3_key") != key:
        raise ValueError(f"Stem key does not match the durable job record for {job_id}/{stem_name}.")

    item = record_midi_state(
        jobs_table, job_id, stem_name, {"status": "processing", "extractor": EXTRACTOR}
    )
    send_job_updated(connections_table, item, job_id, item.get("revision", 0))
    try:
        artifacts = extract_midi_cloud(bucket, output_bucket, key, job_id, stem_name)
        state: dict[str, Any] = {
            "status": "ready",
            "extractor": EXTRACTOR,
            "s3_key": artifacts["midi_key"],
            "tempo_candidate": artifacts["tempo_candidate"],
        }
        if artifacts["bpm_key"]:
            state["bpm_key"] = artifacts["bpm_key"]
        item = record_midi_state(jobs_table, job_id, stem_name, state)
        send_job_updated(connections_table, item, job_id, item.get("revision", 0))
        print(f"Basic Pitch completed job {job_id}, stem {stem_name}.")
    except Exception as error:
        item = record_midi_state(
            jobs_table,
            job_id,
            stem_name,
            {"status": "failed", "extractor": EXTRACTOR, "error": str(error)[:1000]},
        )
        send_job_updated(connections_table, item, job_id, item.get("revision", 0))
        print(f"Basic Pitch failed for job {job_id}, stem {stem_name}: {error}")
        raise


def event_stems(event: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Support direct Batch invokes plus legacy direct S3/SQS delivery."""
    if event.get("bucket_name") and event.get("file_key"):
        return [(event["bucket_name"], event["file_key"], event)]

    stems: list[tuple[str, str, dict[str, Any]]] = []
    for record in event.get("Records", []):
        wrapped = json.loads(record["body"]) if "body" in record else {"Records": [record]}
        for s3_record in wrapped.get("Records", []):
            if "s3" not in s3_record:
                continue
            stems.append((
                s3_record["s3"]["bucket"]["name"],
                urllib.parse.unquote_plus(s3_record["s3"]["object"]["key"]),
                wrapped,
            ))
    return stems


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    print("Received Basic Pitch event:", json.dumps(event))
    output_bucket = os.environ.get("OUTPUT_BUCKET")
    stems = event_stems(event)
    if not stems:
        raise ValueError("No supported stem records were supplied.")
    for bucket, key, stem_event in stems:
        process_stem(bucket, output_bucket or bucket, key, stem_event)
    return {"statusCode": 200, "body": json.dumps("Processed Basic Pitch stem(s).")}
