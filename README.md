# CloudDSP

CloudDSP is a browser-based audio workspace for separating recordings into stems,
transcribing pitched instruments and drums to MIDI, and editing the results.
The repository contains an AWS deployment and a separate local Kubernetes
deployment of the processing services.

## Cloud

**[Open CloudDSP](https://y1ktor.com)**

The hosted application runs on AWS. The website includes an **Architecture**
page describing its authentication, upload, processing, storage, and result
delivery paths. Source code and CloudFormation templates are in
[`cloudDeployment/`](cloudDeployment/); the detailed design is in the
[cloud architecture documentation](cloudDeployment/docs/architecture.md).

## Local

### Prerequisites

- **An ARM64 host:** the published local images currently target
  `linux/arm64`. The deployment has been tested on Apple Silicon macOS and
  Ubuntu 24.04 ARM64. An x86_64 or NVIDIA GPU deployment needs a separate
  image and runtime profile.
- **Docker Desktop or Docker Engine, running and accessible to your user,**
  with the **Docker Buildx** plugin. Allocate enough resources to Docker or
  the Linux VM. The fresh VM trials used **8 CPUs, 16 GiB RAM, and a 100 GiB
  disk**; this is a tested configuration, not a measured minimum.
- **k3d, kubectl, and Helm** available on `PATH`. The checked-in cluster
  configuration pins K3s to `v1.35.5-k3s1`; use a compatible kubectl client.
- **Ruby** with its standard YAML and JSON libraries, plus **Git, Bash,
  and curl**.
- **Python 3.9 or newer:** the shared MIDI sample bootstrap uses `python3`
  and its standard library; no Python packages need to be installed.
- **AWS CLI v2:** the bootstrap uses its S3 client against MinIO. An AWS
  account or AWS credentials are not required for local deployment.
- **Internet access** for the pinned K3s/KEDA dependencies and public
  container images. The bootstrap fetches the reviewed CloudDSP images from
  [`y1ktor/clouddsp` on Docker Hub](https://hub.docker.com/r/y1ktor/clouddsp)
  and mirrors them into a dedicated local registry.
- **Available loopback ports:** `6550` for the Kubernetes API, `5001` for
  the registry, and `8080`/`8443` for the ingress mapping. The current
  application routes use HTTP on `8080`; certificates are not installed.
- **No existing `clouddsp-local` cluster** for a fresh deployment. The
  bootstrap stops if that cluster already exists. A retained CloudDSP image
  registry may be reused.

Configure Docker to allow the local HTTP registry before deploying. Merge
this entry into Docker's existing daemon JSON, preserving other settings,
then restart Docker. On Docker Desktop, use **Settings → Docker Engine**;
on Linux, the daemon configuration is usually `/etc/docker/daemon.json`.

```json
{
  "insecure-registries": ["clouddsp-registry.localhost:5001"]
}
```

The registry is bound to `127.0.0.1`. This setting applies to the local
development registry; Docker Hub remains the public image source.

You can check the tools before starting:

```bash
docker info
docker buildx version
k3d version
kubectl version --client
helm version
ruby -ryaml -rjson -e 'puts RUBY_VERSION'
python3 --version
aws --version
```

### Deploy

Clone the repository and run the complete bootstrap from its root:

```bash
git clone https://github.com/Y1ktor/CloudDSP.git
cd CloudDSP
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform
```

The local deployment creates a three-node k3d cluster, installs the services
through Helm, and bootstraps their databases, identities, buckets, policies,
and message queues in dependency order. PostgreSQL stores durable job state;
MinIO stores audio and MIDI; RabbitMQ delivers work to Demucs, Basic Pitch,
and ADTOF workers. Keycloak provides local sign-in, and Mailpit captures
registration and password-reset emails.

That one deployment command creates the cluster, mirrors the locked images,
generates local credentials, installs the Helm releases, and runs the
bootstrap verification gates. It stops at the first failed stage and leaves
the resources available for inspection. A fresh Ubuntu ARM64 trial completed
all 93 stages in about 16½ minutes; elapsed time depends on downloads and
machine resources.

Once deployment completes, open:

| Service | Local URL |
| --- | --- |
| CloudDSP | [http://clouddsp.localhost:8080](http://clouddsp.localhost:8080) |
| Keycloak | [http://keycloak.localhost:8080](http://keycloak.localhost:8080) |
| Mailpit inbox | [http://mailpit.localhost:8080](http://mailpit.localhost:8080) |

Register a CloudDSP account through the application and open the verification
email in Mailpit. The generated Keycloak administrator account is a separate
identity for managing the local realm.

These addresses resolve to the deployment machine's loopback interface. If
you deploy inside a VM, open the application there or forward the ingress
port to your host before using the same URLs.

### Choose or view credentials

No populated Secret templates are needed in the clone. The bootstrap creates
33 Secret source files under ignored `k8Deployment/.local/`, with directory
permissions `700` and file permissions `600`. Values are not printed during
initialization. The deployment user can open these files to view them; for
example, `keycloak-bootstrap-admin.secret.yaml` contains the administrator
username and password.

To choose selected credentials, do this **before** the first deployment or
`secrets-init` run:

```bash
mkdir -p k8Deployment/.local
chmod 700 k8Deployment/.local
cp k8Deployment/kubernetes/credentials/overrides.example.yaml \
  k8Deployment/.local/credentials-input.yaml
chmod 600 k8Deployment/.local/credentials-input.yaml
${EDITOR:-vi} k8Deployment/.local/credentials-input.yaml
./k8Deployment/kubernetes/scripts/deploy-local.sh secrets-init \
  --input k8Deployment/.local/credentials-input.yaml
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform
```

Use the group and field names in the
[credential catalog](k8Deployment/kubernetes/credentials/catalog.yaml).
The example shows the Keycloak administrator override; omitted fields are
generated. Custom passwords and MinIO secret keys must be at least 16
characters long. Matching runtime and bootstrap files receive the same
values automatically.

An existing complete credential set is validated and reused. A partial set
stops initialization, and overrides cannot replace an existing set. This
command initializes a fresh installation; it does not rotate live service
passwords. Keep populated files private and out of Git.

### Verify and clean up

Run the read-only verification command whenever you want to check the
deployment:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
```

It checks the cluster, reviewed images and Helm releases, service identities,
Secrets, schema, buckets, policies, and notification configuration. It is
separate from the end-to-end audio-processing smoke tests described in the
[deployment scripts documentation](k8Deployment/kubernetes/scripts/README.md).

Remove the cluster and its Kubernetes resources with:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh cleanup
```

**Cleanup deletes the local databases, MinIO objects, and PVC data.** It
retains the dedicated image registry and ignored `.local` credential files,
so the same bootstrap command can create another fresh cluster. Deployment
does not restore earlier application data.

After cluster cleanup, remove the registry and its stored images separately:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh purge-registry
```

The purge refuses to run while the CloudDSP cluster exists. A later deployment
will fetch the pinned images from Docker Hub again. These commands target the
fixed CloudDSP resources and do not remove unrelated Docker resources.

The local profile is intended for development and learning: it uses loopback
HTTP routes, a single control-plane node, and CPU processing. It does not
provide production availability, TLS, data recovery, or CUDA performance
validation. See the [orchestration plan](k8Deployment/kubernetes/deployment-orchestration-plan.md)
and [scripts guide](k8Deployment/kubernetes/scripts/README.md) for the full
stage list, verification details, and component smoke tests.
