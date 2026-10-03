# Image builders and active test tools

A fresh [deployment](../../scripts/README.md) pulls reviewed images and needs
no host-side frontend or worker rebuild. This reference is for maintainers
building new images or running explicit functional tests. Root `verify`
checks deployed configuration and does not run the active tests below.

## Image build helpers

All builders below are under [`scripts/`](../../scripts/). They check the
exact local registry, build reviewed ARM64 source/dependencies, push only an
OCI image, and report an immutable digest and local image size. They do not
install/update Kubernetes workloads. Their validation stages run the relevant
module or inference checks; base, dependency, and model inputs are pinned in
Dockerfiles/locks.

| Builder | Source and validation boundary |
| --- | --- |
| [build-frontend-image.sh](../../scripts/build-frontend-image.sh) | Shared root `frontend/` named context; local Vite profile; NGINX runtime/CSP validation. |
| [build-job-api-image.sh](../../scripts/build-job-api-image.sh) | API authentication, owner/job, upload/storage, and PostgreSQL helper tests. |
| [build-upload-intake-image.sh](../../scripts/build-upload-intake-image.sh) | Strict event parser, atomic source/outbox transition, MinIO evidence, manual ACK, and reconnect/shutdown tests. |
| [build-dispatcher-image.sh](../../scripts/build-dispatcher-image.sh) | Demucs-only and generic lease/routing, publisher-confirmation, transaction, and runtime tests. |
| [build-demucs-image.sh](../../scripts/build-demucs-image.sh) | Full worker/model validation and real fixed two-stem CPU inference before publication. |
| [build-basic-pitch-image.sh](../../scripts/build-basic-pitch-image.sh) | Worker tests, approved Basic Pitch command, and bundled TensorFlow Lite model. |
| [build-adtof-image.sh](../../scripts/build-adtof-image.sh) | Worker/scratch-path/CPU-child tests and bundled ADTOF package/weights. |
| [build-minio-source-intake-smoke-client-image.sh](../../scripts/build-minio-source-intake-smoke-client-image.sh) | Disposable MinIO-to-RabbitMQ client. |
| [build-dispatcher-smoke-client-image.sh](../../scripts/build-dispatcher-smoke-client-image.sh) | Normal-upload smoke orchestration/verifier. |
| [build-generic-dispatcher-basic-pitch-smoke-client-image.sh](../../scripts/build-generic-dispatcher-basic-pitch-smoke-client-image.sh) | Restricted Basic Pitch routing verifier. |
| [build-basic-pitch-worker-smoke-client-image.sh](../../scripts/build-basic-pitch-worker-smoke-client-image.sh) | End-to-end Basic Pitch client with fixed database functions/object scope. |
| [build-adtof-worker-smoke-client-image.sh](../../scripts/build-adtof-worker-smoke-client-image.sh) | End-to-end ADTOF client with fixed database functions/object scope. |
| [build-demucs-worker-smoke-client-image.sh](../../scripts/build-demucs-worker-smoke-client-image.sh) | Demucs stage/downstream completion client; verifies pushed registry digest. |

The dispatcher image keeps the Demucs-only runtime as its default entrypoint;
the generic Deployment selects `app.dispatcher_generic_runtime` explicitly.
Basic Pitch and ADTOF use their separate reviewed Python 3.11 inference bases.
Demucs's ARM64 CPU launcher disables Torch MKLDNN before importing Demucs;
its real inference check protects against the illegal-instruction path that
motivated that setting. The frontend builder reads only public browser values
from ignored `k8Deployment/.local/frontend.env.production` and builds
`frontend/dist/local/`; see the [delivery contract](../../services/frontend/README.md).

After a deliberate rebuild, review and update `images.lock.yaml`, the affected
Helm image value, and retained workload/test source reference together. Publish
its locked source through [image-registry-stage.rb](../../scripts/image-registry-stage.rb)
when the public fresh-install path must consume it. A printed build tag alone
is not a deployment input; workloads use the reviewed immutable digest.

## Service and processing smokes

These tests can create Jobs, temporary users, database records, messages,
and/or objects. Read each contract before running it and use its scoped
cleanup; none is implicitly part of the root read-only verification command.

