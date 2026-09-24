#!/usr/bin/env python3
"""Mirror the exact MIDI playback banks used by the local React frontend.

This is a *bootstrap* command run on the Mac while online, not a runtime
dependency of the site. It derives the piano sample names from the installed,
lockfile-pinned smplr package rather than maintaining a second handwritten
note list. It then verifies every downloaded byte against a checked-in SHA-256
catalog before uploading anything to a dedicated MinIO bucket.

Only this bucket receives an anonymous GetObject policy. The private uploads
bucket, MinIO administrator API, and write permissions are not exposed.
"""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent
FRONTEND_APP = HERE.parent / "frontend" / "app"
LOCK_PATH = HERE / "midi-sample-assets.lock.json"
BUCKET = "clouddsp-midi-samples"
ENDPOINT = "http://minio.localhost:8080"
CONTEXT = "k3d-clouddsp-local"
NAMESPACE = "clouddsp-data"
SECRET_NAME = "clouddsp-minio-root-credentials"
PIANO_SOURCE = "https://smpldsnds.github.io/sfzinstruments-splendid-grand-piano/samples"
SOUNDFONT_SOURCE = "https://gleitz.github.io/midi-js-soundfonts/FluidR3_GM"
DRUM_SOURCE = "https://smpldsnds.github.io/drum-machines/808-mini"
DRUM_FILES = ("kick.m4a", "snare-1.m4a", "tom-mid.m4a", "hhclosed-1.m4a", "crash.m4a")


def command(*args: str, env: dict[str, str] | None = None) -> str:
    """Run one CLI without a shell so credentials never become command text."""
    result = subprocess.run(args, check=True, text=True, capture_output=True, env=env)
    return result.stdout


