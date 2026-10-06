# KEDA platform handoff to Flux

Flux delivers the existing native `keda/keda` Helm release through
`flux-system/keda`. The official upstream chart remains pinned to **2.20.2**.
Controller images, resources, namespace watch scope, Pod templates, and runtime
RBAC are unchanged by this handoff. The standard profile remains ARM64 CPU.

## Source and values

The [HelmRepository](clusters/clouddsp-local/keda/helmrepository.yaml) fetches
the official `https://kedacore.github.io/charts` index. The
[HelmRelease](clusters/clouddsp-local/keda/helmrelease.yaml) selects exactly
`keda` version `2.20.2` with `ChartVersion` strategy. Unlike application charts
from Git, its native chart version has no Git SHA suffix. There is no floating
version range or image automation.

The release keeps both target and Helm storage in `keda`, preserving history.
Its public inline values mirror [helm/keda/values.yaml](../helm/keda/values.yaml)
with one exact transformation: omit `additionalLabels.app.kubernetes.io/part-of`.
The upstream chart already emits this label; the historical local override
produces duplicate YAML keys that Kustomize rejects. Explicit postrenderer
patches restore its existing effective `clouddsp` value on 24 non-CRD objects
and the three Pod templates. The six CRDs retain their original upstream
label. Selectors and all effective specs remain unchanged. The native input
file remains intact for pre-Flux bootstrap.

A HelmRepository chart cannot read files from the separate Git source. Update
both mappings together, preserving this transformation; the release verifier
requires exact equality after that single omitted input. Keep the
[release lock](../helm/keda/release.lock.yaml) and executable
version gates consistent when deliberately reviewing a future version change.
The adoption does not copy an upstream chart into the repository.

Prepare and verify the official index and reconciliation identity before
enabling the HelmRelease. Its chart artifact must match the reviewed upstream
archive. Application chart sparse paths remain unchanged. Git changes to this
platform definition must be published to `codex/flux-clouddsp-local`.

## Dependencies and scaling

Shared scaling-auth now depends on the PostgreSQL, KEDA and RabbitMQ HelmReleases. All three worker
HelmReleases depend on scaling-auth: **KEDA/RabbitMQ → shared authentication → workers**.
The ordinary fresh platform bootstrap still installs KEDA, creates observer
identities, and installs the application releases before opt-in Flux adoption.
Flux readiness dependencies do not create credential values or bootstrap data.

The upstream chart owns 30 platform objects: controller Deployments, Services,
service accounts, namespaced/cluster RBAC, six CRDs, the aggregated metrics API,
and admission webhook configuration. It does not own the application
ScaledObjects, TriggerAuthentications, HPAs, queues, or worker Deployments.
KEDA's running operator and Kubernetes HPA continue controlling worker replicas.

## TLS and CRD lifecycle

KEDA generates and rotates its cluster-internal TLS Secret. That Secret is not
rendered by this chart. The operator injects CA bundles into the metrics API
and six admission webhook entries. Flux ignores only those seven exact CA
fields on the two named objects. Controller templates, resources, API routing,
webhook rules, and all other chart fields remain subject to drift correction.
The corresponding [Flux ignore rules](https://fluxcd.io/flux/components/helm/helmreleases/#ignore-rules)
support narrow runtime-field exceptions.

KEDA 2.20.2 renders its CRDs under `templates/crds/`. Flux's `install.crds` and
`upgrade.crds` settings govern Helm's separate `crds/` lifecycle; they do not
skip these normal templates. The postrenderer marks exactly the six KEDA CRDs
with `helm.sh/resource-policy: keep`, and delivery RBAC grants no CRD deletion.
Their schemas and generations remain unchanged during adoption. No CRD,
custom resource, finalizer, or HPA is deleted as a handoff step.

Removing the active HelmRelease still removes its controllers, API, webhook,
and RBAC and interrupts scaling. Retained CRDs/custom resources are not a
working operator installation. Treat release removal or version changes as
deliberate platform work. Normal full cleanup deletes the k3d cluster,
including retained CRDs. K3d stop/start remains the cluster pause/resume path.

## Delivery permissions

The [reconciliation identity](clusters/clouddsp-local/keda/reconciliation-rbac.yaml)
has named access to the chart's cluster objects, controller mutations in
`keda`, one chart-owned binding in `clouddsp-app`, and the authentication-reader
binding in `kube-system`. Read-only Pods/ReplicaSets support chart readiness.
Helm revision storage requires namespace-wide Secret CRUD in `keda`, including
access to the runtime TLS Secret; no app/data Secret permission is granted.

Writing the powerful KEDA runtime roles requires explicit `escalate` and `bind`
rights on reviewed role names. Modifying those roles, bindings, or operator
Pods can confer their runtime authority, so this is a trusted platform identity,
not an application isolation boundary. It has no arbitrary cluster RBAC names,
namespace creation, direct application Deployment/HPA/ScaledObject writes,
Pod/Job creation, PVC permissions, or CRD deletion. Named `patch` rights can
recreate objects through server-side apply even without `create`; object names
restrict that path too. Root Flux reconciliation and trusted Git writers
retain administrative authority.

## Verify and recovery

From the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history keda --namespace keda
ruby k8Deployment/kubernetes/scripts/releases/keda-release-stage.rb verify
ruby k8Deployment/kubernetes/scripts/releases/scaling-auth-release.rb verify
python3 k8Deployment/kubernetes/tests/keda/keda-authentication-smoke.py --expect-idle
```

The KEDA verifier requires the exact reviewed active HelmRelease spec,
current-generation Ready for the release/repository/chart, official repository,
exact upstream artifact version, and unchanged native chart/values/controllers/
CRDs/metrics registration. A Ready Flux record does not bypass native gates.
The five-metric smoke queries fresh authenticated RabbitMQ/PostgreSQL metrics;
omit `--expect-idle` while real work is queued or running.

Both the Ruby fresh-bootstrap entry point and historical
[shell installer](../scripts/releases/install-keda.sh) refuse native writes
whenever the HelmRelease exists, including failed, suspended, and deleting
states. Lookup failures fail closed. Explicit absence preserves pre-Flux
bootstrap. Inspect failed delivery and native history, repair the versioned
configuration, publish it, and request reconciliation:

```sh
flux reconcile helmrelease keda --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

Forced replacement, takeover, automatic rollback/uninstall, failed-upgrade
cleanup, and Helm test hooks are disabled. A failed operation can leave partial
state; investigate its conditions rather than deleting resources or finalizers.
