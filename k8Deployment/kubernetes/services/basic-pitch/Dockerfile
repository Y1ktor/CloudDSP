# syntax=docker/dockerfile:1.7

# This image is deliberately the local Apple-Silicon / Linux ARM64 CPU profile.
# Basic Pitch 0.4.0 requires the TensorFlow 2.15 Python 3.11 runtime, so it
# uses the dedicated immutable Python base recorded in `images.lock.yaml`.
# There is no CUDA, NVIDIA driver, Apple-MPS bridge, Kubernetes client, cloud
# credential, public listener, or baked Secret in this image.
FROM docker.io/library/python@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84 AS validation

# A future Pod will use a read-only root filesystem. Do not create `.pyc`
# files there, and flush only intentionally small operational diagnostics so
# `kubectl logs` reflects a controlled worker exit promptly.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy the lock before worker code so Docker can reuse the expensive verified
# TensorFlow/audio dependency layer when only application source changes. The
# complete closure is explicit, so `--no-deps` prevents a future resolver from
# selecting a newer transitive package. `--ignore-installed` makes the build
# verify even Python-base tooling listed in the lock rather than trusting a
# mutable preinstalled version. `--only-binary` rejects an unexpected source
# build, which would otherwise introduce an unpinned compiler/toolchain path.
COPY requirements.lock ./requirements.lock
RUN python -m pip install \
        --root-user-action=ignore \
        --no-cache-dir \
        --ignore-installed \
        --no-deps \
        --only-binary=:all: \
        --require-hashes \
        --requirement requirements.lock

# Unit tests stay in this throwaway stage. Their fakes prove the worker's
# durable-task and shutdown boundaries without a cluster, mounted Secret,
# broker, database, MinIO object, or real audio inference request.
COPY app ./app
COPY tests ./tests
RUN PYTHONPATH=/app python -m unittest discover --start-directory tests --verbose

# Fail the build if the exact console executable expected by
# `basic_pitch_process.py`, the CPU inference library, or the package-bundled
# TFLite model is missing. This performs no model inference and downloads no
# runtime artifact; a worker Pod must never fetch models while processing work.
RUN python - <<'PY'
from importlib.util import find_spec
from pathlib import Path
from shutil import which

import tensorflow

executable = which("basic-pitch")
if executable != "/usr/local/bin/basic-pitch":
    raise RuntimeError("Basic Pitch console executable is not installed at the reviewed path")
basic_pitch_spec = find_spec("basic_pitch")
if basic_pitch_spec is None or basic_pitch_spec.origin is None:
    raise RuntimeError("Basic Pitch package is not importable from the reviewed wheel")
model_files = tuple(Path(basic_pitch_spec.origin).resolve().parent.rglob("*.tflite"))
if not model_files:
    raise RuntimeError("Basic Pitch wheel does not contain its required TFLite model")
print(f"basic-pitch executable verified; TensorFlow {tensorflow.__version__}; bundled models={len(model_files)}")
PY


# The final image begins from the same immutable base, then copies only
# validated runtime packages, the reviewed CLI, and worker source. Test files,
# requirements metadata, and build-only state do not cross this boundary.
FROM docker.io/library/python@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Both stages use Python 3.11 from the identical image digest, so this is the
# interpreter's exact global package directory. It includes only the verified
# wheel closure—not tests, credentials, or an application-specific cache.
COPY --from=validation /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=validation /usr/local/bin/basic-pitch /usr/local/bin/basic-pitch

# The worker needs no root privileges: it makes only private outbound Service
# connections and later receives a writable `emptyDir` exclusively at
# `/worker-scratch`. The image intentionally does *not* create that directory:
# doing so would mask a missing Pod volume mount and allow temporary media to
# fall back to the image filesystem. The future Deployment owns the mount and
# its fsGroup/write permission.
RUN groupadd --system --gid 10004 clouddsp-basic-pitch \
    && useradd --system --uid 10004 --gid clouddsp-basic-pitch \
        --create-home --home-dir /home/clouddsp-basic-pitch \
        --shell /usr/sbin/nologin clouddsp-basic-pitch

COPY --from=validation --chown=10004:10004 /app/app ./app

# Compile each worker module without creating `.pyc` output. A syntax error
# therefore blocks a registry push rather than becoming a failing Pod later.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path("app").glob("*.py")]'

USER 10004:10004

# Exec-form ENTRYPOINT makes Python the container's PID 1 process. Kubernetes
# therefore delivers SIGTERM directly to `worker_main`, which translates it
# into the cooperative shutdown event already tested in this worker source.
ENTRYPOINT ["python", "-m", "app.worker_main"]
