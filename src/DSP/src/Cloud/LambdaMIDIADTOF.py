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

from cloud_job_workflow import (
    get_job,
    job_id_from_key,
    record_midi_state,
    send_job_updated,
)


EXTRACTOR = "adtof"
DEFAULT_BPM = 120.0
DEFAULT_FPS = 100
DEFAULT_THRESHOLDS = (0.22, 0.24, 0.32, 0.22, 0.30)


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


def estimate_bpm(audio_path: Path) -> float:
    print("Estimating BPM from drum stem...")
    audio, sample_rate = librosa.load(str(audio_path), sr=None, mono=True)
    tempo, _ = librosa.beat.beat_track(y=audio, sr=sample_rate)
    bpm = float(np.asarray(tempo).item()) if np.asarray(tempo).size == 1 else DEFAULT_BPM
    return bpm if np.isfinite(bpm) and bpm > 0 else DEFAULT_BPM


def extract_midi_cloud(
    input_bucket: str,
    output_bucket: str,
    file_key: str,
    job_id: str,
    stem_name: str,
) -> dict[str, str]:
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

    bpm = estimate_bpm(local_input_path)
    local_bpm_path.parent.mkdir(parents=True, exist_ok=True)
    local_bpm_path.write_text(json.dumps({"bpm": bpm, "extractor": EXTRACTOR}))
    midi_key = f"midi/{job_id}/{stem_name}.mid"
    bpm_key = f"midi/{job_id}/{stem_name}_bpm.json"
    upload_file_to_s3(s3_client, local_midi_path, output_bucket, midi_key)
    upload_file_to_s3(s3_client, local_bpm_path, output_bucket, bpm_key)
    shutil.rmtree(base_tmp_dir, ignore_errors=True)
    return {"midi_key": midi_key, "bpm_key": bpm_key}


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
