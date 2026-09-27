# Local React frontend build boundary

## Purpose of this directory

This directory owns the **Kubernetes-local frontend variant and delivery
layer** for CloudDSP's browser application. Its [`app/`](app/) directory is a
versioned copy of the React application, reserved for local-only OIDC and
local-service integration. It also owns the multi-stage Dockerfile, local
image-build script, and Kubernetes Deployment, Service, and Ingress manifests.

The cloud implementation remains preserved in
[`../../../../cloudDeployment/frontend-react/`](../../../../cloudDeployment/frontend-react/).
The two trees must not be edited as though they were the same deployment.
Instead, any UI change needed in both environments is deliberately reviewed
and copied in the appropriate direction. This prevents a local Keycloak/API
change from accidentally changing the existing Cognito/AWS deployment.

## Current result of this task

The initial local source copy now exists in [`app/`](app/). Its exact cloud
source revision, copy scope, and synchronization rules are recorded in
[`app/PROVENANCE.md`](app/PROVENANCE.md). It now has a local Keycloak OIDC
adapter, image recipe, locally built image, and applied Kubernetes delivery
resources. The one-replica
[`frontend-deployment.yaml`](frontend-deployment.yaml) serves generated React
assets through NGINX on a non-root container port. The matching ClusterIP
[`frontend-service.yaml`](frontend-service.yaml) owns stable in-cluster DNS and
routes its HTTP port 80 only to ready Pods on their named container port
`http` (8080). The browser-facing
[`frontend-ingress.yaml`](frontend-ingress.yaml) makes that Service available
at `http://clouddsp.localhost:8080/` through Traefik.

An end-to-end local identity check has passed: a user can register in Keycloak,
receive and verify Mailpit email, create a password on the registration form,
return through the PKCE callback, and reach the signed-in React app. The
frontend now calls the local Job API through its same-origin Ingress and may
post the API-issued form directly to the one public MinIO S3 origin. The API's
upload-intake, job-detail, queue, and worker stages remain separate later
tasks; a successful browser object upload is not processing completion.

The local upload screen describes transfer to local storage and asynchronous
stem/MIDI processing, not AWS Batch. The welcome slides set a browser-local
`localStorage` marker when first shown, so a refresh, Keycloak redirect, or
later visit in the same browser profile does not reopen them. Clearing site
storage (or using a different browser profile) deliberately restores the
first-visit experience; no account preference or credential is stored there.

## Local MIDI playback samples

The React copy now loads its **shared instrument sounds** from the dedicated
`clouddsp-midi-samples` MinIO bucket through the existing
`http://minio.localhost:8080` S3 Ingress. This is distinct from generated MIDI
files: a user's source, stems, MIDI, and result URLs stay in the **private**
`clouddsp-uploads` bucket behind owner-checked, short-lived presigned URLs.
The shared sample bucket grants anonymous `GetObject` but not object writes or
bucket listing. It contains 226 Splendid Grand piano note regions in both OGG
and M4A (452 objects), two FluidR3 soundfont instruments in OGG and MP3 data
files (four objects), and five 808 drum M4A sounds: 461 objects, about 52 MB.
Browser-specific codec selection remains in `smplr`; the local adapter also
percent-encodes sharp-note filenames before MinIO receives them.

The versioned [sample mirror command](../minio/midi_sample_mirror.py) derives
the expected piano filenames from the lockfile-pinned `smplr` dependency,
checks every source byte against the reviewed
[SHA-256 catalog](../minio/midi-sample-assets.lock.json), then creates/fills
the one bucket. It uses the existing MinIO administrator Secret only on the
developer's machine and never puts credentials into the React image. Initial
population requires internet access once; subsequent browser playback needs
only the local cluster. From the repository root:

```bash
python3 k8Deployment/kubernetes/services/minio/midi_sample_mirror.py
```

