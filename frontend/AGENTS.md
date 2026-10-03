# Shared frontend instructions

## Scope and ownership

This directory is the canonical React application for the cloud and local
Kubernetes deployments. Common screens, styles, assets, audio/MIDI hooks,
package metadata, and tests have one source here. Do not create another
frontend source copy inside either deployment directory.

Read [`../cloudDeployment/AGENTS.md`](../cloudDeployment/AGENTS.md) for the
product, durable-job, media-limit, quota, retention, ownership, browser
performance, and security invariants. Read
[`../k8Deployment/AGENTS.md`](../k8Deployment/AGENTS.md) when changing the local
profile or its delivery integration. Backend and infrastructure ownership
remains with those deployment trees; this shared browser source is an
intentional exception to their directory boundary.

## Platform boundaries

Use `src/platform/cloud/` for Cognito/AWS integration and
`src/platform/local/` for Keycloak/local-service integration. Keep common UI
and transport code shared. Vite selects a platform adapter at build time;
do not make a built cloud bundle switch to local authentication at runtime.

Preserve each backend's token contract: cloud calls use Cognito ID tokens,
while local calls use Keycloak OAuth access tokens from Authorization Code
with S256 PKCE. Keep authentication flows, issuer/audience validation,
browser service URLs, sample origins, processing labels, and generated CSP
appropriate to the selected profile. Never introduce local storage origins
or OIDC settings into the cloud policy merely to make the local build pass.

All `VITE_*` settings are public bundle contents. Keep credentials, access
tokens, client secrets, database passwords, and storage/broker credentials
out of environment examples, build arguments, browser logs, and assets.
Public examples live in `profiles/cloud.env.example` and
`profiles/local.env.example`; populated `.env*` configuration is ignored.

## Hook naming

Name React hooks with a `use` prefix and keep their filenames aligned with
their exported hook names. For example,
[`src/hooks/useAudioMultiTrackPlayer.js`](src/hooks/useAudioMultiTrackPlayer.js)
exports `useAudioMultiTrackPlayer`. Keep shared transport behavior in this hook
for both profiles.

## Validation

From this directory, run `npm run lint`, `npm test`, `npm run build:cloud`,
and `npm run build:local` for shared application changes. Cloud mode is
`cloud` and writes `dist/cloud/`; local mode is `k8` and writes `dist/local/`.
Keep generated output and dependency directories untracked. Validate the
affected authentication, audio/MIDI, or network behavior with an appropriate
browser check when a build alone cannot establish correctness.

Building does not publish the website or replace a running local image.
Delivery configuration, release locks, and deployment commands belong to
their respective deployment trees.
