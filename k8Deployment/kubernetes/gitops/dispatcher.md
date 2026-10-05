# Legacy Demucs dispatcher's handoff to Flux

The explicit root selects `flux-system/clouddsp-dispatcher` while its native
release, revision Secrets, and one Deployment remain in `clouddsp-app`.
This is the Demucs-only publisher. Generic-dispatcher has its own HelmRelease,
image lock, selector, and runtime command. Both publishers share the reviewed
database/broker identities and lease-backed outbox boundary through bootstrap.

## Ownership and source

The [HelmRelease](clusters/clouddsp-local/dispatcher/helmrelease.yaml) fixes
the existing release name and target/storage namespace. It uses Git revision
packaging and drift correction, with PostgreSQL, Job API and RabbitMQ readiness
dependencies. Job API verification remains an ordinary fresh prerequisite.
Those delivery dependencies do not provision
database schema or broker identities. The [chart](../helm/dispatcher/) retains
base version `0.1.0` and the locked `images.dispatcher-demucs-only` image,
Pod template, selector, Secret references, resources, and security settings.
There is no Service, Ingress, PVC, or chart-owned test Job.

The [reconciliation identity](clusters/clouddsp-local/dispatcher/reconciliation-rbac.yaml)
can change Deployments and namespace Helm storage Secrets, and read Pods and
ReplicaSets. It cannot create Jobs, Services, Ingresses, PVCs, namespaces,
CRDs, or cluster RBAC. Helm storage access covers all namespace Secrets,
including runtime credentials; it is not limited to this release's history.
The root Flux reconciler and trusted Git writers retain administrative scope.

Prepare `helm/dispatcher/` in sparse checkout first and verify the served
archive contains its `Chart.yaml` before enabling ownership in the root.
Flux upgrades the existing native release, adding only its two origin labels
to resource metadata. The Pod template stays unchanged. History is retained
subject to `maxHistory: 10`. Forced replacement, automatic rollback/uninstall,
and failed-upgrade cleanup are disabled. A failed upgrade can leave partial
state; inspect conditions/history and repair Git. Removing the active
HelmRelease normally uninstalls its Deployment.

## Normal verification

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-dispatcher --namespace clouddsp-app
ruby k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb verify
```

The [runner](../scripts/releases/dispatcher-release.rb) and
[adapter](../scripts/gitops/dispatcher-flux-ownership.rb) use
[shared publisher verification](../scripts/gitops/dispatcher-flux-verification.rb).
They require exact Flux identity/storage, current-generation Ready, precise
native Git-packaged chart revision, full source/render/stored/live parity,
one Ready Pod, and the locked running digest. Direct install/adopt is blocked
whenever the HelmRelease exists, including failed, suspended, and deleting
states. API lookup errors fail closed. Ordinary verification accepts only
normal values and no inline or external values overrides.

## Source smoke pause and restoration

The [source-to-outbox smoke](../tests/source-intake-smoke/README.md) observes one
pending Demucs event and verifies duplicate notification handling. Both
publishers must be completely stopped so the tiny synthetic WAV stays out of
inference. The [shared values editor](../scripts/gitops/dispatchers-smoke-values.rb)
validates both HelmReleases before staging each reviewed pause line. Publish
both changes in one commit; Flux performs the upgrades with drift correction
active. Git changes are reconciled independently, so wait for both gates.

```sh
ruby k8Deployment/kubernetes/scripts/gitops/dispatchers-smoke-values.rb pause
# Review, commit, and push both HelmRelease files as described in the runbook.
ruby k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb verify-smoke-pause
ruby k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb verify-smoke-pause
```

Each pause gate is read-only and requires exact pause valuesFiles,
current-generation Ready, stored/live parity at zero replicas, and no Pods,
including terminating publishers. A terminating process can still publish.
Ordinary verification deliberately rejects the paused configuration. After the
smoke confirms object, database-row, and temporary Keycloak identity cleanup,
delete its exact Job, stage `restore`, publish both manifests, and verify both
normal releases. Inspect surviving synthetic data before restoring a failed
or interrupted run; the editor never blindly resumes on shell failure.

The dispatcher has no process readiness probe or HTTP health endpoint.
Deployment checks alone do not establish publication; the separate
[normal-path dispatcher test](../tests/dispatcher-smoke/client/README.md)
exercises real upload-to-AMQP delivery with its own isolation/credentials.
Smokes are disposable administrator-driven Jobs outside these Helm charts.
Host cluster/registry lifecycle, credentials, and durable service bootstrap
remain separate from optional Flux reconciliation.
