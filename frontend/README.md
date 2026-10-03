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

## Application structure

[`src/App.jsx`](src/App.jsx) composes navigation, dialogs, and routes.
[`useStudioApp`](src/app/useStudioApp.js) owns the shared tab-local workspace
and account state, which stays mounted while switching informational tabs.

| Module | Responsibility |
| --- | --- |
| `src/app/useAppAuthentication.js` | Provider session actions, pending email verification, and requests with a fresh provider token. |
| `src/app/useJobHistory.js` | Account-backed job library reads and terminal-job deletion. |
| `src/app/useJobSnapshots.js` | Snapshot hydration, revision checks, request de-duplication, and bounded retry backoff. |
| `src/app/useJobRealtime.js` | WebSocket subscriptions, heartbeat/reconnect, and polling recovery. |
| `src/app/useJobSubmission.js` | Durable direct-upload POST contracts and linked-source submissions. |
| `src/app/useDemoCatalog.js` | Abortable loading of the validated public demo catalog. |
| `src/app/jobSnapshots.js` / `quotaMessages.js` | Artifact readiness, signed-URL reuse, linked-source guards, and quota messages. |
| `src/components/AppNavBar.jsx` | Shared navigation and account controls. |

Demo jobs remain browser-local, and account history remains server-backed.
The realtime hook consumes stable job refs after the controller synchronizes
them; notifications prompt snapshot reads while polling handles missed hints.

## Workspace structure

[`StemSplitter`](src/components/StemSplitter/StemSplitter.jsx) coordinates the
shared workspace layout, upload actions, and popup props. Its responsibilities
are split into modules under `src/components/StemSplitter/Workspace/`:

| Module | Responsibility |
| --- | --- |
| [`useStemSplitterSession`](src/components/StemSplitter/Workspace/useStemSplitterSession.js) | Audio/MIDI hooks, lazy instruments, tempo, track selection, drum controls, editor sessions, and undo |
| [`useWorkspaceTimeline`](src/components/StemSplitter/Workspace/useWorkspaceTimeline.js) | Timeline geometry, viewport refs, seeking, playhead and cycle dragging, and audio-clock playhead coordination |
| [`WorkspaceTimeline`](src/components/StemSplitter/Workspace/WorkspaceTimeline.jsx) | Track list, ruler, viewport-bounded grid, and playhead presentation |
| [`useProjectDownloads`](src/components/StemSplitter/Workspace/useProjectDownloads.js) | Download artifact memos, selected artifacts, and popup state |
| [`projectTracks`](src/components/StemSplitter/Workspace/projectTracks.js), [`projectDownloads`](src/components/StemSplitter/Workspace/projectDownloads.js), and [`sourceUpload`](src/components/StemSplitter/Workspace/sourceUpload.js) | Pure track/status/drum-row models, archive names and artifacts, and source-file validation |
| [`WorkspaceTransportControls`](src/components/StemSplitter/Workspace/WorkspaceTransportControls.jsx) and [`WorkspaceNotices`](src/components/StemSplitter/Workspace/WorkspaceNotices.jsx) | Playback controls, downloads, demo information, and processing/readiness messages |
| [`MidiScheduler`](src/components/StemSplitter/Workspace/MidiScheduler.jsx) | Memoized per-track bridge to the shared MIDI scheduling hook |

## MIDI editor structure

[`MidiEditorPopup`](src/components/StemSplitter/MidiEditorPopup.jsx) coordinates
the editor layout and passes its existing public props to modules under
`src/components/StemSplitter/MidiEditor/`:

| Module | Responsibility |
| --- | --- |
| [`useMidiEditorPopup`](src/components/StemSplitter/MidiEditor/useMidiEditorPopup.js) | Session state, selection, audition, context actions, scrolling, and playhead coordination |
| [`MidiEditorToolbar`](src/components/StemSplitter/MidiEditor/MidiEditorToolbar.jsx) and [`MidiEditorNoteControls`](src/components/StemSplitter/MidiEditor/MidiEditorNoteControls.jsx) | Transport, track controls, edit history, velocity, and popup zoom |
| [`MidiEditorKeyboard`](src/components/StemSplitter/MidiEditor/MidiEditorKeyboard.jsx) | Piano keys or named drum lanes with mute, solo, and gain controls |
| [`VisibleMidiEditorNotes`](src/components/StemSplitter/MidiEditor/VisibleMidiEditorNotes.jsx) and [`midiEditorNotes`](src/components/StemSplitter/MidiEditor/midiEditorNotes.js) | Memoized viewport rendering and a separate time-sorted note index |
| [`MidiEditorDragPreview`](src/components/StemSplitter/MidiEditor/MidiEditorDragPreview.jsx) | Drag lane, replication previews, and selection rectangle |
| [`MidiEditorDialogs`](src/components/StemSplitter/MidiEditor/MidiEditorDialogs.jsx) and [`MidiEditorContextMenu`](src/components/StemSplitter/MidiEditor/MidiEditorContextMenu.jsx) | Shortcut help, revert confirmation, and context menu presentation |

Editing and export still use the shared
[`useMidiEditorOperations`](src/hooks/useMidiEditorOperations.js) and
[`useMidiExport`](src/hooks/useMidiExport.js) hooks. Rendering keeps original
note-array indices intact so selection, undo, and export address the same
notes; sorting only affects the renderer's separate index. The shared audio
clock remains authoritative:
[`useTransportPlayhead`](src/hooks/useTransportPlayhead.js) writes playhead
transforms directly, without React updates on every animation frame.

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
