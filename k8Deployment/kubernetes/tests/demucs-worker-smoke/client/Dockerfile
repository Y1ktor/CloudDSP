# The disposable image contains one integration-test client, not an audio
# worker or cluster tool. It stimulates a durable event and observes actual
# deployed infrastructure through restricted PostgreSQL and MinIO credentials.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS validation

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

# Install only the reviewed, hash-locked S3/PostgreSQL runtime closure. The
# final stage copies installed wheels and therefore never downloads packages
# after a Pod receives a Secret.
COPY requirements.lock ./requirements.lock
RUN python -m pip install \
    --root-user-action=ignore \
    --no-cache-dir \
    --no-compile \
    --require-hashes \
    --requirement requirements.lock

# Unit tests are validation-stage only. Fake clients prove exact coordinates,
# durable observation predicates, artifact verification, and cleanup ordering
# without a live cluster or administrator credential.
COPY demucs_worker_smoke.py ./demucs_worker_smoke.py
COPY test_demucs_worker_smoke.py ./test_demucs_worker_smoke.py
RUN python -m unittest discover --pattern 'test_*.py' --verbose

FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

# Keep test modules and the dependency lock out of the credential-bearing
# runtime image; only the already hash-verified site packages are transferred.
COPY --from=validation /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# The Pod security context repeats this UID/GID and requires a non-root process
# even if a later base-image change would otherwise default to root.
RUN groupadd --gid 10003 clouddspdemucssmoke \
    && useradd --uid 10003 --gid clouddspdemucssmoke --no-create-home \
      --shell /usr/sbin/nologin clouddspdemucssmoke
COPY --from=validation --chown=10003:10003 /app/demucs_worker_smoke.py ./demucs_worker_smoke.py
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path(".").glob("*.py")]'

USER 10003:10003
# The bounded test exits after one run. It has no listening socket, so no
# Kubernetes Service, Ingress, port declaration, or ServiceAccount is needed.
ENTRYPOINT ["python", "/app/demucs_worker_smoke.py"]
