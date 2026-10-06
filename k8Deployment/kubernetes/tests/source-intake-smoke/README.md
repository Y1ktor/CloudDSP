# Source-to-outbox integration smoke

[`source-to-outbox-smoke-job.yaml`](source-to-outbox-smoke-job.yaml) verifies
the live Job API → presigned MinIO upload → native source notification →
upload-intake → PostgreSQL transition. It then publishes one duplicate source
notification and requires exactly one pending `demucs.requested` outbox row.
The client removes its temporary Keycloak user/client, MinIO object, and
owner-bound database row in `finally`. It prints only fixed progress and safe
failure categories.

The Job uses the current `images.job-api.immutableReference` because its
container borrows that image's pinned Python, Psycopg, and Boto3 packages.
Its own command starts a disposable client, never the API server. RabbitMQ's
[`ingress NetworkPolicy`](../../services/rabbitmq/rabbitmq-ingress-network-policy.yaml)
allows only this fixed-name, short-lived data-namespace Job to reach the
management port for its duplicate publish and queue-drain check. It grants no
management access to the long-running upload-intake Pod.

## Flux cluster run (both dispatchers adopted)

Use a clean checkout of the branch selected by `spec.ref.branch` in the
committed [`gotk-sync.yaml`](../../gitops/clusters/clouddsp-local/flux-system/gotk-sync.yaml):
`main` for CloudDSP, or your configured fork branch. Confirm the live Flux
GitRepository uses that repository and branch, and that `origin` pushes to
the same repository. Publish and reconcile source configuration changes before
starting this smoke. The editor rejects detached HEAD, a different branch,
uncommitted source changes, and sources pinned by tag/semver/name/commit.
Keep this checkout on the same branch through pause, test, and restoration.
Both publishers have separate HelmReleases. The editor validates both manifests
before staging either pause line; publish both changes in the same commit.
Never directly upgrade a Flux-owned dispatcher, even while its HelmRelease is
suspended. Keep the normal health and empty-queue preflight:

```sh
kubectl --context k3d-clouddsp-local -n clouddsp-data exec statefulset/clouddsp-rabbitmq -- rabbitmqctl list_queues -p /clouddsp name messages_ready messages_unacknowledged
ruby k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb verify
ruby k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb verify
ruby k8Deployment/kubernetes/scripts/gitops/dispatchers-smoke-values.rb pause
smoke_branch="$(git branch --show-current)"
git diff -- k8Deployment/kubernetes/gitops/clusters/clouddsp-local/dispatcher/helmrelease.yaml k8Deployment/kubernetes/gitops/clusters/clouddsp-local/generic-dispatcher/helmrelease.yaml
git add k8Deployment/kubernetes/gitops/clusters/clouddsp-local/dispatcher/helmrelease.yaml k8Deployment/kubernetes/gitops/clusters/clouddsp-local/generic-dispatcher/helmrelease.yaml
git commit -m 'test(k8s): pause both Flux dispatchers for source intake smoke'
git push origin "HEAD:refs/heads/${smoke_branch}"
flux reconcile kustomization flux-system --with-source --context k3d-clouddsp-local --namespace flux-system --timeout=3m
for release in clouddsp-dispatcher clouddsp-generic-dispatcher; do
  flux reconcile helmrelease "$release" --with-source --context k3d-clouddsp-local --namespace flux-system --timeout=3m
done
ruby k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb verify-smoke-pause
ruby k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb verify-smoke-pause
kubectl --context k3d-clouddsp-local -n clouddsp-app get pods --selector app.kubernetes.io/name=dispatcher
```

Require **no dispatcher Pods**, including terminating ones, before creating the
Job. The Flux pause check verifies current-generation readiness, exact pause
values, native stored/live specs, and complete Pod termination for each publisher.
These are read-only checks. Normal `verify` intentionally rejects the paused configuration.
The helper stages only each reviewed valuesFiles line; it does not push Git or
change the cluster. Check source/HelmRelease conditions if reconciliation has
not yet observed the published commit.

```sh
kubectl --context k3d-clouddsp-local -n clouddsp-data create -f k8Deployment/kubernetes/tests/source-intake-smoke/source-to-outbox-smoke-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data wait job/source-to-outbox-smoke --for=condition=complete --timeout=300s
kubectl --context k3d-clouddsp-local -n clouddsp-data logs job/source-to-outbox-smoke
```

After the final success marker confirms object, row, and temporary Keycloak
identity cleanup, delete that exact Job and restore both owners' configuration.
On failure or interruption, inspect cleanup before restoration; a surviving
synthetic row must not be published to a real worker. Do not restore blindly
from a shell exit trap or delete unrelated rows/queue messages.

