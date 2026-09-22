# syntax=docker/dockerfile:1.7

# This image deliberately targets the local Apple-silicon / CPU development
# profile. It does not contain CUDA, NVIDIA drivers, or an MPS assumption.
# The exact multi-architecture Python base is recorded in image-recipe.md.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS base

# Keep Python deterministic and make Demucs look for baked model weights rather
# than downloading them while a worker Pod is handling a message.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TORCH_HOME=/opt/clouddsp/demucs-models

WORKDIR /app

# FFprobe is the worker's media-inspection tool. ca-certificates is installed
# only so the *image build* can retrieve the checksum-locked public weights;
# a running worker does not download models. Exact versions make an upstream
# Debian package change fail the build instead of silently changing the image.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates=20250419~deb12u1 \
        ffmpeg=7:5.1.9-0+deb12u1 \
    && rm -rf /var/lib/apt/lists/*


# `validation` contains build-only test sources and is never the final image.
# It installs every Python artifact from the hash-pinned lock before validating
# the source code and the external, checksum-pinned model files.
FROM base AS validation

COPY requirements.lock ./requirements.lock

# Demucs itself is a source distribution. Install the same locked setuptools
# first, without resolving anything, so its build backend is already available
# when pip later builds source distributions with build isolation disabled.
RUN awk '/^setuptools==/{copying=1} copying {print} copying && /--hash=/{exit}' \
        requirements.lock > /tmp/build-tools.lock
RUN python -m pip install \
        --root-user-action=ignore \
        --no-cache-dir \
        --no-deps \
        --require-hashes \
        --requirement /tmp/build-tools.lock
RUN rm -f /tmp/build-tools.lock

# `--no-deps` is intentional: requirements.lock explicitly lists and hashes the
# complete dependency closure. The CPU PyTorch index supplies only the locked
# ARM64 CPU wheels; it is not a permission to resolve newer dependencies.
RUN python -m pip install \
        --root-user-action=ignore \
        --no-cache-dir \
        --no-deps \
        --no-build-isolation \
        --require-hashes \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        --requirement requirements.lock

COPY model-artifacts.lock.yaml ./model-artifacts.lock.yaml

# The model lock is the reviewable source of truth for both URLs and SHA-256
# values. This build-time verifier accepts only HTTPS downloads from Demucs'
# expected public hosts, rejects unsafe filenames, streams each file to disk,
# and checks its exact size and full SHA-256 before it enters the image.
# The `python -` command makes Python read the heredoc below from standard
# input. Without it, Docker would give the source text to `/bin/sh` instead.
RUN python - <<'PY'
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import yaml


LOCK_PATH = Path("/app/model-artifacts.lock.yaml")
DESTINATION = Path("/opt/clouddsp/demucs-models")
ALLOWED_HOSTS = {"raw.githubusercontent.com", "dl.fbaipublicfiles.com"}
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CHUNK_SIZE = 1024 * 1024


def require(condition: bool, message: str) -> None:
    """Fail the Docker build with a useful message when the lock is malformed."""
    if not condition:
        raise RuntimeError(message)


def checked_artifacts(lock: dict[str, object]) -> list[dict[str, object]]:
    """Turn the compact model lock into individually verified download records."""
    models = lock.get("models")
    require(isinstance(models, dict) and models, "models must be a non-empty mapping")
    artifacts: list[dict[str, object]] = []
    for model_name, model in models.items():
        require(isinstance(model_name, str) and isinstance(model, dict), "invalid model record")
        for artifact_kind in ("descriptor", "weights"):
            artifact = model.get(artifact_kind)
            require(isinstance(artifact, dict), f"{model_name}.{artifact_kind} is missing")
            filename = artifact.get("filename")
            source_url = artifact.get("sourceUrl")
            sha256 = artifact.get("sha256")
            size_bytes = artifact.get("sizeBytes")
            require(
                isinstance(filename, str)
                and SAFE_FILENAME.fullmatch(filename) is not None
                and Path(filename).name == filename,
                f"{model_name}.{artifact_kind} has an unsafe filename",
            )
            parsed = urllib.parse.urlsplit(source_url) if isinstance(source_url, str) else None
            require(
                parsed is not None
                and parsed.scheme == "https"
                and parsed.hostname in ALLOWED_HOSTS
                and not parsed.query
                and not parsed.fragment,
                f"{model_name}.{artifact_kind} has an unapproved source URL",
            )
            require(
                isinstance(sha256, str) and re.fullmatch(r"[0-9a-f]{64}", sha256),
                f"{model_name}.{artifact_kind} has an invalid SHA-256",
            )
            require(
                isinstance(size_bytes, int) and size_bytes > 0,
                f"{model_name}.{artifact_kind} has an invalid size",
            )
            if artifact_kind == "weights":
                prefix = artifact.get("demucsFilenameChecksumPrefix")
                require(
                    isinstance(prefix, str)
                    and sha256.startswith(prefix)
                    and f"-{prefix}.th" in filename,
                    f"{model_name}.weights does not match Demucs' filename checksum convention",
                )
            artifacts.append(
                {
                    "filename": filename,
                    "source_url": source_url,
                    "sha256": sha256,
                    "size_bytes": size_bytes,
                }
            )
    return artifacts


def download_and_verify(artifact: dict[str, object], download_directory: Path) -> None:
    """Download one artifact only after exact byte-count and hash checks pass."""
    filename = artifact["filename"]
    source_url = artifact["source_url"]
    expected_sha256 = artifact["sha256"]
    expected_size = artifact["size_bytes"]
    require(isinstance(filename, str), "validated filename unexpectedly changed")
    require(isinstance(source_url, str), "validated source URL unexpectedly changed")
    require(isinstance(expected_sha256, str), "validated SHA-256 unexpectedly changed")
    require(isinstance(expected_size, int), "validated size unexpectedly changed")

    target = download_directory / filename
    hasher = hashlib.sha256()
    bytes_written = 0
    request = urllib.request.Request(source_url, headers={"User-Agent": "CloudDSP-Demucs-Image-Build/1"})
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as output:
        final_url = urllib.parse.urlsplit(response.geturl())
        require(
            final_url.scheme == "https" and final_url.hostname in ALLOWED_HOSTS,
            f"{filename} redirected to an unapproved host",
        )
        while chunk := response.read(CHUNK_SIZE):
            bytes_written += len(chunk)
            hasher.update(chunk)
            output.write(chunk)
    require(bytes_written == expected_size, f"{filename} has {bytes_written} bytes, expected {expected_size}")
    require(hasher.hexdigest() == expected_sha256, f"{filename} SHA-256 does not match the model lock")


lock = yaml.safe_load(LOCK_PATH.read_text(encoding="utf-8"))
require(isinstance(lock, dict) and lock.get("lockFormatVersion") == 1, "unsupported model lock format")
artifacts = checked_artifacts(lock)
require(not DESTINATION.exists(), f"model destination already exists: {DESTINATION}")
# The Python base has no application-owned `/opt/clouddsp` directory. Create
# only this fixed parent before the final atomic move; filenames remain checked.
DESTINATION.parent.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix="clouddsp-demucs-models-") as temporary_directory:
    downloaded = Path(temporary_directory)
    for artifact in artifacts:
        download_and_verify(artifact, downloaded)
    # This move means a partially verified collection can never reach the
    # runtime stage: either every locked artifact passed or the build fails.
    os.replace(downloaded, DESTINATION)

print(f"Verified and baked {len(artifacts)} Demucs model artifacts.")
PY

COPY app ./app
COPY tests ./tests
# The structural tests read the source recipe and the Deployment contract only.
# Keep both copies in this throwaway validation stage so a future build catches
# a changed PID-1, user, scratch-volume, or outbound-port boundary before an
# image is exported; neither file enters the final runtime stage.
COPY Dockerfile ./Dockerfile
COPY demucs-deployment.yaml ./demucs-deployment.yaml

# Unit tests exercise the pure adapters without a running cluster. The media
# command/import checks prove the pinned media and ML runtimes are present.
RUN PYTHONPATH=/app python -m unittest discover --start-directory tests --verbose
RUN ffprobe -version
RUN python -c "import demucs, torch, torchaudio; print(f'demucs={demucs.__version__} torch={torch.__version__} torchaudio={torchaudio.__version__}')"

# A successful import is not enough: the local k3d Linux/ARM64 CPU previously
# reached an illegal instruction only during actual model inference. Generate a
# deterministic two-second WAV, change to the same private output working
# directory used by the process adapter, run the same absolute launcher and
# arguments, and require both two-stem artifacts. This proves both the MKLDNN
# safeguard and the launcher's package-resolution boundary before model
# execution. The throwaway directory is removed in the same immutable build
# layer, so no test audio or output survives into the runtime image.
RUN python - <<'PY'
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


source = Path("/tmp/demucs-local-arm64-cpu-smoke/source.media")
source.parent.mkdir(parents=True, exist_ok=True)
# The production subprocess runner intentionally gives the child a freshly
# allocated output directory as its working directory. Create the matching
# empty directory here before the next validation instruction reproduces that
# boundary with `cd`; it is still removed with all other test artifacts.
(source.parent / "output").mkdir()
with wave.open(str(source), "wb") as output:
    output.setnchannels(1)
    output.setsampwidth(2)
    output.setframerate(8_000)
    samples = (
        int(12_000 * math.sin(2 * math.pi * 440 * index / 8_000))
        for index in range(16_000)
    )
    output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
PY
RUN cd /tmp/demucs-local-arm64-cpu-smoke/output \
    && /usr/local/bin/python /app/app/demucs_cpu_cli.py \
        --device cpu \
        --repo /opt/clouddsp/demucs-models \
        --name htdemucs \
        --out /tmp/demucs-local-arm64-cpu-smoke/output \
        --two-stems vocals \
        /tmp/demucs-local-arm64-cpu-smoke/source.media \
    && test -s /tmp/demucs-local-arm64-cpu-smoke/output/htdemucs/source/vocals.wav \
    && test -s /tmp/demucs-local-arm64-cpu-smoke/output/htdemucs/source/no_vocals.wav \
    && rm -rf /tmp/demucs-local-arm64-cpu-smoke


# The final image receives only runtime libraries, validated model files,
# FFmpeg, and worker source. It contains no test suite, model-lock tooling,
# credentials, or public network listener.
FROM base AS runtime

RUN groupadd --system --gid 10003 clouddsp-demucs \
    && useradd --system --uid 10003 --gid clouddsp-demucs \
        --create-home --home-dir /home/clouddsp-demucs \
        --shell /usr/sbin/nologin clouddsp-demucs

COPY --from=validation /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=validation --chown=root:root /opt/clouddsp/demucs-models /opt/clouddsp/demucs-models
COPY --from=validation --chown=clouddsp-demucs:clouddsp-demucs /app/app ./app

# The worker needs no root privileges: it makes only private outbound Service
# connections and the future Pod will supply a bounded writable ``emptyDir`` at
# `/worker-scratch`. Do not create that directory here. Creating it in the
# image would mask a missing Pod volume mount and let temporary source/stem
# media fall back to the container filesystem.
USER clouddsp-demucs

# Exec form makes Python PID 1 rather than a child of a shell. Kubernetes can
# therefore deliver SIGTERM directly to the reviewed worker wrapper, whose
# scoped shutdown handler stops new cycles and allows AMQP cleanup to run.
ENTRYPOINT ["python", "-m", "app.worker_main"]
