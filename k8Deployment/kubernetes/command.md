# CloudDSP local Kubernetes command reference

Run these commands from the CloudDSP repository root
(`.../CloudDSP`). They inspect the current local k3d/K3s environment and do
not change cluster state unless a command is explicitly marked as destructive.
Every Kubernetes command names `k3d-clouddsp-local` explicitly so it cannot
silently query another kubeconfig context.

The current foundation resources are split between these namespaces:

| Namespace | Current contents |
| --- | --- |
| `kube-system` | K3s platform components such as CoreDNS, Traefik, ServiceLB, metrics-server, and local-path-provisioner. |
| `clouddsp-app` | The `routing-smoke` Deployment, Service, Ingress, and any temporarily visible `registry-smoke` Job. |
| `clouddsp-system` | Reserved for CloudDSP shared platform configuration; currently empty. |
| `clouddsp-data` | Reserved for stateful dependencies and future PVC tests; currently empty. |
| `default` | Empty; CloudDSP workloads should not be created here. |

> Do not use `kubectl get secrets -o yaml`, `kubectl describe secret`, or a
> command that decodes Secret data in shared terminal output. Future Secrets may
> contain local database passwords, OIDC client credentials, or MinIO keys.

## 1. Confirm the active cluster connection

```bash
# Show the context kubectl would use when --context is omitted.
kubectl config current-context

# List every locally configured Kubernetes context and its selected namespace.
kubectl config get-contexts

# Show the Kubernetes API endpoint and core services for this exact cluster.
kubectl --context k3d-clouddsp-local cluster-info

# Show client and server Kubernetes versions.
kubectl --context k3d-clouddsp-local version

# Run the project's concise node and system-Pod status view.
./k8Deployment/kubernetes/scripts/cluster.sh status
```

## 2. Inspect k3d and Docker Desktop resources

```bash
# List k3d clusters. The project cluster is named clouddsp-local.
k3d cluster list

# List k3d-managed registries and the cluster currently associated with each.
k3d registry list

# Show the Docker containers that form the k3d cluster: one K3s server, two
# K3s agents, the k3d load balancer, and k3d's helper container.
docker ps --filter name=k3d-clouddsp-local \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

# Show the dedicated Docker Registry container and its loopback port mapping.
docker ps --filter name=clouddsp-registry.localhost \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

# Show the Docker network used by the current k3d cluster.
docker network ls --filter name=k3d-clouddsp-local

# Display current CPU and memory use of node/load-balancer/registry containers.
docker stats --no-stream
```

## 3. Inspect Kubernetes nodes and namespaces

```bash
# One control-plane server plus two schedulable K3s agent nodes.
kubectl --context k3d-clouddsp-local get nodes -o wide

# Node labels, capacity, conditions, allocated resources, and running Pods.
kubectl --context k3d-clouddsp-local describe node \
  k3d-clouddsp-local-agent-0

# List all namespaces and their purpose labels.
kubectl --context k3d-clouddsp-local get namespaces --show-labels

# Inspect one CloudDSP namespace's labels and lifecycle state.
kubectl --context k3d-clouddsp-local describe namespace clouddsp-app
```

## 4. List workloads across the cluster

```bash
# A convenient summary of common workload resources. It does NOT include every
# Kubernetes type, such as Ingresses, PVCs, ConfigMaps, or EndpointSlices.
kubectl --context k3d-clouddsp-local get all --all-namespaces

# A fuller foundation view, including routing and endpoint-discovery objects.
kubectl --context k3d-clouddsp-local get \
  deployments,replicasets,daemonsets,jobs,pods,services,ingresses,endpointslices \
  --all-namespaces -o wide

# Watch Pods appear, become ready, complete, or disappear in every namespace.
kubectl --context k3d-clouddsp-local get pods --all-namespaces --watch

# Show recent cluster events in chronological order. Events are temporary
# diagnostics, so old scheduling and image-pull messages eventually expire.
kubectl --context k3d-clouddsp-local get events --all-namespaces \
  --sort-by=.lastTimestamp
```

## 5. Inspect K3s platform Pods and Traefik

```bash
# Built-in K3s components live in kube-system, not default.
kubectl --context k3d-clouddsp-local --namespace kube-system get pods -o wide

# See which controller owns each platform Pod: Deployment/ReplicaSet,
# DaemonSet, or completed bootstrap Job.
kubectl --context k3d-clouddsp-local --namespace kube-system get pods \
  -o custom-columns='NAME:.metadata.name,OWNER:.metadata.ownerReferences[0].kind,OWNER_NAME:.metadata.ownerReferences[0].name,CONTAINERS:.spec.containers[*].name'

# Show the CoreDNS, Traefik, and local-path-related controllers and Services.
kubectl --context k3d-clouddsp-local --namespace kube-system get \
  deployments,daemonsets,jobs,services -o wide

# Inspect Traefik's LoadBalancer Service. Its endpoints ultimately lead to the
# Traefik Pod that reads CloudDSP Ingress rules.
kubectl --context k3d-clouddsp-local --namespace kube-system describe service traefik

# Read Traefik controller logs. This is useful after changing an Ingress rule.
kubectl --context k3d-clouddsp-local --namespace kube-system logs \
  deployment/traefik --tail=100
```