If the `smplr` package or upstream samples change, review the sources first;
`--record-lock` intentionally regenerates the SHA-256 catalog. The bootstrap
must finish **before** rolling out the new frontend image, because its CSP no
longer permits the original online sample hosts. The original suppliers and
their licensing/attribution information are the [FluidR3 soundfont project](https://github.com/gleitz/midi-js-soundfonts),
[Splendid Grand piano sample project](https://github.com/smpldsnds/sfzinstruments-splendid-grand-piano),
and [drum-machine sample project](https://github.com/smpldsnds/drum-machines).

## Ownership boundary

| Concern | Owner | Reason |
| --- | --- | --- |
| Cloud React screens, audio UX, and Cognito implementation | `cloudDeployment/frontend-react/` | Preserved cloud deployment; it remains independent of the local variant. |
| Local React screens and local-only OIDC/API integration | `app/` | Starts from a recorded cloud revision, then changes only for the Kubernetes-local runtime. |
| Local container build, image provenance, and Kubernetes resources | This `k8Deployment/kubernetes/services/frontend/` directory | Keep delivery configuration versioned and independently reviewable. |
| Local identity server, realm, SMTP, and public browser client | `../keycloak/` | Keycloak is the identity provider; the frontend is only an OIDC public client. |
| Browser URL | `http://clouddsp.localhost:8080/` | This is the exact origin/callback registered as `clouddsp-react`; do not substitute a Pod IP or Service DNS name. |

## Build contract

The local image uses a **versioned build recipe in this directory** and builds
only [`app/`](app/). The Dockerfile must not silently read a
developer's globally installed Node modules, an untracked `.env.production`
file, or uncommitted cloud changes.

The container build has these stages:

```text
reviewed Node image
  -> npm ci from app/package-lock.json
  -> Vite production build
  -> reviewed NGINX image serving only generated dist/ assets
```

The digest-pinned official Node and NGINX bases are now recorded in
[`../../images.lock.yaml`](../../images.lock.yaml). Build and push the current
Apple-Silicon image with:

```bash
./k8Deployment/kubernetes/scripts/build-frontend-image.sh
```

The script reads only public browser values from the ignored
`app/.env.production`, passes them explicitly as Docker build arguments, and
prints the resulting immutable local-registry reference and Docker image size.
It does not copy that file into the Docker context. Do not use the Vite
development server as a Kubernetes runtime; it has different behavior,
development headers, and an unsuitable delivery model.

## Local OIDC boundary

The cloud baseline imported `amazon-cognito-identity-js` directly. The local
variant replaces that boundary with [`app/src/auth/oidc.js`](app/src/auth/oidc.js),
which implements Authorization Code + S256 PKCE against Keycloak discovery.
It validates callback `state`, keeps the PKCE verifier and token session in
`sessionStorage`, uses the OAuth access token for API/WebSocket authorization,
and delegates registration, email verification, and password reset to
Keycloak's own browser UI.

That adapter will use Keycloak's public metadata and client, not administrator
credentials:

| Public browser setting | Local value | Why it is safe in built assets |
| --- | --- | --- |
| OIDC issuer | `http://keycloak.localhost:8080/realms/clouddsp` | Identifies the public issuer and its discovery document. |
| OIDC client ID | `clouddsp-react` | Public SPA identifier; it is not a password. |
| Redirect URI | `http://clouddsp.localhost:8080/` | Exact URL registered in Keycloak for Authorization Code + PKCE. |
| Post-logout URI | `http://clouddsp.localhost:8080/` | Exact Keycloak post-logout destination. |
| Object-storage origin | `http://minio.localhost:8080` | Exact CSP `connect-src` destination for API-issued MinIO presigned POSTs; it contains no credential and does not make the bucket public. |

Never place the following in a Vite value, frontend image, ConfigMap visible to
the frontend, or browser storage: a Keycloak administrator password, a client
secret, a database password, a MinIO credential, or a RabbitMQ password. A
public SPA uses PKCE specifically because it cannot keep a client secret. Its
PKCE verifier is the one exception to ordinary browser-storage guidance: it is
kept only in `sessionStorage` for the few minutes required to survive the
Keycloak redirect, then removed before the authorization code is exchanged.
It is never placed in a Vite value, long-lived `localStorage`, log, image, or
Kubernetes resource.

`app/.env.production` is intentionally ignored by Git. When the local adapter
exists, it may contain only public browser configuration such as the issuer,
public client ID, local API origin, and one local object-storage origin. Vite
embeds every `VITE_*` value in the built JavaScript, so an API key that must
remain secret never belongs in this file. Kubernetes Secrets are for
server-side workloads, not browser assets.

## Completed delivery sequence

1. **Image build:** Node/NGINX bases are digest-pinned; the script builds and
   pushes the static arm64 image and records its immutable registry digest.
2. **Kubernetes delivery:** the one-replica Deployment, ClusterIP Service, and
   Traefik Ingress were originally applied for `clouddsp.localhost`. They are
   now owned by the [frontend Helm release](../../helm/frontend/README.md),
   which preserves their names, selectors, Pod identity, and browser route.
3. **Browser integration:** sign-up, Mailpit confirmation, password creation,
   OIDC code exchange, and authenticated frontend return have passed. Session
   refresh/expiry behavior should be revisited once the local protected API
   exists, because that is the resource server that will enforce access tokens.
