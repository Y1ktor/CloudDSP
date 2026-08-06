"""Extract durable CloudDSP drum MIDI with Madmom.

Madmom is an optional drum extractor.  It follows the same job-ID contract as
ADTOF: persist the artifact keys first, then notify subscribed clients to
refresh the Job API snapshot.
"""

import json
import os
import shutil
import urllib.parse
from pathlib import Path
from typing import Any

import boto3
import mido
import numpy as np
from madmom.features.beats import DBNBeatTrackingProcessor, RNNBeatProcessor
from madmom.features.onsets import OnsetPeakPickingProcessor, RNNOnsetProcessor

from cloud_job_workflow import (
    get_job,
    job_id_from_key,
    record_midi_state,
    send_job_updated,
)


EXTRACTOR = "madmom"
MIDI_TICKS_PER_BEAT = 480
DEFAULT_BPM = 120.0
DEFAULT_DRUM_NOTE = 36


def download_from_s3(s3_client, bucket: str, key: str, local_path: Path) -> None:
    print(f"Downloading s3://{bucket}/{key} to {local_path}...")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    s3_client.download_file(bucket, key, str(local_path))


def upload_file_to_s3(s3_client, local_file: Path, bucket: str, key: str) -> None:
    print(f"Uploading {local_file.name} to s3://{bucket}/{key}...")
    s3_client.upload_file(str(local_file), bucket, key)


def estimate_bpm(audio_path: Path) -> float:
    activations = RNNBeatProcessor()(str(audio_path))
    beat_times = DBNBeatTrackingProcessor(fps=100)(activations)
    if len(beat_times) < 2:
        return DEFAULT_BPM
    intervals = np.diff(beat_times)
    intervals = intervals[intervals > 0]
    return float(60.0 / np.median(intervals)) if len(intervals) else DEFAULT_BPM


def detect_drum_onsets(audio_path: Path) -> np.ndarray:
    activations = RNNOnsetProcessor()(str(audio_path))
    onsets = OnsetPeakPickingProcessor(fps=100, threshold=0.5, combine=0.03)(activations)
    print(f"Madmom detected {len(onsets)} drum onset(s).")
    return onsets


def write_drum_midi(onsets: np.ndarray, bpm: float, destination: Path) -> None:
    tempo = mido.bpm2tempo(bpm)
    midi = mido.MidiFile(ticks_per_beat=MIDI_TICKS_PER_BEAT)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Madmom Drums", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    events = [(float(onset), 1, "note_on") for onset in onsets]
    events.extend((float(onset) + 0.05, 0, "note_off") for onset in onsets)
    previous_time = 0.0
    drum_note = int(os.environ.get("MADMOM_DRUM_MIDI_NOTE", DEFAULT_DRUM_NOTE))
    for event_time, _, event_type in sorted(events):
        delta_ticks = max(
            0,
            int(round(mido.second2tick(max(0.0, event_time - previous_time), MIDI_TICKS_PER_BEAT, tempo))),
        )
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


def extract_midi_cloud(
    input_bucket: str,
    output_bucket: str,
    file_key: str,
    job_id: str,
    stem_name: str,
) -> dict[str, str]:
    s3_client = boto3.client("s3")
    base_tmp_dir = Path("/tmp/clouddsp-madmom") / job_id / stem_name
    shutil.rmtree(base_tmp_dir, ignore_errors=True)
    input_path = base_tmp_dir / "input" / Path(file_key).name
    output_dir = base_tmp_dir / "output"
    midi_path = output_dir / f"{stem_name}.mid"
    bpm_path = output_dir / f"{stem_name}_bpm.json"
    download_from_s3(s3_client, input_bucket, file_key, input_path)
    onsets = detect_drum_onsets(input_path)
    bpm = estimate_bpm(input_path)
    write_drum_midi(onsets, bpm, midi_path)
    bpm_path.parent.mkdir(parents=True, exist_ok=True)
    bpm_path.write_text(json.dumps({"bpm": bpm, "extractor": EXTRACTOR}))
    midi_key = f"midi/{job_id}/{stem_name}.mid"
    bpm_key = f"midi/{job_id}/{stem_name}_bpm.json"
    upload_file_to_s3(s3_client, midi_path, output_bucket, midi_key)
    upload_file_to_s3(s3_client, bpm_path, output_bucket, bpm_key)
    shutil.rmtree(base_tmp_dir, ignore_errors=True)
    return {"midi_key": midi_key, "bpm_key": bpm_key}


def process_stem(bucket: str, output_bucket: str, key: str, event: dict[str, Any]) -> None:
    job_id = event.get("job_id") or job_id_from_key(key, "stems")
    stem_name = event.get("stem_name") or Path(key).stem
    if stem_name.lower() != "drums":
        raise ValueError("Madmom accepts only the drums stem.")
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
    except Exception as error:
        item = record_midi_state(
            jobs_table,
            job_id,
            stem_name,
            {"status": "failed", "extractor": EXTRACTOR, "error": str(error)[:1000]},
        )
        send_job_updated(connections_table, item, job_id, item.get("revision", 0))
        print(f"Madmom failed for job {job_id}, stem {stem_name}: {error}")
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
    print("Received Madmom event:", json.dumps(event))
    output_bucket = os.environ.get("OUTPUT_BUCKET")
    stems = event_stems(event)
    if not stems:
        raise ValueError("No supported stem records were supplied.")
    for bucket, key, stem_event in stems:
        process_stem(bucket, output_bucket or bucket, key, stem_event)
    return {"statusCode": 200, "body": json.dumps("Processed Madmom drum stem(s).")}
