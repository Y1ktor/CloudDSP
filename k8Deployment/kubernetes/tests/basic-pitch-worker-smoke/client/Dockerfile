# This disposable image runs one bounded, least-privilege integration test:
#
#   controlled MinIO WAV -> PostgreSQL outbox -> deployed dispatcher/worker
#       -> PostgreSQL task success + private MinIO MIDI
#
# It is a one-shot Kubernetes Job client, not a Basic Pitch worker, a broker,
# an API, or a general PostgreSQL/MinIO administration image. The pinned
# Docker Official Python base is recorded as `images.api-python-runtime` in
# ../../../images.lock.yaml.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS validation

# A later Job will have a read-only root filesystem. These settings keep Python
# from trying to create bytecode in /app and flush only the smoke client's
# deliberately non-sensitive milestone logs for `kubectl logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install only the complete hash-enforced S3/PostgreSQL dependency closure.
# `--require-hashes` makes the build stop if an index serves an artifact other
# than the exact reviewed wheel in requirements.lock. The installed packages
# are copied to the final stage, so the credential-bearing runtime never needs
# a package download, compiler, or PostgreSQL development headers.
COPY requirements.lock ./requirements.lock
RUN python -m pip install \
    --root-user-action=ignore \
    --no-cache-dir \
    --no-compile \
    --require-hashes \
    --requirement requirements.lock

# Unit tests remain in this temporary validation stage. Their fake clients
# prove fixed-coordinate storage, function-only database access, worker-result
# evidence, bounded polling, and cleanup ordering without a cluster service.
COPY basic_pitch_worker_smoke.py ./basic_pitch_worker_smoke.py
COPY test_basic_pitch_worker_smoke.py ./test_basic_pitch_worker_smoke.py
RUN python -m unittest discover --pattern 'test_*.py' --verbose

# Build the runtime from the same immutable Python image. This avoids making a
# different distribution's libc/Python ABI an accidental dependency of the
# reviewed Psycopg binary wheel.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only already hash-verified runtime packages. Tests and the dependency
# lock are intentionally excluded from the container that later receives
# Kubernetes Secrets.
COPY --from=validation /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Docker Official Python images begin as root. This explicit account matches
# the planned Pod security context yet has no home directory to persist state.
# It needs no shell, privileged port, Docker socket, Kubernetes API token, or
# writable root filesystem—only its private service connections at runtime.
RUN groupadd --gid 10003 clouddspbasicpitchsmoke \
    && useradd --uid 10003 --gid clouddspbasicpitchsmoke --no-create-home \
      --shell /usr/sbin/nologin clouddspbasicpitchsmoke

# The sole runtime module has no credentials, endpoint values, object keys,
# queue access, or database SQL beyond its fixed functions. Those values are
# supplied by the future Job through the already-restricted Kubernetes Secrets.
COPY --from=validation --chown=10003:10003 /app/basic_pitch_worker_smoke.py ./basic_pitch_worker_smoke.py

# Compile in memory only. A syntax error fails the local image build before an
# invalid image can be pushed, while no root-owned `.pyc` file remains in /app.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path(".").glob("*.py")]'

USER 10003:10003

# The process executes the bounded sequence once and exits. It has no HTTP
# listener, so its eventual Job needs neither a Service nor an Ingress.
ENTRYPOINT ["python", "/app/basic_pitch_worker_smoke.py"]
