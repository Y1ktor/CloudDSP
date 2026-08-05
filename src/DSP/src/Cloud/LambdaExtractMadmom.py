"""Extract percussion MIDI and tempo from an S3 audio stem using Madmom.

This Lambda is a CPU-oriented alternative to Basic Pitch for drum stems. It
detects drum onsets with Madmom's RNN onset model, writes General MIDI
percussion notes, estimates tempo with Madmom's beat tracker, uploads both
artifacts to S3, and returns their presigned URLs to the originating frontend
through API Gateway WebSockets.
"""

import json
import os
import shutil
import urllib.parse
from pathlib import Path

import boto3
import mido
import numpy as np
from madmom.features.beats import DBNBeatTrackingProcessor, RNNBeatProcessor
from madmom.features.onsets import OnsetPeakPickingProcessor, RNNOnsetProcessor


MIDI_TICKS_PER_BEAT = 480
DEFAULT_BPM = 120.0
DEFAULT_DRUM_NOTE = 36  # General MIDI percussion: bass drum.


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
    """Resolve the browser callback context from stem metadata or the event."""
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
    """Send the same completion event consumed by the existing React client."""
    if not connection_id or not websocket_url:
        print("Skipping Madmom completion notification (missing callback context).")
        return

    payload = {
        "type": "midi_processing_complete",
        "stem_name": stem_name,
        "midi_url": midi_url,
        "extractor": "madmom",
    }
    if bpm_url:
        payload["bpm_url"] = bpm_url

    print(f"Sending Madmom completion event to WebSocket ID: {connection_id}")
    try:
        boto3.client("apigatewaymanagementapi", endpoint_url=websocket_url).post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(payload).encode("utf-8"),
        )
        print(f"Successfully sent Madmom MIDI URL for '{stem_name}'.")
    except Exception as error:
        print(f"Warning: Failed to send Madmom completion notification: {error}")


def estimate_bpm(audio_path: Path) -> float:
    """Estimate tempo from Madmom beat activations, with a usable fallback."""
    print("Estimating BPM with Madmom...")
    beat_activations = RNNBeatProcessor()(str(audio_path))
    beat_times = DBNBeatTrackingProcessor(fps=100)(beat_activations)
    if len(beat_times) < 2:
        print(f"Too few detected beats; using fallback BPM {DEFAULT_BPM}.")
        return DEFAULT_BPM

    intervals = np.diff(beat_times)
    intervals = intervals[intervals > 0]
    if len(intervals) == 0:
        print(f"No positive beat intervals; using fallback BPM {DEFAULT_BPM}.")
        return DEFAULT_BPM

    bpm = float(60.0 / np.median(intervals))
    print(f"Madmom estimated BPM: {bpm:.2f}")
    return bpm


def detect_drum_onsets(audio_path: Path) -> np.ndarray:
    """Return onset timestamps suitable for General MIDI percussion events."""
    print("Detecting drum onsets with Madmom...")
    activations = RNNOnsetProcessor()(str(audio_path))
    onsets = OnsetPeakPickingProcessor(fps=100, threshold=0.5, combine=0.03)(activations)
    print(f"Madmom detected {len(onsets)} drum onset(s).")
    return onsets


def write_drum_midi(onsets: np.ndarray, bpm: float, destination: Path):
    """Write detected onsets as General MIDI percussion notes on channel 10."""
    tempo = mido.bpm2tempo(bpm)
    midi = mido.MidiFile(ticks_per_beat=MIDI_TICKS_PER_BEAT)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Madmom Drums", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))

    events = []
    for onset in onsets:
        onset_time = float(onset)
        events.append((onset_time, 1, "note_on"))
        events.append((onset_time + 0.05, 0, "note_off"))

    previous_time = 0.0
    drum_note = int(os.environ.get("MADMOM_DRUM_MIDI_NOTE", DEFAULT_DRUM_NOTE))
    for event_time, event_order, event_type in sorted(events):
        delta_seconds = max(0.0, event_time - previous_time)
        delta_ticks = max(0, int(round(mido.second2tick(delta_seconds, MIDI_TICKS_PER_BEAT, tempo))))
        track.append(mido.Message(
            event_type,
            channel=9,
            note=drum_note,
            velocity=112 if event_type == "note_on" else 0,
            time=delta_ticks,
        ))
        previous_time = event_time

    track.append(mido.MetaMessage("end_of_track", time=0))
    destination.parent.mkdir(parents=True, exist_ok=True)
    midi.save(destination)
    print(f"Wrote Madmom percussion MIDI to {destination}.")


def output_keys(file_key: str, stem_name: str) -> tuple[str, str]:
    parts = Path(file_key).parts
    if len(parts) >= 3 and parts[0] == "stems":
        job_folder = parts[1]
        return f"midi/{job_folder}/{stem_name}.mid", f"midi/{job_folder}/{stem_name}_bpm.json"
    return f"midi/{stem_name}.mid", f"midi/{stem_name}_bpm.json"


def extract_midi_cloud(input_bucket: str, output_bucket: str, file_key: str, stem_name: str):
    """Download a stem, create percussion MIDI/BPM files, and upload them."""
    s3_client = boto3.client("s3")
    base_tmp_dir = Path("/tmp/clouddsp-madmom")
    # Lambda environments can be warm; Madmom outputs must not leak between jobs.
    shutil.rmtree(base_tmp_dir, ignore_errors=True)

    local_input_path = base_tmp_dir / "input" / Path(file_key).name
    local_output_dir = base_tmp_dir / "output"
    download_from_s3(s3_client, input_bucket, file_key, local_input_path)

    onsets = detect_drum_onsets(local_input_path)
    bpm = estimate_bpm(local_input_path)
    midi_path = local_output_dir / f"{Path(file_key).stem}_madmom.mid"
    bpm_path = local_output_dir / f"{Path(file_key).stem}_bpm.json"
    write_drum_midi(onsets, bpm, midi_path)
    bpm_path.parent.mkdir(parents=True, exist_ok=True)
    bpm_path.write_text(json.dumps({"bpm": bpm, "extractor": "madmom"}))

    midi_key, bpm_key = output_keys(file_key, stem_name)
    upload_file_to_s3(s3_client, midi_path, output_bucket, midi_key)
    upload_file_to_s3(s3_client, bpm_path, output_bucket, bpm_key)
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
        print(f"Error processing Madmom event: {error}")
        return {"statusCode": 500, "body": json.dumps(f"Error: {error}")}
