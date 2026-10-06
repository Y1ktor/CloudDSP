# RabbitMQ delivery through Flux

Flux reuses native Helm release `clouddsp-data/clouddsp-rabbitmq` and its
existing history through [flux-system/clouddsp-rabbitmq](clusters/clouddsp-local/rabbitmq/helmrelease.yaml).
The [chart](../helm/rabbitmq/) owns exactly the single-node StatefulSet, three
Services and ingress NetworkPolicy. Its version is `0.1.1`; the management image
remains the reviewed immutable RabbitMQ `4.3.5` digest. ARM64 CPU k3d remains
the standard local profile.

## Source and order

The source artifact includes `k8Deployment/kubernetes/helm/rabbitmq` before the
HelmRelease is enabled. Revision strategy packages the Git revision with the
base chart version. The only values file is the reviewed chart default; no
inline credentials, external values sources, floating images or image automation
are configured. Publish changes to `codex/flux-clouddsp-local` for reconciliation.

Upload-intake and both dispatchers now depend on PostgreSQL, RabbitMQ and
Job API readiness.
Shared scaling-auth depends on PostgreSQL, KEDA and RabbitMQ; the three workers depend on
MinIO and scaling-auth. These gates order delivery. They do not initialize users, grants,
queues or database state. The ordinary fresh bootstrap still prepares those
prerequisites before the separate opt-in Flux bootstrap.

## Storage and external state

The broker keeps the same StatefulSet name, headless Service, selector, ordinal,
node name, storage class and `5Gi` claim template. Both deletion and scale-down
claim retention are `Retain`. The bound claim
`rabbitmq-data-clouddsp-rabbitmq-0` and its PV are controller-created durable
storage, not rendered chart objects. Flux adoption must not replace them.

Vhosts, queue/exchange/binding topology, credentials and messages stay broker
state. Runtime Secrets, bootstrap ConfigMaps/Jobs, KEDA resources and smoke
Jobs have separate owners. The handoff does not run the historical raw-manifest
adoption's backup/restore gate, initialize an empty store, purge a queue or
rotate an account. Removing the HelmRelease can uninstall its broker/network
objects and interrupt all queue processing even though the claim is retained.
Deleting the claim or the entire k3d cluster deletes local data; retention is
not a backup or a working broker.

## Health probes

The prior repeated Erlang exec probes exceeded their five-second kubelet limit
and caused broker restarts. Chart `0.1.1` uses an AMQP TCP startup probe (roughly
three-minute initialization budget), TCP readiness, and no liveness probe,
following [RabbitMQ's health-check guidance](https://www.rabbitmq.com/docs/monitoring#health-checks-as-readiness-probes).
This removes per-probe Erlang distribution joins and avoids restarting the
broker because a diagnostic client is slow or resources are under pressure.
Kubernetes still restarts an exited container. A live but stuck process needs
operator diagnosis; TCP Ready alone does not prove authentication, alarm-free
state, usable queues or successful publish/consume.

The release verifier separately executes `check_running` and
`check_local_alarms` with a bounded diagnostic timeout. The AMQP smoke, topology
and identity verification, fresh KEDA metrics, and worker processing smoke
exercise the other boundaries. The probe change deliberately replaces the one
broker Pod once; this single-node profile has brief broker unavailability
during rollout. It does not provide high availability or a multi-node design.

## Delivery permissions and lifecycle

The [delivery identity](clusters/clouddsp-local/rabbitmq/reconciliation-rbac.yaml)
can manage StatefulSets, Services and NetworkPolicies in `clouddsp-data`.
Existing-object mutations are restricted to the five chart names; create is
kind-scoped because Kubernetes cannot restrict create by `resourceNames`.
Pods and PVCs are read-only. There is no direct PVC/PV mutation, Pod exec,
Job/Pod creation, app Deployment mutation, namespace creation, CRD or cluster
RBAC permission.

Helm history requires namespace-wide Secret CRUD, including data-service
credentials. StatefulSet mutation can create Pods mounting other namespace
Secrets or retained PVCs, so direct API denials do not establish isolation
from the data namespace. This is a trusted delivery identity. Root Flux and
trusted Git writers retain administrative authority. Force, takeover, automatic
rollback/uninstall remediation, failed-upgrade cleanup and Helm test hooks are
disabled; full drift correction remains enabled. Upgrades explicitly use
`serverSideApply: disabled`: the handoff encountered old exec handlers retained
by server-side apply when adding TCP handlers. Helm's three-way merge removes
the old handlers through an in-place patch. This changes only RabbitMQ's Helm
apply method; drift correction still uses Flux's server-side comparison.
See [Flux upgrade configuration](https://fluxcd.io/flux/components/helm/helmreleases/#upgrade-configuration).

## Verify and recover

From the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-rabbitmq --namespace clouddsp-data
ruby k8Deployment/kubernetes/scripts/releases/rabbitmq-release.rb verify
ruby k8Deployment/kubernetes/scripts/releases/rabbitmq-release.rb smoke
ruby k8Deployment/kubernetes/scripts/stages/rabbitmq/rabbitmq-processing-topology.rb verify
ruby k8Deployment/kubernetes/scripts/stages/rabbitmq/rabbitmq-source-intake-bootstrap.rb verify
python3 k8Deployment/kubernetes/tests/keda/keda-authentication-smoke.py --expect-idle
```

The adapter requires the exact reviewed active HelmRelease configuration and
current-generation Ready, native chart revision/storage, source/render/stored/
live parity, a bound claim, Ready Pod, locked running image, and local health.
The AMQP smoke creates one disposable versioned Job and removes it after success.
Omit `--expect-idle` during real work. Native install/adopt stop whenever the
HelmRelease exists, even failed, suspended or deleting; lookup errors fail closed.
Explicit absence preserves the original native fresh-install/adoption guards.

Inspect a failed release, broker events, PVC binding and native history. Repair
and publish versioned configuration, then reconcile:

```sh
flux reconcile helmrelease clouddsp-rabbitmq --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

Do not uninstall the release, delete its claim, remove finalizers or reapply raw
manifests to recover a failed handoff. Git removal of an active HelmRelease is a
lifecycle operation, not a harmless way to stop reconciliation. Cluster pause
and resume remain `k3d cluster stop/start clouddsp-local`.

[MinIO delivery](minio.md) waits for RabbitMQ; Job API, intake and all three
workers wait directly for MinIO while preserving the dependencies above.
