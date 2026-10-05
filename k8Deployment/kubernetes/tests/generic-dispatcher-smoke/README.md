# Controlled generic-dispatcher routing smoke

The [Job](generic-dispatcher-basic-pitch-routing-smoke-job.yaml) exercises
PostgreSQL outbox → generic dispatcher → Basic Pitch request queue. A restricted
database role can execute only the three reviewed create/status/cleanup functions.
A separate RabbitMQ identity can read/ack only that queue, with no configure or
publish permission. The client validates the exact synthetic delivery and its
persistent envelope before removing its owner-bound database row. It does not
create a real stem object or run inference. On failure it preserves evidence;
inspect synthetic data before restoring workers or rerunning.

## Provision test identities separately

These optional smoke identities are outside the ordinary application Secret
catalog. Use the adjacent example templates to create ignored local copies
with fresh passwords; use the same database values in its app/data copies and
the same RabbitMQ values in its app/data copies. Never commit those copies.
The database name is `clouddsp_job_api`; the reviewed usernames are
`clouddsp-generic-dispatcher-smoke` and `clouddsp-basic-pitch-smoke`.

- [Database runtime template](generic-dispatcher-smoke-database-credentials.secret.example.yaml)
- [Database temporary bootstrap template](generic-dispatcher-smoke-database-bootstrap-credentials.secret.example.yaml)
- [RabbitMQ runtime template](basic-pitch-smoke-rabbitmq-credentials.secret.example.yaml)
- [RabbitMQ temporary bootstrap template](../../services/rabbitmq/rabbitmq-basic-pitch-smoke-bootstrap-credentials.secret.example.yaml)

Apply the four ignored Secret copies, then run these versioned bootstrap Jobs
if the restricted identities are absent or deliberately rotated:

```sh
kubectl --context k3d-clouddsp-local create -f k8Deployment/kubernetes/tests/generic-dispatcher-smoke/generic-dispatcher-smoke-database-bootstrap-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data wait job/generic-dispatcher-smoke-database-bootstrap --for=condition=complete --timeout=180s
kubectl --context k3d-clouddsp-local create -f k8Deployment/kubernetes/services/rabbitmq/rabbitmq-basic-pitch-smoke-consumer-bootstrap-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data wait job/rabbitmq-basic-pitch-smoke-consumer-bootstrap --for=condition=complete --timeout=180s
kubectl --context k3d-clouddsp-local -n clouddsp-data delete job/generic-dispatcher-smoke-database-bootstrap job/rabbitmq-basic-pitch-smoke-consumer-bootstrap --wait=true
kubectl --context k3d-clouddsp-local -n clouddsp-data delete secret/clouddsp-generic-dispatcher-smoke-database-bootstrap-credentials secret/clouddsp-basic-pitch-smoke-rabbitmq-bootstrap-credentials
```

Use `create` deliberately: a completed fixed-name Job is not rerun by apply.
Inspect an existing/failed Job before deleting that exact test Job for a retry.
Bootstrap output contains no passwords; never dump the ignored Secret files.

## Isolate the synthetic delivery

Run only when Basic Pitch has no ordinary queue messages, active task leases,
or retries and there are no worker Pods. Pause cannot be used on a busy cluster.
The commands in this section apply only before Basic Pitch Flux adoption.
The current GitOps root selects its HelmRelease; do not run these competing
native Helm upgrades on that cluster. A reviewed GitOps pause/restore path is
required for this synthetic routing test. Use the separate
[worker processing smoke](../basic-pitch-worker-smoke/README.md) to verify the
Flux-owned worker through the real dispatcher and inference path.

```sh
kubectl --context k3d-clouddsp-local -n clouddsp-data exec statefulset/clouddsp-rabbitmq -- rabbitmqctl list_queues -p /clouddsp name messages_ready messages_unacknowledged
ruby k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb verify-idle
ruby k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb verify
helm upgrade clouddsp-basic-pitch k8Deployment/kubernetes/helm/basic-pitch --kube-context k3d-clouddsp-local --namespace clouddsp-app --reset-values --values k8Deployment/kubernetes/helm/basic-pitch/values.routing-smoke-pause.yaml --wait --timeout 3m
kubectl --context k3d-clouddsp-local -n clouddsp-app wait scaledobject/clouddsp-basic-pitch-rabbitmq-scaler --for=condition=Paused=true --timeout=60s
kubectl --context k3d-clouddsp-local -n clouddsp-app get pods --selector app.kubernetes.io/name=basic-pitch
```

Require no Pods, including terminating ones, before creating the smoke Job.
`paused-replicas: "0"` tells KEDA to pause autoscaling and hold the Deployment
at zero; it prevents a competing worker from consuming the synthetic message.
Normal chart rendering is unchanged when maintenance is disabled.

```sh
kubectl --context k3d-clouddsp-local create -f k8Deployment/kubernetes/tests/generic-dispatcher-smoke/generic-dispatcher-basic-pitch-routing-smoke-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-app wait job/generic-dispatcher-basic-pitch-routing-smoke --for=condition=complete --timeout=180s
kubectl --context k3d-clouddsp-local -n clouddsp-app logs job/generic-dispatcher-basic-pitch-routing-smoke
```

## Restore after successful acknowledgement and cleanup

Require the client's final success marker and empty queue before restoring
scaling. On failure inspect the synthetic row/message first; do not purge queues
or delete other users' jobs. Delete the exact successful test Job, then restore:

```sh
kubectl --context k3d-clouddsp-local -n clouddsp-app delete job/generic-dispatcher-basic-pitch-routing-smoke --wait=true
helm upgrade clouddsp-basic-pitch k8Deployment/kubernetes/helm/basic-pitch --kube-context k3d-clouddsp-local --namespace clouddsp-app --reset-values --wait --timeout 3m
ruby k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb verify-idle
ruby k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb verify
kubectl --context k3d-clouddsp-local -n clouddsp-data exec statefulset/clouddsp-rabbitmq -- rabbitmqctl list_queues -p /clouddsp name messages_ready messages_unacknowledged
```

Runtime smoke credentials may remain for deliberate repeat tests. Delete only
the test identities/Secrets and their three functions if fully retiring this
optional facility; no application identity is shared with it.
