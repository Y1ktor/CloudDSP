# syntax=docker/dockerfile:1.7

# This image contains the capability lifecycle broker, authenticated-ingress
# client, and read-only PostgreSQL/MinIO/RabbitMQ/KEDA observer for the bounded
# six-stem load test. It is not a DSP worker or browser. Its MinIO CLI is used
# only by the broker to provision/revoke one exact-object read identity.
# `images.api-python-runtime` in ../../../images.lock.yaml pins this official
# multi-architecture Python base for the local ARM64 k3d profile.
# Reuse the project's reviewed digest-pinned MinIO CLI image instead of
# downloading an executable in this build. The runtime stage copies only mc.
FROM clouddsp-registry.localhost:5001/minio-mc@sha256:37d109dddbbb2c95873f5fc81ac93f37023264770fc580a7564148892087b1b7 AS minio-cli

FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS dependencies

# Runtime source uses the standard library, hash-locked Psycopg, and Boto3.
# Disabling bytecode writes lets the final non-root
# container use a read-only root filesystem; unbuffered output makes only its
# intentionally safe progress lines visible in Job logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install the complete hash-pinned dependency closure. Psycopg's binary wheel
# provides libpq; Boto3 is limited to known-key MinIO Head/Get operations.
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes --requirement requirements.lock

FROM dependencies AS validation

WORKDIR /app

# Copy the exact runtime graph plus its existing offline tests. The tests run
# during image construction; the final Pod performs no package installation.
COPY keycloak_identity_lifecycle.py ./keycloak_identity_lifecycle.py
COPY lifecycle_handoff.py ./lifecycle_handoff.py
COPY lifecycle_broker.py ./lifecycle_broker.py
COPY observer_capability_contract.py ./observer_capability_contract.py
COPY postgresql_admin_boundary.py ./postgresql_admin_boundary.py
COPY postgresql_observer_contract.py ./postgresql_observer_contract.py
COPY postgresql_observer_bootstrap.py ./postgresql_observer_bootstrap.py
COPY postgresql_durable_state_observer.py ./postgresql_durable_state_observer.py
COPY postgresql_observer_report.py ./postgresql_observer_report.py
COPY load_test_terminal_outcome.py ./load_test_terminal_outcome.py
COPY minio_observer_bootstrap.py ./minio_observer_bootstrap.py
COPY minio_artifact_observer.py ./minio_artifact_observer.py
COPY rabbitmq_queue_observer.py ./rabbitmq_queue_observer.py
COPY keda_scale_observer.py ./keda_scale_observer.py
COPY observer_start_gate.py ./observer_start_gate.py
COPY private_volume_setup.py ./private_volume_setup.py
COPY six_stem_load_observer.py ./six_stem_load_observer.py
COPY authenticated_load_client.py ./authenticated_load_client.py
COPY owner_bound_job_verifier.py ./owner_bound_job_verifier.py
COPY test_keycloak_identity_lifecycle.py ./test_keycloak_identity_lifecycle.py
COPY test_lifecycle_handoff.py ./test_lifecycle_handoff.py
COPY test_authenticated_load_client.py ./test_authenticated_load_client.py
COPY test_owner_bound_job_verifier.py ./test_owner_bound_job_verifier.py
COPY test_observer_capability_contract.py ./test_observer_capability_contract.py
COPY test_postgresql_observer_contract.py ./test_postgresql_observer_contract.py
COPY test_postgresql_durable_state_observer.py ./test_postgresql_durable_state_observer.py
COPY test_postgresql_admin_boundary.py ./test_postgresql_admin_boundary.py
COPY test_postgresql_observer_report.py ./test_postgresql_observer_report.py
COPY test_load_test_terminal_outcome.py ./test_load_test_terminal_outcome.py
COPY test_minio_observer_bootstrap.py ./test_minio_observer_bootstrap.py
COPY test_minio_artifact_observer.py ./test_minio_artifact_observer.py
COPY test_rabbitmq_queue_observer.py ./test_rabbitmq_queue_observer.py
COPY test_keda_scale_observer.py ./test_keda_scale_observer.py
COPY test_observer_start_gate.py ./test_observer_start_gate.py
COPY test_private_volume_setup.py ./test_private_volume_setup.py
COPY test_six_stem_load_observer.py ./test_six_stem_load_observer.py
COPY requirements.lock ./requirements.lock
COPY Dockerfile ./Dockerfile

