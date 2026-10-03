# Local frontend source provenance

## Current ownership

CloudDSP's React application now lives in the single repository-root
[`frontend/`](../../../../frontend/) tree. Its cloud and local profiles choose
their authentication, public configuration, storage/sample integration, and
CSP. This service directory owns the local Docker/NGINX delivery recipe and
retained Kubernetes resource baselines. The
[frontend Helm chart](../../helm/frontend/README.md) owns the live local
resources.

The source consolidation was authorized on 2026-10-02. Shared UI changes no
longer require copying screens between cloud and Kubernetes trees. The local
Keycloak, access-token, MinIO sample, and browser-storage behavior was retained
as the shared frontend's local profile. The cloud profile retains Cognito and
AWS service integration.

The local image `0.6.0-shared-profiles` consumes `frontend/` as a named BuildKit
context and runs `npm run build:local`. Only `dist/local/` reaches the NGINX
runtime. Ignored public configuration moved from the earlier
`app/.env.production` location to
`k8Deployment/.local/frontend.env.production`; the helper passes its public
values explicitly as build arguments.

### Consolidation validation

On 2026-10-02, both profile builds and 19 frontend tests passed. Lint completed
with existing warnings. The Docker build wiring tests passed 5 tests with 29
assertions, and catalog review matched all 461 locked MinIO sample sources
against the shared dependency tree.

The local image was published as
`y1ktor/clouddsp:frontend-0.6.0-shared-profiles` at manifest digest
`sha256:913e4264b8f54debcc78501640784a8aee6c78d35614d2a0046111c5f088c55a`.
Anonymous Docker Hub verification passed for all 19 locked images. A scoped
frontend Helm upgrade reached revision 3 and passed ownership, specification,
readiness, route, CSP, and static-asset checks.

A disposable local browser account completed PKCE sign-in, a four-second
two-stem upload, polling through completion, stem/MIDI/BPM display, MinIO MIDI
playback, session restoration, saved-job reopening, and Keycloak sign-out.
Its test account, job, and objects were removed. The cloud profile's sign-in
and registration dialogs and shared K8 page were checked in a local preview;
the AWS website was not published by this consolidation.

## Original Kubernetes snapshot

The first local frontend was a reviewed Git object archive of the cloud
application. Its source record is preserved here for tracing earlier images:

| Field | Recorded value |
| --- | --- |
| Original cloud source path | `cloudDeployment/frontend-react/` |
| Original local source path | `k8Deployment/kubernetes/services/frontend/app/` |
| Source Git commit | `6393ae3eb4153b883baf993e7c1d887252f8de1f` |
| Source commit subject | `feat: add local frontend oidc client` |
| Copy method | Git object archive of the recorded commit |
| Copied scope | Every frontend file tracked at that commit |
| Working-tree changes used for that snapshot | None |

The Git archive excluded the cloud tree's untracked configuration, dependencies,
and generated assets. Later local changes replaced Cognito with Keycloak
Authorization Code with S256 PKCE, introduced MinIO playback sample delivery,
and preserved the browser-local tutorial marker.

## Informational-page synchronization on 2026-10-02

Before consolidation, the user requested the newest cloud Architecture and K8
pages in the local deployment. The reviewed cloud working tree supplied those
screens, styles, diagram, component icons, and license records. Local
authentication and service adapters stayed in place.

That synchronization produced the reviewed image
`0.5.3-local-k8-docs`, published to the local registry and the public
`y1ktor/clouddsp:frontend-0.5.3-local-k8-docs` tag at the same manifest digest.
The image lock, Helm values, and retained source manifest pinned those bytes.
The local Helm release then passed ownership, readiness, HTTP, and static-asset
verification. This record describes that earlier release; the current image
lock identifies the version deployed by a fresh bootstrap.

## Configuration boundary

Every `VITE_*` value is bundled into JavaScript and visible to browser users.
Public issuers, SPA identifiers, and browser endpoints may be configured;
passwords, private API keys, client secrets, database credentials, queue
credentials, MinIO keys, and Kubernetes credentials may not be bundled.
Environment files, `node_modules`, and generated output are excluded from
source synchronization and local image source layers.
