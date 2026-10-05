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

## Live-cluster run

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
