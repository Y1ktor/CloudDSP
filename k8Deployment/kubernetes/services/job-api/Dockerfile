# This is the official Python image pinned by immutable OCI index digest. It
# stays on Python 3.12 to match the existing cloud Job API's language runtime;
# moving the local API to another Python version is a separate compatibility
# decision, not an incidental container-base update.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS validation

# Avoid bytecode files on a future read-only root filesystem, and make Python
# logs immediately observable through `kubectl logs` instead of buffering them.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The fully pinned, hashed lock must be copied separately from application code
# so a future code-only image build can reuse the dependency layer. `pip` fails
# closed if PyPI returns a package different from the reviewed hash.
COPY requirements.lock ./requirements.lock
RUN python -m pip install \
    --root-user-action=ignore \
    --no-cache-dir \
    --require-hashes \
    --requirement requirements.lock

# Application source and tests enter this temporary validation stage together.
# The following runtime stage copies only the validated application and Python
# packages, so unit-test files and their generated artifacts never ship in the
# production-like Job API image.
COPY app ./app
COPY tests ./tests
# These tests inspect the immutable migration source and its one-shot Job. They
# are build-only fixtures: the runtime stage copies just /app/app, so neither
# Kubernetes manifest is shipped inside the live API image.
COPY job-api-schema-migration-v007-job-finalization-configmap.yaml ./job-api-schema-migration-v007-job-finalization-configmap.yaml
COPY job-api-schema-migration-v007-job-finalization-job.yaml ./job-api-schema-migration-v007-job-finalization-job.yaml
COPY job-api-schema-migration-v008-partial-task-finalization-configmap.yaml ./job-api-schema-migration-v008-partial-task-finalization-configmap.yaml
COPY job-api-schema-migration-v008-partial-task-finalization-job.yaml ./job-api-schema-migration-v008-partial-task-finalization-job.yaml

# Catch a syntax error in every API module while building. `compile` is used
# instead of `compileall` so no root-owned .pyc output is left in the app tree.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path("app").glob("*.py")]'

# The suite generates an ephemeral RSA key for JWT tests and performs local
# Signature V4 calculations for MinIO POST-policy tests. It makes no Keycloak,
# PostgreSQL, MinIO, Docker, or Kubernetes request. A token-validation or
# upload-contract regression therefore fails the image build before any image
# is pushed to the local registry.
RUN python -m unittest discover --start-directory tests --verbose

# Begin the slim runtime stage from the same immutable Python base. Copying
# site-packages from the tested stage avoids a second package install and keeps
# this layer free of test source, while preserving the exact validated wheels.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Both stages use the identical Python 3.12 base image, so this is the precise
# global package directory that the runtime interpreter imports from. It holds
# FastAPI, Psycopg, PyJWT, Cryptography, and their locked dependencies only.
COPY --from=validation /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# The Docker Official Python image begins as root. A dedicated numeric account
# makes the runtime identity explicit now, before a future Pod securityContext
# and read-only filesystem task layer on additional Kubernetes protections.
RUN groupadd --gid 10001 clouddspapi \
    && useradd --uid 10001 --gid clouddspapi --create-home \
      --shell /usr/sbin/nologin clouddspapi

# Copy only the tested application source; `tests/` deliberately remains in
# the validation stage so debugging aids do not expand the runtime attack surface.
COPY --from=validation --chown=10001:10001 /app/app ./app

# Repeat the source-only syntax check after the runtime copy. This verifies the
# exact non-root-owned application tree that the final container will execute.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path("app").glob("*.py")]'

USER 10001:10001
EXPOSE 8080

# Uvicorn binds every container interface. It does not publish a Mac port by
# itself; only a later Kubernetes Service and Traefik Ingress can do that.
# Access logs are disabled because generic URL logging may disclose future
# query-string data. Purpose-built future application logs can record only
# reviewed operational fields. Kubernetes probe results remain observable.
ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
