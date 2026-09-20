# syntax=docker/dockerfile:1.7

# This disposable image runs one bounded three-request Basic Pitch KEDA burst:
#
#   three controlled MinIO WAVs -> fixed PostgreSQL outbox events -> deployed
#   dispatcher + KEDA + workers -> verified private MIDI -> guarded cleanup
#
# It is a finite Kubernetes Job client, not a Basic Pitch worker, web API,
# RabbitMQ client, PostgreSQL administrator, or Kubernetes API client. The
# pinned Docker Official Python base is `images.api-python-runtime` in
# ../../../images.lock.yaml. The requirements lock contains the reviewed
# CPython 3.12 Linux/ARM64 Psycopg wheel, so a future build must explicitly use
# that platform rather than weakening hash enforcement on another architecture.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS validation

# A later Job uses a read-only root filesystem. These settings stop Python
# from writing bytecode beneath /app and make only intentionally safe progress
# logs immediately visible through `kubectl logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# The lock enumerates every direct/transitive package. `--no-deps` prevents
# pip from silently resolving an unreviewed extra package, while
# `--require-hashes` rejects changed artifact bytes. Package download happens
# before any Kubernetes Secret exists and only in this throwaway stage.
COPY requirements.lock ./requirements.lock
RUN python -m pip install \
    --root-user-action=ignore \
    --no-cache-dir \
    --no-compile \
    --no-deps \
    --require-hashes \
    --requirement requirements.lock

# Tests use fakes and local module imports only. They prove fixed-coordinate
# storage/function boundaries, safe post-prepare retention, explicit SDK
# factories, and the Dockerfile's own least-privilege structure without a
# cluster, service connection, credential, or worker model.
COPY basic_pitch_keda_burst_smoke.py ./basic_pitch_keda_burst_smoke.py
COPY minio_adapter.py ./minio_adapter.py
COPY postgresql_adapter.py ./postgresql_adapter.py
COPY burst_runner.py ./burst_runner.py
COPY burst_entrypoint.py ./burst_entrypoint.py
COPY test_*.py ./
COPY Dockerfile ./Dockerfile
RUN python -W error::ResourceWarning -m unittest discover \
    --start-directory /app \
    --pattern 'test_*.py' \
    --quiet

# The runtime repeats the exact immutable base, preserving the reviewed Python
# ABI for the copied ARM64 Psycopg binary wheel. It downloads no package and
# contains neither tests nor the requirements lock before a future Job mounts
# its two scoped runtime Secrets.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=validation /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Docker Official Python starts as root. This dedicated account has no writable
# home, shell, privileged port, Docker socket, Kubernetes token, or service
# account authority. The later Pod uses the same numeric identity with a
# read-only root filesystem and can mount only its app-namespace Secrets.
RUN groupadd --gid 10007 clouddsp-basic-pitch-keda-burst \
    && useradd --uid 10007 --gid clouddsp-basic-pitch-keda-burst \
        --no-create-home --shell /usr/sbin/nologin clouddsp-basic-pitch-keda-burst

# Copy precisely the production execution graph. Unit tests, lock source,
# Dockerfile, build cache, credentials, endpoints, and arbitrary files remain
# outside this final image. Runtime configuration comes only from the later
# Secret-backed environment variables that BurstSettings validates.
COPY --from=validation --chown=10007:10007 /app/basic_pitch_keda_burst_smoke.py ./basic_pitch_keda_burst_smoke.py
COPY --from=validation --chown=10007:10007 /app/minio_adapter.py ./minio_adapter.py
COPY --from=validation --chown=10007:10007 /app/postgresql_adapter.py ./postgresql_adapter.py
COPY --from=validation --chown=10007:10007 /app/burst_runner.py ./burst_runner.py
COPY --from=validation --chown=10007:10007 /app/burst_entrypoint.py ./burst_entrypoint.py

# Parse every final runtime module without retaining root-owned .pyc files. A
# syntax error consequently fails a local image build before invalid bytes can
# reach the local registry.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path(".").glob("*.py")]'

USER 10007:10007

# Exec-form Python becomes PID 1. The entrypoint performs one finite test run,
# preserves post-prepare evidence on failure, and exits; its future Job needs
# no Service, Ingress, RabbitMQ credential, or Kubernetes API permission.
ENTRYPOINT ["python", "/app/burst_entrypoint.py"]