def piano_sample_names() -> list[str]:
    # smplr's five layers contain several regions for each note. The preset
    # already de-duplicates the sample names the browser's loader will fetch.
    script = (
        "import {pianoToPreset} from 'smplr';"
        "const preset=pianoToPreset({baseUrl:'',detune:0,decayTime:0.5});"
        "const names=[...new Set(preset.groups.flatMap(g=>g.regions.map(r=>r.sample)))].sort();"
        "console.log(JSON.stringify(names));"
    )
    output = subprocess.run(
        ("node", "--input-type=module", "--eval", script),
        cwd=FRONTEND_APP,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    names = json.loads(output)
    if len(names) != 226 or not all(isinstance(name, str) and name for name in names):
        raise RuntimeError("Pinned smplr piano bank changed; review the new asset catalog first")
    return names


def catalog() -> dict[str, str]:
    """Map fixed MinIO object keys to their reviewed upstream source URLs."""
    assets = {}
    for name in piano_sample_names():
        for extension in ("ogg", "m4a"):
            key = f"piano/{name}.{extension}"
            assets[key] = f"{PIANO_SOURCE}/{quote(name)}.{extension}"
    for instrument in ("acoustic_bass", "acoustic_guitar_nylon"):
        for format_name in ("ogg", "mp3"):
            filename = f"{instrument}-{format_name}.js"
            assets[f"soundfonts/FluidR3_GM/{filename}"] = f"{SOUNDFONT_SOURCE}/{filename}"
    for filename in DRUM_FILES:
        assets[f"drums/{filename}"] = f"{DRUM_SOURCE}/{filename}"
    return dict(sorted(assets.items()))


def download_one(key: str, url: str, directory: Path) -> tuple[str, str, int]:
    # Request the bytes once and retain their SHA-256. Missing/HTML error pages
    # fail closed; otherwise a short GitHub Pages outage could poison the bank.
    request = Request(url, headers={"User-Agent": "CloudDSP-local-sample-mirror/1"})
    with urlopen(request, timeout=45) as response:
        content_type = response.headers.get("Content-Type", "").lower()
        if response.status != 200 or "text/html" in content_type:
            raise RuntimeError(f"Sample source returned unexpected response for {key}: {response.status} {content_type}")
        data = response.read()
    if not data:
        raise RuntimeError(f"Empty sample source: {key}")
    target = directory / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return key, hashlib.sha256(data).hexdigest(), len(data)


def minio_environment() -> dict[str, str]:
    secret = json.loads(command(
        "kubectl", "--context", CONTEXT, "get", "secret", SECRET_NAME,
        "--namespace", NAMESPACE, "--output", "json",
    ))
    values = secret["data"]
    env = os.environ.copy()
    env.update({
        "AWS_ACCESS_KEY_ID": base64.b64decode(values["MINIO_ROOT_USER"]).decode(),
        "AWS_SECRET_ACCESS_KEY": base64.b64decode(values["MINIO_ROOT_PASSWORD"]).decode(),
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_MAX_ATTEMPTS": "3",
    })
    return env


def aws(env: dict[str, str], *args: str) -> str:
    return command("aws", "--endpoint-url", ENDPOINT, *args, env=env)


def ensure_bucket(env: dict[str, str]) -> None:
    try:
        aws(env, "s3api", "head-bucket", "--bucket", BUCKET)
    except subprocess.CalledProcessError:
        # create-bucket will fail if a different cause made head-bucket fail;
        # that is safer than silently targeting an unexpected endpoint.
        aws(env, "s3api", "create-bucket", "--bucket", BUCKET)


def publish_read_only_policy(env: dict[str, str], directory: Path) -> None:
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "BrowserReadSharedMidiSamplesOnly",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": f"arn:aws:s3:::{BUCKET}/*",
        }],
    }
    policy_file = directory / "public-read-only-policy.json"
    policy_file.write_text(json.dumps(policy, sort_keys=True))
    aws(env, "s3api", "put-bucket-policy", "--bucket", BUCKET, "--policy", f"file://{policy_file}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record-lock", action="store_true",
        help="record upstream SHA-256 evidence for an initial/reviewed sample-bank update",
    )
    parser.add_argument(
        "--catalog-only", action="store_true",
        help="inspect required object count without network access or MinIO writes",
    )
    args = parser.parse_args()
    sources = catalog()
    print(f"Pinned smplr requires {len(sources)} local playback objects", flush=True)
    if args.catalog_only:
        return 0

    existing_lock = json.loads(LOCK_PATH.read_text()) if LOCK_PATH.exists() else None
    if not args.record_lock and existing_lock is None:
        raise RuntimeError("Sample SHA-256 lock is absent; run with --record-lock after source review")
    if existing_lock and set(existing_lock["assets"]) != set(sources):
        raise RuntimeError("Sample set changed; review smplr update and intentionally refresh the lock")

    with tempfile.TemporaryDirectory(prefix="clouddsp-midi-samples-") as temp_name:
        directory = Path(temp_name)
        download_directory = directory / "objects"
        hashes = {}
        # Bound concurrency so bootstrap is quick without hammering two public
        # GitHub Pages origins or exhausting the laptop's file descriptors.
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(download_one, key, url, download_directory) for key, url in sources.items()]
            for index, future in enumerate(as_completed(futures), 1):
                key, digest, length = future.result()
                hashes[key] = {"sha256": digest, "bytes": length, "source": sources[key]}
                if index % 50 == 0 or index == len(sources):
                    print(f"Downloaded and hashed {index}/{len(sources)} objects", flush=True)

        if existing_lock and not args.record_lock:
            if hashes != existing_lock["assets"]:
                raise RuntimeError("Upstream sample bytes changed; no MinIO objects were written")
        if args.record_lock:
            lock = {"bucket": BUCKET, "asset_count": len(sources), "assets": dict(sorted(hashes.items()))}
            LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
            print(f"Recorded reviewed SHA-256 catalog: {LOCK_PATH}", flush=True)

        env = minio_environment()
        ensure_bucket(env)
        # S3 sync preserves original filenames (including #); the browser's
        # smplr storage adapter percent-encodes # in HTTP URLs when fetching.
        aws(env, "s3", "sync", str(download_directory), f"s3://{BUCKET}/", "--no-progress", "--only-show-errors")
        listing = json.loads(aws(env, "s3api", "list-objects-v2", "--bucket", BUCKET, "--output", "json"))
        uploaded = {item["Key"]: item["Size"] for item in listing.get("Contents", [])}
        expected = {key: item["bytes"] for key, item in hashes.items()}
        if any(uploaded.get(key) != size for key, size in expected.items()):
            raise RuntimeError("MinIO object listing did not match all expected sample sizes; bucket remains private")
        publish_read_only_policy(env, directory)

    # The anonymous route must work from an ordinary browser without S3
    # credentials. Also verify that MinIO's server CORS allows the React origin.
    url = f"{ENDPOINT}/{BUCKET}/drums/kick.m4a"
    request = Request(url, headers={"Origin": "http://clouddsp.localhost:8080"})
    with urlopen(request, timeout=20) as response:
        if response.status != 200 or response.headers.get("Access-Control-Allow-Origin") != "http://clouddsp.localhost:8080":
            raise RuntimeError("Anonymous browser-read/CORS smoke check failed")
    print(f"Verified {len(sources)} objects and anonymous browser read in {BUCKET}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"MIDI sample mirror failed: {exc}", file=sys.stderr)
        sys.exit(1)
