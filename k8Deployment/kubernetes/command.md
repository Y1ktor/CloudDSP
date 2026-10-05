# CloudDSP local Kubernetes command reference

Run these commands from the repository root. They inspect the fixed
`k3d-clouddsp-local` context. For deployment and destructive cleanup commands,
see the [operator guide](scripts/README.md).

## Cluster and release overview

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh stages
./k8Deployment/kubernetes/scripts/deploy-local.sh plan
./k8Deployment/kubernetes/scripts/deploy-local.sh verify

k3d cluster list
k3d registry list
kubectl config get-contexts
kubectl --context k3d-clouddsp-local cluster-info
kubectl --context k3d-clouddsp-local get nodes -o wide
helm list --kube-context k3d-clouddsp-local --all-namespaces
```

`stages` derives the ordered stage names from the bootstrap source without
contacting the cluster. `plan` is a preflight; `verify` runs the reviewed
read-only gates and stops at the first failure. Neither deploys a workload.

| Namespace | Expected purpose after full bootstrap |
| --- | --- |
| `kube-system` | K3s components: CoreDNS, Traefik, ServiceLB, metrics-server, and local-path provisioner. |
| `clouddsp-data` | PostgreSQL, RabbitMQ, MinIO, Keycloak, Mailpit, their Services, storage, and bootstrap configuration. |
| `clouddsp-app` | Frontend, Job API, upload-intake, both dispatchers, workers, application Secrets, and scaling resources. |
| `clouddsp-system` | Project platform namespace reserved by the foundation. |
| `keda` | KEDA controllers and supporting resources. |
| `default` | No CloudDSP workload is expected here. |

## Workloads, events, and logs

```bash
kubectl --context k3d-clouddsp-local get namespaces --show-labels
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get \
  deployments,pods,services,ingresses -o wide
kubectl --context k3d-clouddsp-local --namespace clouddsp-data get \
  statefulsets,deployments,pods,services,ingresses -o wide
kubectl --context k3d-clouddsp-local --namespace kube-system get pods -o wide
kubectl --context k3d-clouddsp-local --namespace keda get deployments,pods

kubectl --context k3d-clouddsp-local --namespace clouddsp-app describe \
  deployment clouddsp-job-api
kubectl --context k3d-clouddsp-local --namespace clouddsp-app logs \
  deployment/clouddsp-job-api --tail=100
kubectl --context k3d-clouddsp-local get events --all-namespaces \
  --sort-by=.lastTimestamp
```

For a failing bootstrap Job, use the named namespace and Job from the stage
output with `kubectl describe job` and `kubectl logs job/NAME`. A completed Job
may already have been removed by its runner or TTL. Keep credentials, access
tokens, and presigned URLs out of shared diagnostic output.

## Worker scaling

```bash
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get \
  deployments,scaledobjects,horizontalpodautoscalers
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get \
  triggerauthentications
kubectl --context k3d-clouddsp-local --namespace keda logs \
  deployment/keda-operator --tail=100
```

Demucs, Basic Pitch, and ADTOF normally have zero idle replicas. KEDA manages
Deployment replicas through `ScaledObject` resources and generated HPAs.
Demucs also accounts for durable PostgreSQL work so an acknowledged broker
message does not make an active task disappear from the scaling signal. Consult
the worker chart and service README before changing scaling; a zero replica
count alone is not a failure.

## Routing and protected API paths

```bash
kubectl --context k3d-clouddsp-local --namespace clouddsp-app describe \
  ingress clouddsp-frontend
kubectl --context k3d-clouddsp-local --namespace clouddsp-app describe \
  ingress clouddsp-job-api
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get \
  endpointslices --selector kubernetes.io/service-name=clouddsp-job-api

curl --noproxy '*' --fail --silent --show-error \
  --resolve clouddsp.localhost:8080:127.0.0.1 http://clouddsp.localhost:8080/
curl --noproxy '*' --silent --show-error --output /dev/null \
  --write-out '%{http_code}\n' \
  --resolve clouddsp.localhost:8080:127.0.0.1 http://clouddsp.localhost:8080/auth/me
```

The frontend request should succeed. An unauthenticated `/auth/me` request
should return **401**. The frontend owns `/`; the Job API owns `/auth` and
`/jobs` on the same origin. Keycloak and the MinIO S3 API use separate local
hosts. MinIO user artifacts require signed access; successful frontend routing
does not prove login, upload, or processing.

## Persistent storage and Secret metadata

```bash
kubectl --context k3d-clouddsp-local get storageclasses
kubectl --context k3d-clouddsp-local --namespace clouddsp-data get \
  persistentvolumeclaims
kubectl --context k3d-clouddsp-local get persistentvolumes
kubectl --context k3d-clouddsp-local --namespace clouddsp-data get secrets
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get secrets
```

PostgreSQL, MinIO, and RabbitMQ have local-path PVCs. Keycloak persists its
realm and users in its separate PostgreSQL database. PVCs should be Bound;
this does not prove their contents or a backup. The Secret commands above show
metadata only. Do not print or decode Secret data in shared terminal output.

## Registry and Docker resources

```bash
curl --noproxy '*' --fail --silent --show-error \
  http://clouddsp-registry.localhost:5001/v2/_catalog
curl --noproxy '*' --fail --silent --show-error \
  http://clouddsp-registry.localhost:5001/v2/frontend/tags/list
docker image ls --filter 'reference=clouddsp-registry.localhost:5001/*'
docker ps --filter name=k3d-clouddsp-local \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker ps --filter name=clouddsp-registry.localhost \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker stats --no-stream
```

The [image lock](images.lock.yaml) specifies immutable workload references and
public Docker Hub sources. Normal cleanup retains the registry. A retained tag
or host Docker cache entry is not digest verification; the deployment runners
perform that check before using an image.

For lower-level diagnostics, each k3d node runs containerd. These read commands
show its cache and containers, distinct from the host Docker image cache:

```bash
docker exec k3d-clouddsp-local-agent-0 crictl pods
docker exec k3d-clouddsp-local-agent-0 crictl ps -a
docker exec k3d-clouddsp-local-agent-0 crictl images
```

The earlier routing-only foundation reference is preserved in
[history](docs/history/foundation-command-reference.md).