## 6. Inspect the CloudDSP application namespace

```bash
# Show the routing smoke Deployment, its ReplicaSet, the Service, and Ingress.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get \
  deployments,replicasets,pods,services,ingresses -o wide

# Show every label on the Pod. These labels connect Deployment ownership and
# Service endpoint selection.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get pods \
  --show-labels

# Detailed Deployment status, rollout events, ReplicaSet references, and Pod
# template settings such as the image, volume, security context, and resources.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app describe deployment routing-smoke

# Read the BusyBox HTTP server's container output.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app logs \
  deployment/routing-smoke

# Show all events for the application namespace, including image pulls,
# scheduling, endpoint changes, and Ingress controller observations.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get events \
  --sort-by=.lastTimestamp
```

## 7. Inspect HTTP routing, Services, and endpoints

```bash
# Inspect the host/path rule, Traefik class, backend Service, and HTTP entry
# point annotation that make routing-smoke.localhost:8080 work.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app describe ingress routing-smoke

# Show the Service's virtual ClusterIP, port 80, and selector.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get service routing-smoke -o wide

# EndpointSlices contain the ready Pod IP and port selected by the Service.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get endpointslices \
  --selector kubernetes.io/service-name=routing-smoke -o wide

# Send the same host-aware request used by the routing test. --resolve ensures
# the test hostname maps to loopback while curl still sends the correct Host.
curl --fail --silent --show-error \
  --resolve routing-smoke.localhost:8080:127.0.0.1 \
  http://routing-smoke.localhost:8080/
```

## 8. Inspect Jobs and their Pods

```bash
# Registry smoke Jobs remain visible only until their five-minute TTL expires.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get jobs -o wide

# List the Pods created by the fixed smoke-test Job name.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get pods \
  --selector job-name=registry-smoke -o wide

# Watch the Job's lifecycle in one terminal, then run verify-registry.sh in
# another terminal to observe Pending → Running → Complete.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get jobs --watch

# Once visible, inspect a Job's completion condition, Pod template, and events.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app describe job registry-smoke
```

## 9. Inspect storage before the PVC test

```bash
# K3s's local-path provisioner should supply the default StorageClass.
kubectl --context k3d-clouddsp-local get storageclasses
kubectl --context k3d-clouddsp-local describe storageclass local-path

# There are no CloudDSP PVCs yet. These commands will show future Persistent
# Volume Claims and their backing PersistentVolumes after the storage test.
kubectl --context k3d-clouddsp-local get persistentvolumeclaims --all-namespaces
kubectl --context k3d-clouddsp-local get persistentvolumes
```

## 10. Inspect the local image registry

```bash
# The Docker Registry API lists repositories stored in the loopback-only local
# registry. The current smoke tests create registry-smoke and routing-smoke.
curl --fail --silent http://clouddsp-registry.localhost:5001/v2/_catalog

# List tags for either stored smoke-test repository.
curl --fail --silent \
  http://clouddsp-registry.localhost:5001/v2/registry-smoke/tags/list
curl --fail --silent \
  http://clouddsp-registry.localhost:5001/v2/routing-smoke/tags/list

# List the host Docker image cache entries tagged for the local registry.
docker image ls clouddsp-registry.localhost:5001

# Inspect the manifest Docker sees for the exact routing test image.
docker manifest inspect \
  clouddsp-registry.localhost:5001/routing-smoke:1.37.0
```

## 11. Inspect Pod containers inside a k3d node

In k3d, `docker ps` shows the K3s node containers, not every Kubernetes workload
container. Each K3s node runs `containerd`, which owns its Pods' sandbox and
workload containers. Run `crictl` inside a node to inspect that lower layer.

```bash
# List Pod sandboxes and workload containers, including exited Job containers,
# inside the agent currently running the routing smoke Deployment.
docker exec k3d-clouddsp-local-agent-1 crictl pods
docker exec k3d-clouddsp-local-agent-1 crictl ps -a

# List images cached by containerd on that agent node.
docker exec k3d-clouddsp-local-agent-1 crictl images

# Inspect the server node instead. It runs the Kubernetes control plane and may
# also schedule K3s system Pods in this small local topology.
docker exec k3d-clouddsp-local-server-0 crictl ps -a
```

## 12. Focused resource map

Use these commands together to connect Kubernetes abstractions to the current
HTTP test:

```bash
# Controller keeps one Pod running.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get deployment routing-smoke

# ReplicaSet is the Deployment's immediate Pod owner.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get replicasets

# Pod runs the BusyBox HTTP process and has its own Pod IP.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get pods -o wide

# Service selects the ready Pod by label and provides a stable ClusterIP/DNS name.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get service routing-smoke

# Ingress tells Traefik to route routing-smoke.localhost requests to that Service.
kubectl --context k3d-clouddsp-local --namespace clouddsp-app get ingress routing-smoke
```

## Destructive test cleanup

The following command removes only the HTTP routing smoke-test Deployment,
Service, and Ingress. It stops the test Pod and removes the route; it does not
remove the cluster, registry, namespaces, or test image stored in the registry.

```bash
kubectl --context k3d-clouddsp-local delete \
  --filename k8Deployment/kubernetes/tests/routing-smoke/http-routing-smoke.yaml
```