```sh
kubectl --context k3d-clouddsp-local -n clouddsp-data delete job/source-to-outbox-smoke --wait=true
ruby k8Deployment/kubernetes/scripts/gitops/dispatchers-smoke-values.rb restore
smoke_branch="$(git branch --show-current)"
git diff -- k8Deployment/kubernetes/gitops/clusters/clouddsp-local/dispatcher/helmrelease.yaml k8Deployment/kubernetes/gitops/clusters/clouddsp-local/generic-dispatcher/helmrelease.yaml
git add k8Deployment/kubernetes/gitops/clusters/clouddsp-local/dispatcher/helmrelease.yaml k8Deployment/kubernetes/gitops/clusters/clouddsp-local/generic-dispatcher/helmrelease.yaml
git commit -m 'test(k8s): restore both Flux dispatchers after source intake smoke'
git push origin "HEAD:refs/heads/${smoke_branch}"
flux reconcile kustomization flux-system --with-source --context k3d-clouddsp-local --namespace flux-system --timeout=3m
for release in clouddsp-dispatcher clouddsp-generic-dispatcher; do
  flux reconcile helmrelease "$release" --with-source --context k3d-clouddsp-local --namespace flux-system --timeout=3m
done
ruby k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb verify
ruby k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb verify
ruby k8Deployment/kubernetes/scripts/releases/upload-intake-release.rb verify
kubectl --context k3d-clouddsp-local -n clouddsp-data exec statefulset/clouddsp-rabbitmq -- rabbitmqctl list_queues -p /clouddsp name messages_ready messages_unacknowledged
```

Git remains authoritative during the pause and restoration, and Flux drift
correction stays enabled. Each Git revision can advance other adopted native
chart revisions with Revision packaging; unchanged Pod templates need no rollout.

## Native Helm cluster run (before Flux handoffs)

Use this section only while neither dispatcher has a Flux HelmRelease.
The test requires an empty source-intake queue and both dispatcher releases
temporarily at zero replicas. Without that pause, a dispatcher can publish the
new row before the test observes its pending state and can send the deliberately
tiny test WAV toward a Demucs worker. Check the queue and release health first,
then use each chart's versioned pause values:

```bash
kubectl --context k3d-clouddsp-local -n clouddsp-data exec statefulset/clouddsp-rabbitmq -- rabbitmqctl list_queues -p /clouddsp name messages_ready messages_unacknowledged
./k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb verify
./k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb verify
helm upgrade clouddsp-dispatcher k8Deployment/kubernetes/helm/dispatcher --kube-context k3d-clouddsp-local --namespace clouddsp-app --reset-values --values k8Deployment/kubernetes/helm/dispatcher/values.smoke-pause.yaml --wait --timeout 3m
helm upgrade clouddsp-generic-dispatcher k8Deployment/kubernetes/helm/generic-dispatcher --kube-context k3d-clouddsp-local --namespace clouddsp-app --reset-values --values k8Deployment/kubernetes/helm/generic-dispatcher/values.smoke-pause.yaml --wait --timeout 3m
kubectl --context k3d-clouddsp-local -n clouddsp-app get deployment clouddsp-dispatcher clouddsp-generic-dispatcher
kubectl --context k3d-clouddsp-local -n clouddsp-data create -f k8Deployment/kubernetes/tests/source-intake-smoke/source-to-outbox-smoke-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data wait job/source-to-outbox-smoke --for=condition=complete --timeout=300s
kubectl --context k3d-clouddsp-local -n clouddsp-data logs job/source-to-outbox-smoke
```

If the Job passes and reports cleanup, delete that exact disposable Job before
restoring the dispatchers. If it fails, inspect its safe log and cleanup state
first; an interrupted Pod may leave a test row that would become publishable
on restoration. Do not delete unrelated Jobs or queue messages.

```bash
kubectl --context k3d-clouddsp-local -n clouddsp-data delete job/source-to-outbox-smoke --wait=true
helm upgrade clouddsp-dispatcher k8Deployment/kubernetes/helm/dispatcher --kube-context k3d-clouddsp-local --namespace clouddsp-app --reset-values --wait --timeout 3m
helm upgrade clouddsp-generic-dispatcher k8Deployment/kubernetes/helm/generic-dispatcher --kube-context k3d-clouddsp-local --namespace clouddsp-app --reset-values --wait --timeout 3m
./k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb verify
./k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb verify
./k8Deployment/kubernetes/scripts/releases/upload-intake-release.rb verify
```

On 2026-09-26, the first run failed at the duplicate publish because the
RabbitMQ NetworkPolicy did not admit this smoke Pod to port 15672. Its cleanup
completed. A fixed-name, label-constrained policy exception was added, and the
second run passed. The test Job was deleted, both dispatcher releases were
restored to one replica at revision 3, and the source and Demucs queues were
empty afterward.