# Tests use fake HTTP/lifecycle objects and temporary local directories only.
# They prove direct-grant configuration, private handoff shape, cleanup order,
# and SIGTERM-capable broker control flow without a cluster or any Secret.
RUN python -W error::ResourceWarning -m unittest discover \
    --start-directory /app \
    --pattern 'test_*.py' \
    --quiet

FROM dependencies AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Docker Official Python starts as root. The Job Pod repeats this dedicated
# UID/GID, mounts two purpose-specific emptyDirs, and makes its root filesystem
# read-only. This account has no writable home, shell, Docker socket,
# Kubernetes ServiceAccount token, or ambient cloud credential.
RUN groupadd --gid 10012 clouddsp-six-stem-load \
    && useradd --uid 10012 --gid clouddsp-six-stem-load --no-create-home \
      --shell /usr/sbin/nologin clouddsp-six-stem-load

# Tests, Dockerfile, build cache, administrator Secrets, endpoints, and all
# data-plane DSP dependencies stay out of the runtime image.
COPY --from=validation --chown=10012:10012 /app/keycloak_identity_lifecycle.py ./keycloak_identity_lifecycle.py
COPY --from=validation --chown=10012:10012 /app/lifecycle_handoff.py ./lifecycle_handoff.py
COPY --from=validation --chown=10012:10012 /app/lifecycle_broker.py ./lifecycle_broker.py
COPY --from=validation --chown=10012:10012 /app/observer_capability_contract.py ./observer_capability_contract.py
COPY --from=validation --chown=10012:10012 /app/postgresql_admin_boundary.py ./postgresql_admin_boundary.py
COPY --from=validation --chown=10012:10012 /app/postgresql_observer_contract.py ./postgresql_observer_contract.py
COPY --from=validation --chown=10012:10012 /app/postgresql_observer_bootstrap.py ./postgresql_observer_bootstrap.py
COPY --from=validation --chown=10012:10012 /app/postgresql_durable_state_observer.py ./postgresql_durable_state_observer.py
COPY --from=validation --chown=10012:10012 /app/postgresql_observer_report.py ./postgresql_observer_report.py
COPY --from=validation --chown=10012:10012 /app/load_test_terminal_outcome.py ./load_test_terminal_outcome.py
COPY --from=validation --chown=10012:10012 /app/minio_observer_bootstrap.py ./minio_observer_bootstrap.py
COPY --from=validation --chown=10012:10012 /app/minio_artifact_observer.py ./minio_artifact_observer.py
COPY --from=validation --chown=10012:10012 /app/rabbitmq_queue_observer.py ./rabbitmq_queue_observer.py
COPY --from=validation --chown=10012:10012 /app/keda_scale_observer.py ./keda_scale_observer.py
COPY --from=validation --chown=10012:10012 /app/observer_start_gate.py ./observer_start_gate.py
# The Job's brief root init container invokes this fixed-path chmod adapter
# before any Secret-bearing, non-root application container can start.
COPY --from=validation --chown=10012:10012 /app/private_volume_setup.py ./private_volume_setup.py
COPY --from=validation --chown=10012:10012 /app/six_stem_load_observer.py ./six_stem_load_observer.py
# The same minimal image supplies the authenticated client container. Its
# default entrypoint remains the broker, so the Job explicitly uses
# ``python /app/authenticated_load_client.py`` for the unprivileged container.
COPY --from=validation --chown=10012:10012 /app/authenticated_load_client.py ./authenticated_load_client.py
# This broker-only adapter reuses no administrator API. It trades the private
# coordinate claim for exactly three ordinary owner-bound Job API reads before
# broker-only code creates each separately scoped observer capability.
COPY --from=validation --chown=10012:10012 /app/owner_bound_job_verifier.py ./owner_bound_job_verifier.py
# These report contracts are available to the broker and evidence-observer /
# orchestrator containers. The PostgreSQL report is one intermediate snapshot;
# the distinct terminal-outcome file is required before the broker may revoke
# temporary credentials. Neither module reads service credentials or cleans
# up durable application work.

# Parse the final copied modules before publishing. This prevents a future
# copy-stage edit from putting syntactically invalid source into the registry.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path(".").glob("*.py")]'

USER 10012:10012

# `mc` is the exact digest-locked CLI from the official MinIO image. It is
# invoked only by the privileged broker; the observer never receives root
# credentials and uses Boto3 with a temporary exact-key user.
COPY --from=minio-cli --chown=10012:10012 /usr/bin/mc /usr/local/bin/mc

# No process listens on a network port. The suspended Job overrides this
# default entrypoint for its separate authenticated client and observer.
ENTRYPOINT ["python", "/app/lifecycle_broker.py"]
