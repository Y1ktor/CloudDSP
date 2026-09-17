# syntax=docker/dockerfile:1.7

# This source-only recipe targets the local k3d Linux/ARM64 CPU profile.  The
# immutable official Python 3.11 base is recorded as
# `images.basic-pitch-python-runtime` in `kubernetes/images.lock.yaml`; that
# image index has an ARM64 child for the current Apple-Silicon Docker Desktop
# VM.  A future build must explicitly select `--platform linux/arm64`.
#
# This is intentionally not a CUDA, NVIDIA, Apple-MPS, cloud, or Kubernetes
# client image.  The ADTOF model runs as ordinary CPU PyTorch code inside the
# Pod and talks only to the private Services configured by its future manifest.
FROM docker.io/library/python@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84 AS validation

# A future Pod uses a read-only root filesystem.  Disable bytecode writes and
# make controlled worker diagnostics appear promptly in `kubectl logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy the complete lock before source code so a later image rebuild can reuse
# the expensive verified dependency layer when only worker code changes.  The
# two source distributions need the reviewed `setuptools`/`wheel` tools before
# the main install.  The small AWK extraction reads those already hash-pinned
# entries; it does not resolve a version, alter the source lock, or access any
# credential.
COPY requirements.lock ./requirements.lock
RUN awk '/^(setuptools|wheel)==/{copying=1} copying { print } copying && /--hash=/{copying=0}' requirements.lock \
        > /tmp/adtof-build-tools.lock \
    && python -m pip install \
        --root-user-action=ignore \
        --no-cache-dir \
        --no-deps \
        --require-hashes \
        --requirement /tmp/adtof-build-tools.lock \
    && rm /tmp/adtof-build-tools.lock

# `--no-deps` makes the lock fail closed if any transitive package is removed.
# `--require-hashes` checks the exact archive bytes, including the reviewed
# ADTOF and PrettyMIDI source archives.  Build isolation stays disabled so a
# source build cannot fetch mutable build requirements outside this lock.  The
# official CPU index provides the ARM64 PyTorch CPU wheel; the package version
# has no `+cpu` suffix on that architecture.
RUN python -m pip install \
        --root-user-action=ignore \
        --no-cache-dir \
        --no-deps \
        --no-build-isolation \
        --require-hashes \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        --requirement requirements.lock

# Tests remain in this throwaway validation stage.  Their fakes test durable
# task ownership and cooperative shutdown without credentials, a broker, a
# database, MinIO data, real model inference, or a Kubernetes resource. Two
# structural tests also inspect the source Dockerfile and administrator-owned
# bootstrap manifest, so copy those source-only inputs here. They never cross
# into the final runtime stage.
COPY app ./app
COPY tests ./tests
COPY Dockerfile ./Dockerfile
COPY adtof-database-bootstrap-job.yaml ./adtof-database-bootstrap-job.yaml
# The Deployment structural test reads this prepared manifest from the
# throwaway validation stage only. It is not copied into the runtime image and
# the Docker build still never applies it to Kubernetes.
COPY adtof-deployment.yaml ./adtof-deployment.yaml
# This source-only runtime-policy smoke Job is also structurally inspected by
# the test suite. It stays in this throwaway validation stage and is never a
# file in the deployed ADTOF worker image.
COPY adtof-minio-runtime-policy-smoke-job.yaml ./adtof-minio-runtime-policy-smoke-job.yaml
# The full repository-level image catalog is deliberately outside this small
# service-only build context. Its dedicated structural test recognizes this
# explicit nonexistent path and skips only that catalog assertion here; source
# checkout tests still validate it against the real catalog.
RUN CLOUDDSP_IMAGE_LOCK_PATH=/app/.image-lock-not-in-build-context \
    PYTHONPATH=/app python -m unittest discover --start-directory tests --verbose

# Confirm that the installed model package, its bundled model weights, and the
# principal CPU/audio/MIDI imports exist.  This is an import-only inspection:
# it runs no transcription and a future runtime Pod never downloads weights.
RUN python - <<'PY'
from importlib.util import find_spec
from pathlib import Path

import librosa
import pretty_midi
import torch

package = find_spec("adtof_pytorch")
if package is None or package.origin is None:
    raise RuntimeError("the reviewed ADTOF package is not importable")
weight_files = tuple(Path(package.origin).resolve().parent.rglob("*.pth"))
if not weight_files:
    raise RuntimeError("the reviewed ADTOF package has no bundled model weights")
print(
    "ADTOF dependency imports verified; "
    f"torch={torch.__version__}; bundled_weights={len(weight_files)}; "
    f"librosa={librosa.__version__}; pretty_midi={pretty_midi.__version__}"
)
PY


# The runtime starts from the identical immutable base and copies only the
# verified Python packages and worker source.  It intentionally excludes test
# code, requirement files, build helper files, credentials, and model caches.
FROM docker.io/library/python@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/clouddsp-adtof \
    NUMBA_CACHE_DIR=/tmp/numba-cache \
    NUMBA_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /app

# The hash-pinned Python ``soundfile`` package deliberately links to the
# system-provided libsndfile shared library instead of bundling a second native
# copy.  ADTOF reaches it through Librosa when decoding the already-verified
# WAV stem.  Keep this Debian runtime dependency explicit and install no
# recommended packages; without it, model imports succeed but every real
# transcription fails at audio decode with ``libsndfile.so`` missing.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Both stages use the same Python 3.11 digest, so this is the exact matching
# site-packages location.  There is no ADTOF console executable: the reviewed
# `app.worker_main` module is the one process entrypoint for the worker.
COPY --from=validation /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# The worker has no reason to run as root: it makes only private outbound
# Service connections and later receives writable `emptyDir` mounts from its
# Deployment.  Do not make `/worker-scratch` here.  Creating it in the image
# would mask a missing required scratch-volume mount and allow temporary media
# to fall back to the container filesystem.
RUN groupadd --system --gid 10005 clouddsp-adtof \
    && useradd --system --uid 10005 --gid clouddsp-adtof \
        --create-home --home-dir /home/clouddsp-adtof \
        --shell /usr/sbin/nologin clouddsp-adtof

COPY --from=validation --chown=10005:10005 /app/app ./app

# Compile every worker module without writing `.pyc` files.  A syntax error is
# therefore found during image build, before the image can be pushed or a Pod
# can attempt to start it.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path("app").glob("*.py")]'

USER 10005:10005

# Exec form makes Python PID 1, allowing Kubernetes SIGTERM to reach the
# reviewed shutdown wrapper directly rather than first reaching a shell.
ENTRYPOINT ["python", "-m", "app.worker_main"]
