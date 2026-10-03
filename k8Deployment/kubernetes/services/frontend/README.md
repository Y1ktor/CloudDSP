# Local frontend delivery

CloudDSP has one React source tree at [`frontend/`](../../../../frontend/).
Its cloud and local build profiles select their authentication, endpoints,
MIDI playback sample sources, and content security policy. Shared screens,
audio tools, navigation, and informational pages are edited once in that tree.
This directory owns the local Docker/NGINX delivery recipe and retained
Kubernetes resource baselines. The [frontend Helm chart](../../helm/frontend/README.md)
owns the live Deployment, Service, and Ingress.

## Build the local image

From the repository root:

```bash
./k8Deployment/kubernetes/scripts/images/build-frontend-image.sh
```

The helper requires Docker with BuildKit, the dedicated
`clouddsp-registry.localhost:5001` k3d registry, and the ignored public browser
configuration at `k8Deployment/.local/frontend.env.production`. It builds the
Apple Silicon `linux/arm64` image, pushes the descriptive
`frontend:0.6.0-shared-profiles` tag to that local registry, and prints the
immutable digest and image size. Review the digest before updating
`images.lock.yaml`, the Helm image value, and the retained source manifest.
This helper does not deploy the image.

The build keeps source and delivery configuration in separate contexts:

```text
frontend/                         named BuildKit context: frontend
  package.json + package-lock.json -> npm ci --ignore-scripts
  src/ + public/ + Vite config     -> npm run build:local -> dist/local/

this directory                   primary Docker context
  Dockerfile + nginx.conf          -> pinned NGINX runtime + local static assets
```

Only package inputs, `src/`, `public/`, `index.html`, `csp.js`, and
`vite.config.js` are copied from the named context. Developer environment
files, host `node_modules`, and prior `dist` output are not copied into build
layers. The final image contains only NGINX configuration and generated local
assets; Node, React source, and build configuration are absent from it. Both
base image digests are recorded in [`images.lock.yaml`](../../images.lock.yaml).

Vite writes the local CSP meta tag and `dist/local/csp-header.conf` from the
same policy. The Dockerfile installs that include outside the public document
root and runs `nginx -t` before finishing the image. NGINX runs as its ordinary
non-root account on port 8080; the Service maps port 80 to that listener and
Traefik serves the browser at `http://clouddsp.localhost:8080/`.

## Public local configuration

`k8Deployment/.local/frontend.env.production` supplies only settings that
every browser user may read. The helper parses individual `KEY=value` entries
as data; it does not execute the file. Required entries must appear exactly
once; optional endpoint entries may contain an empty value.

| Setting | Purpose |
| --- | --- |
| `VITE_OIDC_ISSUER` | Public Keycloak realm issuer and discovery origin. |
| `VITE_OIDC_CLIENT_ID` | Keycloak's public `clouddsp-react` SPA identifier. |
| `VITE_OIDC_REDIRECT_URI` | Exact registered browser callback. |
| `VITE_OIDC_POST_LOGOUT_REDIRECT_URI` | Exact registered logout destination. |
| `VITE_JOB_API_URL` | Browser-facing local Job API URL; normally its same-origin route. |
| `VITE_OBJECT_STORAGE_URL` | Public MinIO origin for sample playback and owner-authorized uploads. |
| `VITE_WEBSOCKET_URL` | Optional browser-facing realtime endpoint. |
| `VITE_DEMO_ASSET_ORIGIN` | Optional public demo origin. |

Vite embeds these values into JavaScript. Administrator passwords, database
credentials, MinIO keys, RabbitMQ credentials, and client secrets belong only
to server-side Secret sources. A public SPA uses Authorization Code with PKCE
and cannot protect a bundled client secret.

The local authentication adapter uses Keycloak discovery, S256 PKCE,
callback-state validation, and short-lived `sessionStorage` state. Keycloak
handles registration, email verification, password reset, and sign-out. The
cloud profile uses its Cognito adapter; selecting a profile preserves each
provider's public configuration and token contract.

## Local MIDI playback samples

The local profile loads shared, non-user instrument sounds from
`clouddsp-midi-samples` through the `http://minio.localhost:8080` S3 Ingress.
This bucket allows anonymous `GetObject` only. User uploads, stems, generated
MIDI, and results remain in the private `clouddsp-uploads` bucket and require
owner-checked, short-lived presigned access.

The reviewed sample catalog contains 461 objects, about 52 MB: piano note
regions, two FluidR3 soundfont instruments, and five drum sounds. The
[sample mirror](../minio/midi_sample_mirror.py) validates every source against
the [SHA-256 catalog](../minio/midi-sample-assets.lock.json) before publishing
it. Fresh bootstrap initializes this bank before the local frontend starts.
For a deliberate maintenance run:

```bash
python3 k8Deployment/kubernetes/services/minio/midi_sample_mirror.py
```

Review upstream changes before using `--record-lock` to regenerate the
catalog. Browser codec selection and percent-encoding of sharp-note filenames
remain in the shared frontend's local sample adapter. Attribution comes from
the [FluidR3 project](https://github.com/gleitz/midi-js-soundfonts),
[Splendid Grand sample project](https://github.com/smpldsnds/sfzinstruments-splendid-grand-piano),
and [drum-machine sample project](https://github.com/smpldsnds/drum-machines).

## Source history

[PROVENANCE.md](PROVENANCE.md) preserves the earlier local snapshot record and
explains the move to shared source. The local image version
`0.6.0-shared-profiles` records that build-layout change. Deployment versions
continue to identify reviewed immutable images independently of the shared
frontend source layout.
