# Cluster foundation and image mirroring

The [operator guide](../../scripts/README.md) lists prerequisites and the
complete fresh command. This reference explains the lower-level foundation
and image helpers; they are already called by `bootstrap-platform`.

## Versioned cluster profile

[cluster/k3d.yaml](../../cluster/k3d.yaml) pins K3s `v1.35.5-k3s1`, one server,
two agents, explicit node-role labels, and loopback bindings:

| Host endpoint | Purpose |
| --- | --- |
| `127.0.0.1:6550` | Kubernetes API; context `k3d-clouddsp-local`. |
| `127.0.0.1:5001` | Dedicated local image registry. |
| `127.0.0.1:8080` | Ingress HTTP mapping. |
| `127.0.0.1:8443` | Ingress HTTPS mapping; current application has no certificate bootstrap. |

The namespace manifest creates `clouddsp-system`, `clouddsp-app`, and
`clouddsp-data`. Packaged CoreDNS, Traefik, and local-path provisioning remain
owned by K3s. The local application images are reviewed for `linux/arm64`;
this profile does not establish x86_64 or GPU support.

```bash
ruby ./k8Deployment/kubernetes/scripts/deploy-local-foundation.rb plan
ruby ./k8Deployment/kubernetes/scripts/deploy-local-foundation.rb verify
./k8Deployment/kubernetes/scripts/cluster.sh status
```

Foundation `plan` requires an absent target cluster and a healthy retained
registry or a registry that can be created. `bootstrap` calls the versioned
cluster creator, waits for Ready nodes, creates validated namespaces, and
checks exact role labels, registry endpoint, namespace labels, and packaged
system Deployments. A failed creation remains for inspection. An existing
cluster blocks fresh foundation bootstrap; `verify` checks the existing
foundation without installing services.

The underlying `cluster.sh create` is idempotent for its cluster creation
boundary; calling it directly creates only cluster containers, not the full
application or all bootstrap state. It may change the current kubeconfig
context. Administrative commands should pass the explicit context:

```bash
kubectl --context k3d-clouddsp-local get nodes
kubectl --context k3d-clouddsp-local get pods --all-namespaces
```

## Root image preparation

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh prepare
```

Prepare runs six ordered steps: fresh foundation plan, image plan, public
source verification, foundation bootstrap, local mirror, and digest
verification. Public source checks happen before cluster creation so missing
published images stop early. Prepare leaves an installed cluster/namespace
foundation and images, with application Secrets, Helm releases, and external
service bootstrap still pending. It is a partial fresh trial; a subsequent
full bootstrap refuses that already-created cluster.

## Image registry modes

[images.lock.yaml](../../images.lock.yaml) pins immutable digests and
reviewed source tags. The mirror currently selects 19 local-registry entries;
each includes ARM64 support. Tags provide traceability, while digests identify
exact manifest/index bytes.

```bash
ruby ./k8Deployment/kubernetes/scripts/image-registry-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/image-registry-stage.rb verify-source
ruby ./k8Deployment/kubernetes/scripts/image-registry-stage.rb verify
```

| Mode | Behavior |
| --- | --- |
| `plan` | Read committed local image entries and report their Docker Hub tags/digests. |
| `verify-source` | Check Buildx and anonymous access to every locked public digest; local registry not required. |
| `mirror` | Copy missing reviewed public manifest/index bytes into the dedicated local registry, then verify digests. |
| `verify` | Check reviewed image availability/digests in both public and local registries. |
| `publish` | Publish reviewed local images using the saved Docker CLI login; fail on unrelated digest mismatch. |

The public source is [y1ktor/clouddsp](https://hub.docker.com/r/y1ktor/clouddsp).
Publication is maintainer work; a fresh user deployment needs anonymous pulls,
not Docker Hub credentials. The helper does not rewrite the lock.

Buildx accesses the local registry through `127.0.0.1:5001`, avoiding Docker
Desktop's proxy/custom-host HTTPS probes. Workloads retain the stable
`clouddsp-registry.localhost:5001` reference. A tag mistakenly pointing to one
child of a locked OCI index can be repaired only for that specific known
index-child mismatch; unrelated existing mismatches stop the mirror. Matching
images in a retained registry are reused.

## Cleanup boundary

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh cleanup
./k8Deployment/kubernetes/scripts/deploy-local.sh purge-registry
```

Cleanup deletes the fixed cluster and PVC-backed data while retaining the
registry. It connects the registry to the dedicated `clouddsp-registry-hold`
Docker network before k3d removes the cluster network. Replacement creation
attaches that retained registry. Purge requires the cluster to be absent,
then removes registry storage and the hold network. Both support an already
absent target and leave ignored local files in place.

The checked-in [trial records](../trials/) describe historical VM results;
current stage lists and locks remain the authority for a new run. Active
registry/network tests are described in the [test-tool guide](image-builds-and-smoke-tools.md).
