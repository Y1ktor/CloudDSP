# MinIO delivery through Flux

Flux selects [flux-system/clouddsp-minio](clusters/clouddsp-local/minio/helmrelease.yaml)
and reuses native release `clouddsp-data/clouddsp-minio` with its existing history.
The unchanged [chart](../helm/minio/) owns four resources: one StatefulSet, normal
and headless Services, and the browser S3 Ingress. Base chart `0.1.0`, application
`RELEASE.2025-10-15T17-29-55Z`, the locked ARM64 image, chart defaults, probes,
security context, Secret references, selector and Pod template are preserved.

## Source and delivery order

Prepare the sparse Git source and delivery RBAC before enabling the HelmRelease;
check that Flux's actual source archive contains all seven reviewed chart files.
Publish changes to `codex/flux-clouddsp-local`. Revision strategy packages the
Git SHA and values generation with the base chart version. The only values file
is `helm/minio/values.yaml`; there are no inline values or credential sources.

MinIO waits for RabbitMQ because its existing AMQP notification target uses the
broker. Job API, upload-intake and all three workers wait directly for MinIO.
Their existing PostgreSQL, broker, Job API and shared scaling-auth gates remain.
These gates order delivery; they do not create buckets, provision users, attach
notifications or prove application processing. Dispatchers inherit the storage
gate through Job API. Keycloak remains on its native deployment/bootstrap path.

Ordinary fresh deployment installs the native release and provisions storage
configuration before the separate opt-in Flux bootstrap. The latter verifies
an existing healthy release, bound claim, buckets, policies and notifications.
Flux has no adopt-only install mode: if native Helm storage disappears, it can
attempt installation. Prepare the existing platform before opting in.

## Retained state and lifecycle

The single-server StatefulSet retains its ordinal, governing Service,
`minio-data` claim template, `local-path` class, `10Gi` request, `ReadWriteOnce`
access and `Retain` policies for deletion and scaling. The generated
`minio-data-clouddsp-minio-0` claim and PV are outside the chart. A metadata-only
handoff preserves their UIDs/specs and the current Pod UID, container and restart
count. The browser origin stays `http://minio.localhost:8080`; no rewrite alters
presigned signatures and the administrative console remains unexposed.

Buckets, objects, IAM users/policies, the source-upload notification, root and
restricted runtime Secrets, policy ConfigMaps and bootstrap Jobs keep their
existing owners. The handoff does not rerun bootstrap, rotate credentials,
initialize a new store or invoke the historical raw-manifest adoption's stopped
volume snapshot/restore test. That historical gate remains on the original
native adoption path when Flux ownership is explicitly absent.

Removing an active HelmRelease can uninstall its workload, Services and route,
interrupting storage access. Claim retention is not a backup. Deleting the
claim or k3d cluster removes local data; fresh deployment does not restore it.

## Delivery permissions and runtime security

The [dedicated identity](clusters/clouddsp-local/minio/reconciliation-rbac.yaml)
can mutate the named StatefulSet, two Services and S3 Ingress in `clouddsp-data`.
Create rights are kind-wide because Kubernetes cannot restrict create with
`resourceNames`. Pods/PVCs are read-only; the role grants no direct Pod/Job
creation, exec, PVC/PV mutation, NetworkPolicy, application Deployment, namespace,
CRD or cluster RBAC authority.

Helm history requires namespace-wide Secret CRUD, including data-service
credentials. StatefulSet mutation can create Pods mounting other namespace
Secrets or claims. These direct API limits do not isolate MinIO delivery from
other data services: this is a trusted delivery identity. Root Flux and writers
to its Git branch retain administrative authority.

Runtime MinIO remains non-root UID/GID 65532 with no API token, privilege
escalation or Linux capabilities. Uploads/artifacts remain private. The separate
sample bucket permits anonymous `GetObject` only, with no list/write grant.
Six versioned IAM policies map to five restricted application users. Upload
notifications retain the `uploads/` creation filter and existing AMQP target,
publisher confirms and event backlog on the PVC. These settings support durable,
at-least-once delivery; Flux itself does not provide processing idempotency.

Force, ownership takeover, failed-upgrade cleanup, automatic rollback/uninstall
remediation and Helm test hooks are disabled. Native apply behavior is retained
with `serverSideApply: auto`; full drift detection has no storage/spec exceptions.

## Verify, smoke and reconcile

From the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-minio --namespace clouddsp-data
ruby k8Deployment/kubernetes/scripts/releases/minio-release.rb verify
ruby k8Deployment/kubernetes/scripts/stages/minio/minio-state-verify.rb verify
ruby k8Deployment/kubernetes/scripts/releases/minio-release.rb smoke
ruby k8Deployment/kubernetes/scripts/stages/minio/minio-notification-stage.rb verify
ruby k8Deployment/kubernetes/scripts/stages/credentials/minio-root-secret-stage.rb verify
ruby k8Deployment/kubernetes/scripts/stages/credentials/minio-amqp-secret-stage.rb verify
```

The [release runner](../scripts/releases/minio-release.rb) requires current
Flux generation Ready, exact reviewed HelmRelease settings, source/value
selection, matching native target/storage/revision, complete source/render/stored/
live parity, Ready Pod, exact running single-platform image digest, bound claim
and HTTP health through the existing S3 Ingress. Its YAML decoder preserves
Flux's unquoted `:9001` as the same exact Kubernetes string, avoiding Ruby's
Symbol interpretation without skipping a manifest field. The state verifier independently
checks the two standard buckets, private/public boundaries, all six IAM policies,
five users and exact notification rule without printing credentials.

The separate S3 smoke uses the existing root Secret through Service DNS. It
creates only reserved bucket `clouddsp-s3-api-smoke`, writes/downloads/checksums
`round-trip.txt`, then deletes the object and bucket. Require that reserved bucket
and named Job to be absent before running it; the state verifier's exact bucket
set establishes bucket absence. Successful Jobs are removed; failures remain
inspectable. This tests S3 connectivity and round-trip behavior; it does not
simulate broker failure, replay processing, restart MinIO or test data restore.

Native install/adopt stop whenever the HelmRelease exists, even when suspended,
failed or deleting; API errors fail closed. Repair/publish Git configuration,
then reconcile the delivery:

```sh
flux reconcile helmrelease clouddsp-minio --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

Inspect Flux conditions, native history, Pod events and claim binding when a
check fails. Preserve resources/history while repairing the reviewed settings.
Pause/resume remains `k3d cluster stop/start clouddsp-local`.
