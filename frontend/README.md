# CloudDSP frontend

This is the canonical React/Vite application for the AWS and local Kubernetes
deployments. Screens, editor components, audio/MIDI hooks, styles, assets, and
the dependency lockfile are shared. The application starts from the cloud UI
baseline; adapters in `src/platform/` select authentication, service behavior,
instrument samples, and deployment-specific text at build time.

## Profiles and ownership

| Profile | Vite mode | Browser integration | Generated output |
| --- | --- | --- | --- |
| Cloud | `cloud` | Cognito, AWS Job API/WebSockets, cloud sample delivery | `dist/cloud/` |
| Local | `k8` | Keycloak OIDC/PKCE, local Job API, MinIO instrument samples | `dist/local/` |

Cloud backend and CloudFormation source remains in `../cloudDeployment/`.
Local service code, Helm charts, NGINX configuration, and the frontend Docker
recipe remain in `../k8Deployment/`. The earlier standalone EQ prototype and
its unused React wrapper are retained in
[`../archive/eq/`](../archive/eq/README.md).

Use one shared screen or hook for a common product change. Put authentication,
endpoint behavior, sample origins, and processing labels in the relevant
platform adapter. A build selects one adapter; public environment settings do
not switch an already built cloud bundle into a local bundle.

## Install and configure

From the repository root:

```bash
cd frontend
npm ci
```

For the cloud profile, create an ignored file with public outputs from your
CloudFormation deployment:

```bash
cp profiles/cloud.env.example .env.cloud.local
${EDITOR:-vi} .env.cloud.local
npm run dev:cloud
```

The cloud profile needs the Cognito User Pool ID, public browser client ID,
Job API URL, and WebSocket URL. The optional demo proxy origin lets a
development browser fetch the hosted demo catalog through the same origin.

For a manual local build or development session:

```bash
cp profiles/local.env.example .env.k8.local
${EDITOR:-vi} .env.k8.local
npm run dev:local
```

Use the browser-facing local URLs in the example. A browser cannot resolve
Kubernetes ClusterIP service names. Keycloak redirect and post-logout URLs
must match the browser origin registered for the public client.

The local Docker build uses a separate ignored public configuration file,
`../k8Deployment/.local/frontend.env.production`, which its image-build
script passes explicitly as build arguments. The script builds the shared
source in this directory with the local profile. The
[local frontend delivery guide](../k8Deployment/kubernetes/services/frontend/README.md)
documents that build and the versioned NGINX/Kubernetes resources.

All `VITE_*` values are embedded in downloadable browser assets. These files
may contain public IDs and URLs, but never passwords, access tokens, client
secrets, AWS keys, database credentials, or MinIO/RabbitMQ credentials. The
populated environment files and generated `dist/` output are ignored by Git.

## Validate and build

```bash
npm run lint
npm test
npm run build:cloud
npm run build:local
```

Run both profile builds after shared frontend changes. The cloud build uses
the cloud environment and CSP; the local build uses the local environment
and CSP. Upload only `dist/cloud/` to the AWS website bucket. The local NGINX
image packages only `dist/local/`. Keep each build's generated CSP aligned
with its delivery headers.

Deployment commands are in the [repository README](../README.md). Building
the application does not publish assets, replace a locked local image, or
update a running Helm release; those are separate delivery steps.

## Browser invariants

Keep authentication tokens out of logs and public build configuration. Cloud
ownership uses the immutable Cognito `sub`; the local API validates the
Keycloak access token. Presigned artifact URLs are temporary downloads, and
polling remains the recovery path for missed notifications.

[`useAudioMultiTrackPlayer`](src/hooks/useAudioMultiTrackPlayer.js) owns the
shared Web Audio transport. Hook filenames use the `use` prefix to match their
exports. The shared audio clock, bounded timeline rendering, lazy instrument
lifecycle, and MIDI scheduling rules are described in
[browser-performance.md](../cloudDeployment/docs/browser-performance.md).
Both profiles must preserve them when their platform integration changes.