| Test/runbook | Functional evidence and side effects |
| --- | --- |
| [Mailpit SMTP capture Job](../../tests/mailpit-smoke/mailpit-smtp-capture-smoke-job.yaml) | Sends/reads disposable captured mail. Available through `mailpit-release.rb smoke`. |
| [PostgreSQL read/write Job](../../tests/postgresql-smoke/postgresql-read-write-smoke-job.yaml) | Creates/reads/deletes a disposable table through the Service; available through `postgresql-release.rb smoke`. |
| [RabbitMQ AMQP Job](../../tests/rabbitmq-smoke/rabbitmq-amqp-smoke-job.yaml) | Disposable broker delivery exercise; available through `rabbitmq-release.rb smoke`. |
| [MinIO S3 API Job](../../tests/minio-smoke/minio-s3-api-smoke-job.yaml) | Scoped create/read/delete exercise; available through `minio-release.rb smoke`. |
| [Keycloak smoke manifests](../../tests/keycloak-smoke/) | Discovery/PKCE authorization, email verification via Mailpit, and authenticated Job API access; identity-bearing tests need their own cleanup. |
| [Source-to-outbox runbook](../../tests/source-intake-smoke/README.md) | Authenticated upload, source notification, atomic outbox, and duplicate notification. Temporarily pauses/restores both dispatchers for its pending-row assertion. |
| [Dispatcher client/runbook](../../tests/dispatcher-smoke/client/README.md) | Controlled durable publication and normal-upload workflow; temporary identity/data scope. |
| [Generic dispatcher test](../../tests/generic-dispatcher-smoke/) | Restricted Basic Pitch outbox route and delivery proof. |
| [Basic Pitch worker runbook](../../tests/basic-pitch-worker-smoke/README.md) | Worker execution, private MIDI bytes/provenance, scoped data cleanup. |
| [ADTOF worker runbook](../../tests/adtof-worker-smoke/README.md) | Drum execution, private MIDI/tempo evidence, scoped data cleanup. |
| [Demucs worker runbook](../../tests/demucs-worker-smoke/README.md) | Real stem artifacts, downstream tasks, parent completion, and scoped cleanup; available through `demucs-release.rb smoke`. |
| [Basic Pitch burst](../../tests/basic-pitch-keda-burst-smoke/README.md) | Bounded scaling/concurrency test with controlled work. |
| [ADTOF exhausted-lease recovery](../../tests/adtof-exhausted-lease-recovery-smoke/README.md) | Controlled durable recovery/finalization evidence. |
| [Six-stem load runbook](../../tests/six-stem-load/README.md) | Bounded complete workload, observer capabilities, terminal results, and cleanup. |

Failure may preserve fixed test coordinates for diagnosis. Follow the
runbook's explicit cleanup before rerunning rather than deleting an entire
release or broad namespace. Historical test successes/failures belong to
[trial records](../trials/) and the [archived scripts reference](../history/scripts-reference-before-reorganization.md),
not to a claim that a current code revision has passed every active smoke.

## Focused MinIO authorization smokes

```bash
ruby ./k8Deployment/kubernetes/tests/minio-smoke/demucs-iam-smoke.rb
ruby ./k8Deployment/kubernetes/tests/minio-smoke/basic-pitch-iam-smoke.rb
ruby ./k8Deployment/kubernetes/tests/minio-smoke/adtof-iam-smoke.rb
```

These use runtime keys to prove allowed operations and denied other-stem,
wrong-output, deletion, or listing capabilities. They create unique scoped
probe objects and remove them with the ignored local root credential. They
avoid `uploads/` notification keys and do not enqueue processing. Job API's
[restricted-access Job](../../tests/minio-smoke/minio-job-api-restricted-access-smoke-job.yaml)
has its separate contract. Configuration verification alone does not replace
these active authorization tests.

## Foundation and image/process tests

```bash
./k8Deployment/kubernetes/scripts/verify-registry.sh
./k8Deployment/kubernetes/scripts/verify-http-routing.sh
./k8Deployment/kubernetes/scripts/verify-job-api-local-image.sh
```

`verify-registry.sh` pushes BusyBox `1.37.0`, runs the reviewed registry Job
with `imagePullPolicy: Always`, and reports pull events/logs. It removes only a
prior same-name test Job before rerunning. The completed Job/Pod remain until
TTL, and the test image remains in the dedicated registry. The app namespace
must already exist; full bootstrap creates it.

`verify-http-routing.sh` pushes a separate BusyBox server image and applies the
[isolated routing workload](../../tests/routing-smoke/http-routing-smoke.yaml)
to test `http://routing-smoke.localhost:8080/`. Those test resources remain for
inspection. Remove only that test afterwards:

```bash
kubectl --context k3d-clouddsp-local delete \
  --filename k8Deployment/kubernetes/tests/routing-smoke/http-routing-smoke.yaml
```

`verify-job-api-local-image.sh` starts its reviewed immutable image as a
short-lived Docker container with no network or database credentials. It
requires `/healthz` HTTP 200 and `/readyz` HTTP 503 with
`database_configuration`, then removes the container. This proves the image
process boundary, not in-cluster database connectivity. Its image/version
expectation is hardcoded in the helper and must match the intended revision.

[Backup/recovery rehearsals](helm-releases-and-scaling.md) are active stateful
maintenance tests, separate from these application smokes and fresh bootstrap.
The [one-job upload recovery helper](../../scripts/reconcile-one-upload-intake-job.sh)
accepts a canonical job UUID, uses the already-running intake Pod's restricted
identities, and repeats the normal storage-evidence/atomic-outbox transition.
It is an explicit operator repair, not part of read-only verification.
