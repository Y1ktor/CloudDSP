"""Run a durable, job-ID-aware Demucs stem-separation Batch workload.

Input objects must use ``uploads/{job_id}/filename``.  The job record in
DynamoDB is authoritative; S3 metadata is read only as a compatibility and
integrity check.  After each durable state update the worker sends a small
``job_updated`` WebSocket hint, never presigned result URLs.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import boto3

from cloud_job_workflow import (
    fail_job,
    get_job,
    job_id_from_key,
    record_midi_state,
    record_stem_results,
    send_job_updated,
    update_job_status,
)

try:
    import demucs.separate
except ImportError:
    print("Error: Demucs is not installed or not found in the container environment.")
    sys.exit(1)


VALID_STEM_MODES = {"2-stems", "4-stems", "6-stems"}


def download_from_s3(s3_client, bucket: str, key: str, local_path: Path) -> None:
    print(f"Downloading s3://{bucket}/{key} to {local_path}...")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    s3_client.download_file(bucket, key, str(local_path))
    print("Download complete.")


def upload_directory_to_s3(
    s3_client,
    local_dir: Path,
    bucket: str,
    prefix: str,
    job_id: str,
) -> dict[str, str]:
    """Upload Demucs output while retaining only durable job metadata."""
    print(f"Uploading stems from {local_dir} to s3://{bucket}/{prefix}...")
    uploaded_keys: dict[str, str] = {}
    for root, _, files in os.walk(local_dir):
        for filename in files:
            local_file = Path(root) / filename
            relative_path = local_file.relative_to(local_dir)
            s3_key = f"{prefix}/{relative_path.as_posix()}"
            stem_name = local_file.stem
            print(f"  -> Uploading {filename} to {s3_key}")
            s3_client.upload_file(
                str(local_file),
                bucket,
                s3_key,
                ExtraArgs={"Metadata": {"job-id": job_id, "stem-name": stem_name}},
            )
            uploaded_keys[stem_name] = s3_key
    if not uploaded_keys:
        raise RuntimeError("Demucs produced no output files.")
    print("Stem upload complete.")
    return uploaded_keys


def select_demucs_arguments(mode: str, output_dir: Path, input_path: Path) -> tuple[str, list[str]]:
    if mode == "6-stems":
        return "htdemucs_6s", ["-n", "htdemucs_6s", "-o", str(output_dir), str(input_path)]
    if mode == "4-stems":
        return "htdemucs", ["-n", "htdemucs", "-o", str(output_dir), str(input_path)]
    if mode == "2-stems":
        return "htdemucs", [
            "-n", "htdemucs", "--two-stems", "vocals", "-o", str(output_dir), str(input_path)
        ]
    raise ValueError("stem mode must be 2-stems, 4-stems, or 6-stems.")


def trigger_midi_extraction_jobs(
    lambda_client,
    jobs_table,
    connections_table,
    job: dict,
    bucket_name: str,
    uploaded_keys: dict[str, str],
) -> None:
    """Invoke ADTOF for drums and Basic Pitch for every pitched stem."""
    job_id = job["job_id"]
    basic_pitch_name = os.environ.get("BASIC_PITCH_LAMBDA_NAME")
    adtof_name = os.environ.get("ADTOF_LAMBDA_NAME")

    for stem_name, file_key in uploaded_keys.items():
        is_drum = stem_name.lower() == "drums"
        function_name = adtof_name if is_drum else basic_pitch_name
        extractor = "adtof" if is_drum else "basic_pitch"
        if not function_name:
            message = f"{extractor} Lambda is not configured."
            print(f"Cannot start MIDI extraction for {stem_name}: {message}")
            item = record_midi_state(
                jobs_table,
                job_id,
                stem_name,
                {"status": "failed", "extractor": extractor, "error": message},
            )
            send_job_updated(connections_table, item, job_id, item.get("revision", 0))
            continue

        payload = {
            "job_id": job_id,
            "bucket_name": bucket_name,
            "file_key": file_key,
            "stem_name": stem_name,
            "extractor": extractor,
        }
        print(f"Invoking {extractor} Lambda for job {job_id}, stem {stem_name}: {payload}")
        try:
            response = lambda_client.invoke(
                FunctionName=function_name,
                InvocationType="Event",
                Payload=json.dumps(payload).encode("utf-8"),
            )
            if response.get("StatusCode") not in {202, 200}:
                raise RuntimeError(f"Unexpected asynchronous invoke response: {response}")
        except Exception as error:
            message = f"Could not dispatch {extractor}: {error}"
            print(message)
            item = record_midi_state(
                jobs_table,
                job_id,
                stem_name,
                {"status": "failed", "extractor": extractor, "error": message[:1000]},
            )
            send_job_updated(connections_table, item, job_id, item.get("revision", 0))


def resolve_mode(job: dict, metadata: dict, fallback_mode: str) -> str:
    """Prefer the persisted request, retaining metadata for transition safety."""
    job_mode = job.get("stem_mode")
    metadata_mode = metadata.get("stem-mode")
    mode = job_mode or metadata_mode or fallback_mode
    if mode not in VALID_STEM_MODES:
        raise ValueError(f"Invalid stem mode {mode!r}.")
    if metadata_mode and job_mode and metadata_mode != job_mode:
        print(
            f"Warning: S3 metadata stem mode {metadata_mode!r} differs from durable job mode "
            f"{job_mode!r}; using the durable value."
        )
    return mode


def split_stems_cloud(input_bucket: str, output_bucket: str, file_key: str, fallback_mode: str) -> None:
    s3_client = boto3.client("s3")
    jobs_table = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE_NAME"])
    connections_table = boto3.resource("dynamodb").Table(os.environ["CONNECTIONS_TABLE_NAME"])
    job_id = job_id_from_key(file_key, "uploads")
    job = get_job(jobs_table, job_id)
    if job.get("input_bucket") != input_bucket or job.get("input_key") != file_key:
        raise ValueError(f"Input object does not match the durable record for job {job_id}.")

    try:
        print(f"Fetching metadata for durable input s3://{input_bucket}/{file_key}...")
        metadata = s3_client.head_object(Bucket=input_bucket, Key=file_key).get("Metadata", {})
        metadata_job_id = metadata.get("job-id")
        if metadata_job_id and metadata_job_id != job_id:
            raise ValueError("S3 job-id metadata does not match the upload key.")
        mode = resolve_mode(job, metadata, fallback_mode)

        item = update_job_status(jobs_table, job_id, "stem_processing")
        send_job_updated(connections_table, item, job_id, item.get("revision", 0))

        base_tmp_dir = Path("/tmp/clouddsp") / job_id
        shutil.rmtree(base_tmp_dir, ignore_errors=True)
        local_input_path = base_tmp_dir / "input" / Path(file_key).name
        local_output_dir = base_tmp_dir / "output"
        download_from_s3(s3_client, input_bucket, file_key, local_input_path)

        model_name, arguments = select_demucs_arguments(mode, local_output_dir, local_input_path)
        print(f"Starting Demucs for job {job_id} using {mode} ({model_name}).")
        demucs.separate.main(arguments)
        output_path = local_output_dir / model_name / local_input_path.stem
        if not output_path.exists():
            raise FileNotFoundError(f"Demucs output directory was not created: {output_path}")

        uploaded_keys = upload_directory_to_s3(
            s3_client,
            output_path,
            output_bucket,
            f"stems/{job_id}",
            job_id,
        )
        item = record_stem_results(jobs_table, job_id, uploaded_keys)
        send_job_updated(connections_table, item, job_id, item.get("revision", 0))

        trigger_midi_extraction_jobs(
            boto3.client("lambda"),
            jobs_table,
            connections_table,
            item,
            output_bucket,
            uploaded_keys,
        )
        print(f"Demucs job {job_id} completed; downstream MIDI extraction is running.")
    except Exception as error:
        fail_job(jobs_table, connections_table, job_id, error)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CloudDSP durable Demucs Batch worker.")
    parser.add_argument("--input-bucket", default=os.environ.get("INPUT_BUCKET"))
    parser.add_argument("--output-bucket", default=os.environ.get("OUTPUT_BUCKET"))
    parser.add_argument("--file-key", default=os.environ.get("FILE_KEY"))
    parser.add_argument(
        "--mode",
        default=os.environ.get("STEM_MODE", "6-stems"),
        choices=sorted(VALID_STEM_MODES),
        help="Fallback only; the durable job record is authoritative.",
    )
    args = parser.parse_args()
    if not args.input_bucket or not args.output_bucket or not args.file_key:
        parser.error("--input-bucket, --output-bucket, and --file-key are required.")
    split_stems_cloud(args.input_bucket, args.output_bucket, args.file_key, args.mode)
