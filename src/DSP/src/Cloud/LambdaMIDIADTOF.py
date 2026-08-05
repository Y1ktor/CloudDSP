"""Transcribe a Demucs drum stem to multi-instrument MIDI with ADTOF.

This Lambda receives the same direct, S3, or SQS-wrapped event formats as the
Basic Pitch Lambda. It downloads a drum stem, uses the CPU ADTOF model to
identify kick, snare, tom, hi-hat, and cymbal events, uploads MIDI and tempo
artifacts to S3, and notifies the originating browser over API Gateway
WebSockets with presigned URLs.
"""

import json
import os
import shutil
import time
import urllib.parse
from pathlib import Path

import boto3
import librosa
import numpy as np
from adtof_pytorch import transcribe_to_midi


DEFAULT_BPM = 120.0
DEFAULT_FPS = 100
DEFAULT_THRESHOLDS = (0.22, 0.24, 0.32, 0.22, 0.30)


def download_from_s3(s3_client, bucket: str, key: str, local_path: Path):
    print(f"Downloading s3://{bucket}/{key} to {local_path}...")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    s3_client.download_file(bucket, key, str(local_path))
    print("Download complete.")


def upload_file_to_s3(s3_client, local_file: Path, bucket: str, key: str):
    print(f"Uploading {local_file.name} to s3://{bucket}/{key}...")
    s3_client.upload_file(str(local_file), bucket, key)
    print("Upload complete.")


def get_stem_processing_context(s3_client, bucket: str, key: str, event: dict):
    """Resolve browser callback values from S3 metadata or the direct event."""
    metadata = {}
    try:
        metadata = s3_client.head_object(Bucket=bucket, Key=key).get("Metadata", {})
    except Exception as error:
        print(f"Warning: Could not read metadata for s3://{bucket}/{key}: {error}")

    connection_id = metadata.get("connection-id") or event.get("connection_id")
    if connection_id == "unknown":
        connection_id = None
    websocket_url = event.get("websocket_url") or os.environ.get("WEBSOCKET_API_URL")
    stem_name = event.get("stem_name") or Path(key).stem

    print(
        f"Resolved callback context for '{stem_name}': "
        f"connection ID {'found' if connection_id else 'missing'}, "
        f"WebSocket URL {'found' if websocket_url else 'missing'}."
    )
    return connection_id, websocket_url, stem_name


def send_midi_complete_notification(connection_id: str | None, websocket_url: str | None,
                                    stem_name: str, midi_url: str, bpm_url: str | None):
    """Send the completion event consumed by the existing React client."""
    if not connection_id or not websocket_url:
        print("Skipping ADTOF completion notification (missing callback context).")
        return

    payload = {
        "type": "midi_processing_complete",
        "stem_name": stem_name,
        "midi_url": midi_url,
        "extractor": "adtof",
    }
    if bpm_url:
        payload["bpm_url"] = bpm_url

    print(f"Sending ADTOF completion event to WebSocket ID: {connection_id}")
    try:
        boto3.client("apigatewaymanagementapi", endpoint_url=websocket_url).post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(payload).encode("utf-8"),
        )
        print(f"Successfully sent ADTOF MIDI URL for '{stem_name}'.")
    except Exception as error:
        print(f"Warning: Failed to send ADTOF completion notification: {error}")


def parse_thresholds() -> tuple[float, ...]:
    """Read optional five-class peak-picking thresholds from the environment."""
    configured = os.environ.get("ADTOF_THRESHOLDS")
    if not configured:
        return DEFAULT_THRESHOLDS

    try:
        thresholds = tuple(float(value.strip()) for value in configured.split(","))
    except ValueError as error:
        raise ValueError("ADTOF_THRESHOLDS must be five comma-separated numbers.") from error
    if len(thresholds) != len(DEFAULT_THRESHOLDS):
        raise ValueError("ADTOF_THRESHOLDS must contain values for kick, snare, tom, hi-hat, cymbal.")
    return thresholds


def estimate_bpm(audio_path: Path) -> float:
    """Estimate a display tempo without affecting ADTOF's transcription."""
    print("Estimating BPM from the drum stem...")
    audio, sample_rate = librosa.load(str(audio_path), sr=None, mono=True)
    tempo, _ = librosa.beat.beat_track(y=audio, sr=sample_rate)
    bpm = float(np.asarray(tempo).item()) if np.asarray(tempo).size == 1 else DEFAULT_BPM
    if not np.isfinite(bpm) or bpm <= 0:
        bpm = DEFAULT_BPM
    print(f"Estimated BPM: {bpm:.2f}")
    return bpm


