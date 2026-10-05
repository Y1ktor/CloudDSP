# Local deployment commands

Use these scripts for the versioned `clouddsp-local` k3d deployment. Commands
target context `k3d-clouddsp-local`; the standard published image profile is
Linux ARM64 with CPU workers. Run the examples from the repository root.
The [repository guide](../../../README.md) describes both deployment tracks;
the [local deployment plan](../deployment-orchestration-plan.md) records stage ownership.

## Prerequisites

- An ARM64 host with Docker Desktop or Docker Engine running and accessible
  to your user, plus the Docker Buildx plugin.
- `k3d`, compatible `kubectl`, `helm`, Ruby with standard YAML/JSON libraries,
  Bash, Git, and curl on `PATH`. Helm 4 is required for the optional one-time
  adoption commands that use its ownership flags.
- Python 3.9 or newer with its standard library, and AWS CLI v2. The S3 CLI
  connects to MinIO using local credentials; an AWS account is not required.
- Internet access for reviewed images, K3s/KEDA dependencies, and shared MIDI
  samples. Node.js is needed only when rebuilding the shared frontend.
- Free loopback ports `6550`, `5001`, `8080`, and `8443`. Current application
  browser routes use HTTP on port `8080`.

Fresh ARM64 VM trials used 8 CPUs, 16 GiB RAM, and a 100 GiB disk. That is a
validated configuration, not a measured minimum. The standard profile does
not validate x86_64 images or NVIDIA GPU execution.

Merge the local registry setting into Docker's existing daemon JSON, then
restart Docker:

```json
{
  "insecure-registries": ["clouddsp-registry.localhost:5001"]
}
```

The registry is bound to loopback. Docker Desktop exposes daemon settings
under Settings → Docker Engine; Linux normally uses `/etc/docker/daemon.json`.
See [foundation and image mirroring](../docs/reference/foundation-and-images.md)
for topology, registry lifecycle, and focused checks.

## Deploy a fresh cluster

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform
```

The target cluster must be absent. This command generates or validates ignored
local credential sources, creates the foundation, mirrors locked images from
public Docker Hub, and installs/bootstraps every service in dependency order.
It currently runs 93 stages; see the live list without contacting Kubernetes:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh stages
```

Every failed stage stops later work and leaves created resources for
inspection. A partial cluster cannot be treated as a new bootstrap. For a
fresh restart, inspect the reported stage, correct its inputs, then use the
cleanup/deploy lifecycle below. This workflow creates new service data; it
has no database, MinIO, or Keycloak data-restore step.

