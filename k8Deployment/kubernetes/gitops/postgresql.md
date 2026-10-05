# PostgreSQL delivery through Flux

Flux reuses native Helm release `clouddsp-data/clouddsp-postgresql` through
[flux-system/clouddsp-postgresql](clusters/clouddsp-local/postgresql/helmrelease.yaml).
The unchanged [chart](../helm/postgresql/) owns only the single-node StatefulSet
and its normal/headless Services. Base chart version `0.1.0`, PostgreSQL `17.11`,
locked multi-platform image, values, security settings, probes, and Pod template
are preserved. Standard local deployment remains ARM64 CPU k3d.

## Source and delivery order

The Git source includes `k8Deployment/kubernetes/helm/postgresql` before its
HelmRelease is enabled. Revision strategy packages the Git SHA with the base
chart version. Reviewed chart defaults are the only values file; credentials
are excluded from Git and Helm values. Publish to `codex/flux-clouddsp-local`.

Job API depends on PostgreSQL readiness. Upload-intake and both dispatchers
also depend on PostgreSQL, Job API and RabbitMQ. Shared scaling-auth depends on
PostgreSQL, KEDA and RabbitMQ; all three workers depend on scaling-auth. These
readiness gates order delivery; they do not run migrations, establish roles or
verify every database grant. Keycloak remains on its existing native Helm and
bootstrap path until its own adoption.

Normal fresh platform bootstrap still installs the native database and provisions
schemas/grants before the separate opt-in Flux bootstrap. The latter requires an
existing healthy release and bound claim. Flux has no adopt-only install mode:
if its native release disappears, reconciliation can attempt installation.
Do not enable this configuration against missing, partial or unreviewed data.

## Durable state and lifecycle

The StatefulSet keeps its selector, governing Service, ordinal zero, `postgres-data`
claim template, `local-path` class, `5Gi` request, and `ReadWriteOnce` access.
Deletion and scale-down claim retention both remain `Retain`. The generated
`postgres-data-clouddsp-postgresql-0` PVC and its PV are not chart resources.
A metadata-only handoff must preserve their UIDs/specs and the running Pod UID.
It does not restart the database or upgrade PostgreSQL.

Database contents, roles, grants, schema/migration ledgers, runtime Secrets and
bootstrap Jobs keep their existing owners. The handoff does not rerun their
bootstrap, initialize an empty store, rotate credentials, or invoke the historical
raw-manifest adoption's backup/restore gate. That older gate remains available
only on the original native adoption path when Flux ownership is absent.

Removing an active HelmRelease can uninstall the database StatefulSet and
Services, interrupting all clients despite the retained claim. Claim retention
is not backup or high availability. Deleting the claim or the entire k3d cluster
removes local data; the normal fresh deployment does not restore it.

## Delivery identity and security

The [dedicated delivery identity](clusters/clouddsp-local/postgresql/reconciliation-rbac.yaml)
can manage the one named StatefulSet and two named Services in `clouddsp-data`.
Existing-object mutations are named; create is kind-scoped because Kubernetes
cannot restrict create using `resourceNames`. Pods/PVCs are read-only. There is
no direct Pod/Job creation, exec, PVC/PV mutation, app Deployment mutation,
NetworkPolicy, namespace, CRD or cluster RBAC permission.

Helm revision storage needs namespace Secret CRUD, which also grants access to
data-service credentials. StatefulSet writes can create Pods mounting other
namespace Secrets or retained claims. These direct API restrictions therefore
do not isolate this identity from the data services: it is trusted database
delivery administration. Root Flux and trusted Git writers retain administrative
authority. Runtime PostgreSQL still disables API token mounting, runs as UID/GID
999, drops capabilities, and uses its existing private Service/network boundary.

Forced replacement, ownership takeover, failed-upgrade cleanup, automatic
rollback/uninstall remediation, and Helm test hooks are disabled. Existing native
apply behavior is retained with `serverSideApply: auto`; full drift correction
is enabled without a broad spec or storage exception. The baseline has no
operational Pod-template restart annotation needing a drift exception. Repair a
failed delivery through Git while preserving its resources and history.

## Verify, smoke, and reconcile

From the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-postgresql --namespace clouddsp-data
ruby k8Deployment/kubernetes/scripts/releases/postgresql-release.rb verify
ruby k8Deployment/kubernetes/scripts/releases/postgresql-release.rb smoke
ruby k8Deployment/kubernetes/scripts/stages/credentials/postgresql-secret-stage.rb verify
python3 k8Deployment/kubernetes/tests/keda/keda-authentication-smoke.py --expect-idle
```

The [ownership-aware runner](../scripts/releases/postgresql-release.rb) checks
current-generation Flux Ready, the exact defaulted HelmRelease specification,
source selection, native target/storage/revision, complete source/render/stored/
live parity, Ready Pod, immutable image reference and bound claim. The upstream
image is an OCI index; a runtime may report its architecture child digest, so
it is not checked using the helper's single-platform digest-equality option.
Handoff snapshots additionally compare the unchanged running image identity.

The versioned read/write smoke authenticates through Service DNS using the
existing administrator Secret, creates/inserts/reads/drops one isolated table,
and removes its successful Job. This is an administrator connectivity/DDL test;
application-role privilege checks remain separate. It does not restart the
StatefulSet, test disaster recovery, or validate every application workflow.
Omit `--expect-idle` during real work.

Native install/adopt stop as soon as the PostgreSQL HelmRelease exists, including
failed, suspended or deleting states. Lookup errors fail closed. Explicit absence
preserves the original fresh-install and protected raw-adoption checks.
For a delivery problem, inspect conditions, native history, Pod events and PVC
binding, repair/publish the versioned configuration, then request reconciliation:

```sh
flux reconcile helmrelease clouddsp-postgresql --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

Do not uninstall, delete claims, remove finalizers, reapply raw manifests, or
rerun schema/credential bootstrap as a shortcut to a failed handoff. Cluster
pause/resume remains `k3d cluster stop/start clouddsp-local`.