def output_keys(file_key: str, stem_name: str) -> tuple[str, str]:
    parts = Path(file_key).parts
    if len(parts) >= 3 and parts[0] == "stems":
        job_folder = parts[1]
        return f"midi/{job_folder}/{stem_name}.mid", f"midi/{job_folder}/{stem_name}_bpm.json"
    return f"midi/{stem_name}.mid", f"midi/{stem_name}_bpm.json"


def extract_midi_cloud(input_bucket: str, output_bucket: str, file_key: str, stem_name: str):
    """Run CPU ADTOF inference, then upload standard MIDI and tempo artifacts."""
    s3_client = boto3.client("s3")
    base_tmp_dir = Path("/tmp/clouddsp-adtof")
    # A Lambda execution environment can be reused. Do not reuse prior output.
    shutil.rmtree(base_tmp_dir, ignore_errors=True)

    local_input_path = base_tmp_dir / "input" / Path(file_key).name
    local_output_dir = base_tmp_dir / "output"
    local_midi_path = local_output_dir / f"{Path(file_key).stem}_adtof.mid"
    local_bpm_path = local_output_dir / f"{Path(file_key).stem}_bpm.json"
    download_from_s3(s3_client, input_bucket, file_key, local_input_path)

    thresholds = parse_thresholds()
    fps = int(os.environ.get("ADTOF_FPS", DEFAULT_FPS))
    if fps <= 0:
        raise ValueError("ADTOF_FPS must be positive.")

    print(
        "Running CPU ADTOF drum transcription "
        "(kick, snare, tom, hi-hat, cymbal) "
        f"with FPS={fps} and thresholds={thresholds}."
    )
    started_at = time.monotonic()
    transcribe_to_midi(
        local_input_path,
        local_midi_path,
        thresholds=thresholds,
        fps=fps,
        device="cpu",
    )
    print(f"ADTOF inference completed in {time.monotonic() - started_at:.2f}s.")

    bpm = estimate_bpm(local_input_path)
    local_bpm_path.parent.mkdir(parents=True, exist_ok=True)
    local_bpm_path.write_text(json.dumps({"bpm": bpm, "extractor": "adtof"}))

    midi_key, bpm_key = output_keys(file_key, stem_name)
    upload_file_to_s3(s3_client, local_midi_path, output_bucket, midi_key)
    upload_file_to_s3(s3_client, local_bpm_path, output_bucket, bpm_key)
    return {"midi_key": midi_key, "bpm_key": bpm_key}


def process_stem(bucket: str, output_bucket: str, key: str, event: dict):
    s3_client = boto3.client("s3")
    connection_id, websocket_url, stem_name = get_stem_processing_context(s3_client, bucket, key, event)
    output = extract_midi_cloud(bucket, output_bucket, key, stem_name)
    midi_url = s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": output_bucket, "Key": output["midi_key"]},
        ExpiresIn=3600,
    )
    bpm_url = s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": output_bucket, "Key": output["bpm_key"]},
        ExpiresIn=3600,
    )
    send_midi_complete_notification(connection_id, websocket_url, stem_name, midi_url, bpm_url)
    return midi_url


def lambda_handler(event, context):
    """Handle direct Batch, SQS-wrapped S3, or direct S3 Lambda events."""
    print("Received event:", json.dumps(event))
    output_bucket = os.environ.get("OUTPUT_BUCKET")

    try:
        if event.get("bucket_name") and event.get("file_key"):
            bucket = event["bucket_name"]
            key = event["file_key"]
            process_stem(bucket, output_bucket or bucket, key, event)
            return {"statusCode": 200, "body": json.dumps("Successfully processed drum stem.")}

        for record in event.get("Records", []):
            event_body = {}
            if "body" in record:
                event_body = json.loads(record["body"])
                records = event_body.get("Records", [])
            else:
                records = [record]

            for s3_record in records:
                if "s3" not in s3_record:
                    continue
                bucket = s3_record["s3"]["bucket"]["name"]
                key = urllib.parse.unquote_plus(s3_record["s3"]["object"]["key"])
                process_stem(bucket, output_bucket or bucket, key, event_body)

        return {"statusCode": 200, "body": json.dumps("Successfully processed drum stem(s).")}
    except Exception as error:
        print(f"Error processing ADTOF event: {error}")
        return {"statusCode": 500, "body": json.dumps(f"Error: {error}")}
