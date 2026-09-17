# syntax=docker/dockerfile:1.7

# This disposable image runs exactly one bounded ADTOF pipeline verification:
#
#   controlled private drums WAV -> restricted PostgreSQL event -> deployed
#   dispatcher + ADTOF worker -> verified private MIDI/tempo -> guarded cleanup
#
# It is a finite Kubernetes Job client, not an ADTOF worker, HTTP service,
# RabbitMQ client, administrator tool, or Kubernetes API client. The immutable
# Docker Official Python base is `images.api-python-runtime` in
# ../../../images.lock.yaml. The requirements lock contains a CPython 3.12
# Linux/ARM64 Psycopg wheel, so a future build must explicitly target
# `linux/arm64`; it must not weaken hash checking to build a different platform.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS validation

# The future Job will use a read-only root filesystem. Disable Python bytecode
# files in `/app` and flush only deliberately non-sensitive progress messages
# promptly to `kubectl logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install the entire reviewed S3/PostgreSQL dependency closure at build time.
# The lock names every transitive package; `--no-deps` therefore fails closed if
# it becomes incomplete, while `--require-hashes` rejects changed package bytes.
# The final stage copies these verified packages and never downloads packages
# after Kubernetes has supplied runtime Secrets to the Pod.
COPY requirements.lock ./requirements.lock
RUN python -m pip install \
    --root-user-action=ignore \
    --no-cache-dir \
    --no-compile \
    --no-deps \
    --require-hashes \
    --requirement requirements.lock

# Tests belong solely to this throwaway validation stage. They use in-memory
# adapters and patched SDK/driver imports, so Docker build verifies the client
# contract without connecting to PostgreSQL, MinIO, RabbitMQ, Keycloak, or a
# Kubernetes cluster. Copy the Dockerfile too because its structural test
# verifies that this runtime stays digest-pinned and least-privilege.
COPY adtof_worker_smoke_*.py ./
COPY test_*.py ./
COPY Dockerfile ./Dockerfile
RUN python -W error::ResourceWarning -m unittest discover \
    --start-directory /app \
    --pattern 'test_*.py' \
    --quiet

# Use the exact same immutable Python distribution for runtime so the copied
# CPython 3.12 site-packages tree, including the ARM64 Psycopg binary wheel,
# keeps its reviewed ABI. This stage intentionally contains no test source,
# requirement lock, package installer, compiler, shell script, or credentials.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=validation /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Docker Official Python images begin as root. The dedicated numeric account
# has no writable home and needs no privileged port, Docker socket, Kubernetes
# API token, or shell. The future Pod can mount only its deliberately scoped
# Secrets and must supply any writable temporary volume explicitly.
RUN groupadd --gid 10006 clouddsp-adtof-smoke \
    && useradd --uid 10006 --gid clouddsp-adtof-smoke \
        --no-create-home --shell /usr/sbin/nologin clouddsp-adtof-smoke

# Copy exactly the bounded runtime graph, not tests or the dependency lock.
# Runtime credentials, endpoints, fixed identities, and timeouts enter only
# through Kubernetes Secret-backed environment variables when a future Job runs.
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_composition.py ./
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_contract.py ./
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_database.py ./
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_entrypoint.py ./
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_fixture.py ./
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_minio.py ./
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_minio_cleanup.py ./
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_minio_input.py ./
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_minio_outputs.py ./
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_orchestration.py ./
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_postgresql.py ./
COPY --from=validation --chown=10006:10006 /app/adtof_worker_smoke_runtime.py ./

# Parse every runtime module during the image build without creating root-owned
# `.pyc` files. A syntax error therefore prevents pushing a broken image.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path(".").glob("adtof_worker_smoke_*.py")]'

USER 10006:10006

# Exec form makes Python PID 1. The bounded entrypoint runs once, preserves
# failure evidence when appropriate, performs only approved success cleanup,
# and exits; the future workload needs neither a Service nor an Ingress.
ENTRYPOINT ["python", "/app/adtof_worker_smoke_entrypoint.py"]
