# Local cluster scripts

These non-interactive scripts manage only the versioned local k3d profile
declared in [`../cluster/k3d.yaml`](../cluster/k3d.yaml). Run the commands from
the repository root, or change into this directory first; each script resolves
its own location and does not depend on the current working directory.

## Prerequisites

Start Docker Desktop, then confirm that the local command-line tools are
available:

```bash
docker info
k3d version
kubectl version --client
```

`k3d` uses Docker to create the K3s server, agent, load-balancer, and local
registry containers. `kubectl` is the Kubernetes client used by the status
portion of `cluster.sh`; it connects through the `k3d-clouddsp-local` context
created by k3d.

## Create the cluster

```bash
./k8Deployment/kubernetes/scripts/cluster.sh create
```

`create` is also the default action, so this equivalent command is available:

```bash
./k8Deployment/kubernetes/scripts/cluster.sh
```

The script validates Docker, k3d, kubectl, and the cluster configuration first.
It creates the `clouddsp-local` cluster only when it is absent, then lists its
nodes and K3s system Pods. If the cluster already exists, it leaves it intact;
this protects persistent development data added in later phases.

k3d adds the `k3d-clouddsp-local` context to the standard kubeconfig and, by
default, switches the current context to it. Use the context explicitly when a
command must be unambiguous:

```bash
kubectl --context k3d-clouddsp-local get nodes
kubectl --context k3d-clouddsp-local get pods --all-namespaces
```

## Inspect cluster status

```bash
./k8Deployment/kubernetes/scripts/cluster.sh status
```

This is read-only. It shows the three Kubernetes nodes and the Pods in every
namespace. The `default` namespace remains empty until a CloudDSP workload is
deployed; K3s platform Pods such as CoreDNS and Traefik run in `kube-system`.

## Verify the local registry

Apply the namespace manifest first, then run the end-to-end registry smoke
test:

```bash
kubectl --context k3d-clouddsp-local apply \
  --filename k8Deployment/kubernetes/cluster/namespaces.yaml

./k8Deployment/kubernetes/scripts/verify-registry.sh
```

The script pulls the exact `busybox:1.37.0` release for the Mac's active
container architecture, tags and pushes it as
`clouddsp-registry.localhost:5001/registry-smoke:1.37.0`, then applies the
versioned `registry-smoke` Job in `clouddsp-app`. The Job uses
`imagePullPolicy: Always`, so K3s must query the local registry on each run.
The script prints the Job's Pod pull events and logs, then leaves the completed
Job and Pod available for inspection. Kubernetes's TTL controller removes them
about five minutes after completion. A later test run deletes only that prior
smoke Job before it creates a fresh one. The harmless test image remains in the
dedicated registry for future checks and is removed when the registry itself is
cleaned up.

## Build the local frontend image

```bash
./k8Deployment/kubernetes/scripts/build-frontend-image.sh
```

This uses the local frontend's two-stage Dockerfile: the pinned official Node
image builds Vite assets, then the pinned official NGINX image serves only those
assets as a non-root process on container port 8080. The script reads the
public Keycloak/browser settings from the ignored
`services/frontend/app/.env.production`, pushes the arm64 image to
`clouddsp-registry.localhost:5001/frontend`, and prints its immutable registry
digest plus Docker's local uncompressed size. It does not deploy a Pod; that is
the following Kubernetes delivery task.

## Verify HTTP routing on port 8080

```bash
./k8Deployment/kubernetes/scripts/verify-http-routing.sh
```

This test pushes a separate BusyBox HTTP-server image, deploys a `Deployment`,
`ClusterIP` Service, and Traefik `Ingress`, then checks the exact response at:

```text
http://routing-smoke.localhost:8080/
```

The resources remain in `clouddsp-app` for inspection. They are deliberately
isolated behind `routing-smoke.localhost`, so they do not match a future
CloudDSP hostname. Remove only this routing test when finished:

```bash
kubectl --context k3d-clouddsp-local delete \
  --filename k8Deployment/kubernetes/tests/routing-smoke/http-routing-smoke.yaml
```

## Delete the local cluster

```bash
./k8Deployment/kubernetes/scripts/cleanup-cluster.sh --confirm
```

This is destructive. It removes exactly the `clouddsp-local` k3d cluster and
the `clouddsp-registry.localhost` image registry. It also destroys workloads,
the K3s node containers, and any future PVC-backed local development data or
images stored in that dedicated registry. It does not use `--all` and does not
delete other k3d clusters, Docker containers, images, networks, or registries.

Use `--help` with any script to print its supported command form without
changing local resources:

```bash
./k8Deployment/kubernetes/scripts/cluster.sh --help
./k8Deployment/kubernetes/scripts/cleanup-cluster.sh --help
./k8Deployment/kubernetes/scripts/verify-registry.sh --help
./k8Deployment/kubernetes/scripts/verify-http-routing.sh --help
```