| Service | Browser URL |
| --- | --- |
| CloudDSP | [http://clouddsp.localhost:8080](http://clouddsp.localhost:8080) |
| Keycloak | [http://keycloak.localhost:8080](http://keycloak.localhost:8080) |
| Mailpit | [http://mailpit.localhost:8080](http://mailpit.localhost:8080) |

Register through CloudDSP and open its verification email in Mailpit. VM
loopback belongs to the VM; use a browser there or forward the ingress port
before using these addresses on the host.

## Initialize or choose credentials

No populated Secret manifest needs to be copied from a package dependency.
`bootstrap-platform` initializes all 33 ignored source files automatically.
To initialize them before deployment:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh secrets-init
```

To choose selected values, prepare the owner-only override file before the
first initialization:

```bash
mkdir -p k8Deployment/.local
chmod 700 k8Deployment/.local
cp k8Deployment/kubernetes/credentials/overrides.example.yaml \
  k8Deployment/.local/credentials-input.yaml
chmod 600 k8Deployment/.local/credentials-input.yaml
${EDITOR:-vi} k8Deployment/.local/credentials-input.yaml
./k8Deployment/kubernetes/scripts/deploy-local.sh secrets-init \
  --input k8Deployment/.local/credentials-input.yaml
```

Unspecified generated fields receive random values. A complete existing set
is validated and reused; a partial set or override against existing files
stops initialization. Values are not printed. The deployment owner can open
the ignored files to view them. Initialization does not rotate live accounts.
See [credentials and service identities](../docs/reference/credentials-and-identities.md)
for catalog contracts and individual helper boundaries.

## Verify or reconcile an existing cluster

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh plan
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
```

`plan` is a read-only ownership/input preflight. `verify` runs 56 ordered
read-only gates for credentials, service identities, Helm resources, schema,
Keycloak configuration, MinIO policies/buckets/notification, and KEDA. It stops
at the first failure and prints the focused diagnostic command. Verification
uses the ignored local credential sources, without printing their values.
Worker release checks include idle scale contracts; allow processing, cooldown,
and Pod termination to finish before full verification. It does not run
processing smoke tests or prove recovery of previous data.

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh reconcile
```

`reconcile` runs those gates but enables ten reviewed external-state runners.
They can complete wholly absent versioned database/broker bootstrap state,
restore an absent shared-sample read policy or source-upload notification,
and remove matching leftover temporary MinIO provisioning Secrets. Partial
state and drift stop the command. It does not create a cluster, install or
upgrade Helm releases, recreate buckets, repair MinIO users/policies, rotate
credentials, reconcile Keycloak realm state, or run product smoke tests.
See [database/schema](../docs/reference/database-and-schema.md),
[storage/messaging](../docs/reference/object-storage-and-messaging.md), and
[Helm/scaling](../docs/reference/helm-releases-and-scaling.md) for exact limits.

## Clean up and redeploy

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh cleanup
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
```

`cleanup` deletes the fixed cluster, Kubernetes resources, node containers,
and PVC-backed local service data. It retains the dedicated registry and its
images, plus ignored host configuration. The next bootstrap reuses matching
locked registry images. Cleanup also succeeds when the cluster is absent.

To delete the dedicated registry and its image data separately, first clean
up the cluster, then run:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh purge-registry
```

Purge refuses while the target cluster exists. A later fresh bootstrap fetches
locked public images from Docker Hub. Neither cleanup command removes
unrelated Docker resources.

## Other supported root modes and references

`prepare` creates only the foundation and image mirror. `bootstrap-mailpit`,
`bootstrap-postgresql`, `bootstrap-rabbitmq`, and `bootstrap-minio` are partial
fresh-cluster trials. Each starts with an absent cluster; they are alternative
trials, not commands to chain together. Partial PostgreSQL/RabbitMQ/MinIO
trials require initialized credential sources first. Use `bootstrap-platform`
for the complete deployment.

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh --help
```

## Script layout

`deploy-local.sh` is the public entry point. Its command names and stage order
are independent of the internal folders below. Focused helper commands in the
references use their full paths.

| Folder | Responsibility |
| --- | --- |
| `orchestration/` | Root coordinators, foundation creation, and ordered stage lists. |
| `releases/` | Component Helm install, adoption, verification, and smoke commands. |
| `stages/credentials/` | Local credential initialization and Secret/identity staging. |
| `stages/database/` | PostgreSQL roles, grants, and immutable schema migrations. |
| `stages/minio/` | Buckets, samples, IAM, notification setup, and storage checks. |
| `stages/rabbitmq/` | Broker users, source-intake setup, and processing topology. |
| `stages/keycloak/` | Keycloak database, realm/client bootstrap, and configuration checks. |
| `images/` | Locked registry mirroring, image builders, and image checks. |
| `maintenance/` | Cluster/registry cleanup, focused repairs, and opt-in restore rehearsals. |
| `lib/` | Shared Helm and credential helpers plus explicit script/path resolution. |

The Ruby, Bash, and Python path adapters resolve project roots from their own
locations. Coordinators resolve known child scripts through the shared registry,
so invocation from another working directory does not change the target files.
The shared release helper is `lib/helm-release.rb` (`HelmRelease`); it also
handles stateful component releases.

Focused current references: [foundation/images](../docs/reference/foundation-and-images.md),
[credentials/identities](../docs/reference/credentials-and-identities.md),
[database/schema](../docs/reference/database-and-schema.md),
[storage/messaging](../docs/reference/object-storage-and-messaging.md),
[Helm/scaling](../docs/reference/helm-releases-and-scaling.md), and
[image builds/smoke tools](../docs/reference/image-builds-and-smoke-tools.md).
The [earlier scripts reference](../docs/history/scripts-reference-before-reorganization.md)
preserves the full implementation history and prior trial notes; it is a
historical snapshot, not the current deployment runbook.
