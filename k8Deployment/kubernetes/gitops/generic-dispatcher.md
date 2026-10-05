# Generic dispatcher's handoff to Flux

The explicit root selects `clouddsp-generic-dispatcher` in `flux-system`.
The existing native release, history Secrets, and single outbound Deployment
remain in `clouddsp-app`. Its base chart is `0.1.0`; its image, explicit
`app.dispatcher_generic_runtime` command, selector, Pod template, and Secret
references retain the reviewed source configuration. The legacy Demucs-only
dispatcher is a separate native Helm release with its own Flux handoff.

## Delivery boundary

The [HelmRelease](clusters/clouddsp-local/generic-dispatcher/helmrelease.yaml)
fixes release name, target/storage namespace, and a Job API readiness dependency
that mirrors the existing fresh-install prerequisite. PostgreSQL schema and
dispatcher database/broker identities must already exist through bootstrap.
The readiness dependency does not provision or check those service contents.
There is no inbound listener, Service, Ingress, storage claim, or chart test Job.

The [reconciliation identity](clusters/clouddsp-local/generic-dispatcher/reconciliation-rbac.yaml)
can change Deployments and namespace Helm storage Secrets, and read Pods and
ReplicaSets. It cannot create Jobs, Services, Ingresses, PVCs, namespaces, CRDs,
or cluster RBAC. Helm's Secret grant covers the namespace, including runtime
Secrets; it is not restricted to this release's history. The root reconciler
and trusted Git writers retain administrative scope.

Prepare the chart sparse source in a separate commit and confirm its served
archive contains `helm/generic-dispatcher/Chart.yaml` before enabling the
HelmRelease. Flux packages each Git revision with a SHA/generation suffix.
It adds only its two origin labels to resource metadata. Native history is
preserved subject to `maxHistory: 10`; the handoff needs no delete/reinstall.
Forced replacement, automatic rollback/uninstall, and failed-upgrade cleanup
are disabled. A failed upgrade can still leave partial state: inspect its
conditions/history and repair Git. Removing the active HelmRelease normally
uninstalls its Deployment.

## Checks and smoke maintenance

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-generic-dispatcher --namespace clouddsp-app
ruby k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb verify
```

The [runner](../scripts/releases/generic-dispatcher-release.rb) checks current
Flux generation/readiness, exact native chart revision, source/render/stored/live
specs, Helm ownership, one Ready Pod, and locked running digest. It blocks direct
install/adopt whenever the HelmRelease exists, including suspended, deleting,
and failed states. API lookup failures fail closed. Normal verification accepts
only the ordinary values file and no inline/external overrides.

Kubernetes readiness alone does not prove publishing: there is no dispatcher
HTTP health endpoint or process readiness probe. The [routing smoke](../tests/generic-dispatcher-smoke/README.md)
checks one controlled Basic Pitch outbox event, persistent AMQP envelope,
exact acknowledgement, and synthetic-row cleanup. It temporarily isolates
the native Basic Pitch scaler so a real worker cannot consume the test delivery.

The [source-to-outbox smoke](../tests/source-intake-smoke/README.md) pauses both
publishers while observing one pending outbox row. Generic-dispatcher's pause
is a committed HelmRelease `valuesFiles` change, selecting the reviewed
`values.smoke-pause.yaml` after normal values. The small
[shared values editor](../scripts/gitops/dispatchers-smoke-values.rb) validates
both HelmReleases and stages only their pause lines in the watched checkout; commit/push and reconciliation
remain explicit. It never changes the cluster or publishes Git automatically. The generic-only
entrypoint remains available for targeted routing maintenance.
`verify-smoke-pause` requires current-generation Ready, the exact pause
configuration, full stored/live parity, and **no Pods, including terminating
publishers**. Normal `verify` continues to require one replica and running
digest. Restore the Git values only after the test confirms cleanup; an
interrupted synthetic row must be inspected before publishing can resume.

Both smokes are disposable administrator-driven Jobs outside Flux/Helm chart
ownership. No credentials or service contents are placed in Git. These
handoffs leave host cluster/registry lifecycle and ordinary fresh bootstrap
separate from the optional Flux bootstrap.

References: [Flux values files](https://fluxcd.io/flux/components/source/helmcharts/#values-files),
[HelmRelease](https://fluxcd.io/flux/components/helm/helmreleases/), and
[KEDA pause](https://keda.sh/docs/2.20/concepts/scaling-deployments/#pausing-autoscaling).
