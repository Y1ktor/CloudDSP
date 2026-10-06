# Shared KEDA authentication handoff to Flux

The root selects `flux-system/clouddsp-scaling-auth`. Its existing native
release and Helm revision Secrets stay in `clouddsp-app`. The
[chart](../helm/scaling-auth/) owns exactly two namespaced
`TriggerAuthentication` objects. RabbitMQ observation is shared by Demucs,
Basic Pitch, and ADTOF; PostgreSQL task-count observation is shared by Demucs
and Basic Pitch. It contains fixed Secret names/keys and no Secret values.

## Prerequisites and ownership

Prepare `helm/scaling-auth/` in the sparse source and verify the served archive
contains its `Chart.yaml` before selecting the HelmRelease. The opt-in
[bootstrap](../scripts/gitops/bootstrap-flux.sh) verifies the pinned KEDA
release, controllers, CRDs, metrics API, and existing scaling-auth release.
Observer identities and runtime Secrets must already be provisioned through
the ordinary fresh platform bootstrap. This is adoption of an existing
working platform, rather than an independent cluster creation command.

The [HelmRelease](clusters/clouddsp-local/scaling-auth/helmrelease.yaml)
fixes release name and target/storage namespace, uses Git revision packaging
and drift correction, and retains up to ten Helm history entries. It refuses
unrelated resource ownership takeover and disables forced replacement,
automatic rollback/uninstall, and failed-upgrade cleanup. Inspect failed
conditions/history and repair Git before continuing. Removing this active
HelmRelease normally uninstalls its authentications, disrupting dependent
scalers; treat removal as a deliberate lifecycle operation.

KEDA remains in its separately delivered `keda` native Helm release, now owned
by its Flux HelmRelease. Shared authentication depends on PostgreSQL, KEDA and
RabbitMQ readiness. Worker charts still own
their Deployments and ScaledObjects; KEDA owns their generated HPAs and scale
decisions. Authentication adoption changes only release/origin metadata;
credential references and the two authentication specs remain identical.

The [reconciliation Role](clusters/clouddsp-local/scaling-auth/reconciliation-rbac.yaml)
can manage namespaced TriggerAuthentications and namespace Secrets required
for Helm revision storage. Secret permission covers all `clouddsp-app`
Secrets, including runtime credentials. It grants no worker, ScaledObject,
HPA, Pod, Job, PVC, CRD, namespace, or cluster RBAC permissions. The root Flux
controller and trusted Git writers retain administrative scope.

## Verify and maintain

From the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-scaling-auth --namespace clouddsp-app
ruby k8Deployment/kubernetes/scripts/releases/keda-release-stage.rb verify
ruby k8Deployment/kubernetes/scripts/releases/scaling-auth-release.rb verify
python3 k8Deployment/kubernetes/tests/keda/keda-authentication-smoke.py --expect-idle
```

The [runner](../scripts/releases/scaling-auth-release.rb) and
[adapter](../scripts/gitops/scaling-auth-flux-ownership.rb) require the exact
Flux identity/storage, current-generation Ready, reviewed source/empty values,
native Git-packaged chart revision, stored/live authentication parity, KEDA
finalizers, and Secret names. Full `verify` also checks all three Ready worker
scalers, their authentication references, HPA ownership, and Deployment
targets. `verify-prerequisites` checks the shared release before worker
installation, when the workers may not exist yet. Secret contents are not
read by either mode.

Direct `install`/`adopt` is blocked whenever this HelmRelease exists, including
failed, suspended, and deleting states. API errors fail closed. Maintenance
`reconcile` verifies current Flux reconciliation without performing a direct
Helm upgrade. On a pre-Flux cluster it preserves guarded native reconciliation.
The [Demucs maintenance helper](../scripts/maintenance/reconcile-demucs-scaling.sh)
uses that mode before upgrading the still native Demucs worker release.

The read-only metric smoke requests each worker's three RabbitMQ and two
PostgreSQL measurements through KEDA's external metrics API. It requires fresh,
uncached responses and no paused/fallback scaler. `--expect-idle` additionally
requires five zero metrics and zero worker replicas; omit it during normal
processing. It creates no Jobs, messages, database rows, or credentials and
prints only metric names/values/timestamps. The mandatory ScaledObject selector
chooses the backend source; native KEDA responses may have null metric labels.
See the pinned [KEDA metric provider](https://github.com/kedacore/keda/blob/v2.20.2/pkg/provider/provider.go).
This proves metric access with existing credentials, while worker burst and
end-to-end processing tests remain separate checks for worker adoption.

PostgreSQL and RabbitMQ readiness join KEDA as Flux dependencies. Workers inherit
all three readiness gates through this release. Identity/topology provisioning stays an
ordinary bootstrap prerequisite; see the [RabbitMQ](rabbitmq.md) and [PostgreSQL](postgresql.md) guides.
