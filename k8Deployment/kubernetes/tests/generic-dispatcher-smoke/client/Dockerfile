# This disposable image executes one restricted generic-dispatcher Basic Pitch
# routing smoke test, then exits. It is not a worker, API, RabbitMQ broker, or
# general PostgreSQL administration image. The pinned Docker Official Python
# base is recorded as `images.api-python-runtime` in images.lock.yaml.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS validation

# A later Job will use `readOnlyRootFilesystem: true`. Disabling bytecode files
# prevents Python from trying to write beneath /app at runtime; unbuffered
# output makes short, intentionally non-sensitive test progress visible via
# `kubectl logs` immediately.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install only the reviewed, hash-locked client dependencies. `--require-hashes`
# makes the build fail if PyPI serves any artifact other than the source lock's
# exact SHA-256 values, rather than resolving a newer transitive version.
COPY requirements.lock ./requirements.lock
RUN python -m pip install \
    --root-user-action=ignore \
    --no-cache-dir \
    --require-hashes \
    --requirement requirements.lock

# Unit tests remain only in this temporary validation stage. They prove the
# function-only SQL boundary, exact AMQP contract, requeue safety, bounded
# polling, and cleanup order without connecting to a cluster service.
COPY generic_dispatcher_basic_pitch_smoke.py ./generic_dispatcher_basic_pitch_smoke.py
COPY test_generic_dispatcher_basic_pitch_smoke.py ./test_generic_dispatcher_basic_pitch_smoke.py
RUN python -m unittest discover --pattern 'test_*.py' --verbose

# Use the same immutable Python base for the final image. Copying the already
# validated packages avoids a second dependency download and deliberately keeps
# the test module and requirements lock out of the credential-bearing runtime.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=validation /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Docker Official Python images start as root. This explicit numeric account
# will match the later Pod security context. It needs no shell, privileged
# port, Docker socket, Kubernetes API token, or writable root filesystem.
RUN groupadd --gid 10002 clouddspgenericdispatchersmoke \
    && useradd --uid 10002 --gid clouddspgenericdispatchersmoke \
      --no-create-home --shell /usr/sbin/nologin clouddspgenericdispatchersmoke

# The final image contains exactly the client module and already-validated
# runtime packages. Secrets, endpoints, queue names, and event identifiers are
# supplied only by a later Kubernetes Job, never baked into an image layer.
COPY --from=validation --chown=10002:10002 /app/generic_dispatcher_basic_pitch_smoke.py ./generic_dispatcher_basic_pitch_smoke.py

# Compile without creating root-owned .pyc files. This makes a syntax error a
# deterministic local image-build failure before an invalid image can be pushed.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path(".").glob("*.py")]'

USER 10002:10002

# The entrypoint has no HTTP listener, so its later one-shot Job needs neither
# a Kubernetes Service nor Ingress. It runs the bounded route check once.
ENTRYPOINT ["python", "/app/generic_dispatcher_basic_pitch_smoke.py"]
