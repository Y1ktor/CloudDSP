# CloudDSP architecture

## Purpose and core model

CloudDSP is a browser-based DAW with an asynchronous AWS audio-processing
pipeline. A signed-in user uploads one source track, AWS Batch separates it
into stems with Demucs, then Lambda functions independently produce MIDI for
the stems. The React client displays and edits the resulting audio and MIDI.

The central architecture decision is:

> DynamoDB is the durable source of truth for a processing job. S3 stores
> binary files. WebSockets only signal that the browser should fetch the
> current job snapshot.

Therefore a page reload, a Batch cold start, a broken socket, a duplicate
notification, an expired presigned URL, or a disconnected client must not
cause an already-produced result to disappear.

## Design principles

- Create an opaque **job_id** before a user uploads source audio.
- Use that job ID in every source key, generated artifact key, DynamoDB record,
  Batch/Lambda payload, log line, and browser subscription.
- Store S3 keys, status, and errors in DynamoDB; do not store expiring
  presigned URLs as canonical results.
- Authenticate the browser with Cognito. Authorize job reads and subscriptions
  with the authenticated Cognito subject rather than a client-supplied user ID.
- Persist an artifact and its DynamoDB state before emitting **job_updated**.
- Treat S3 events, EventBridge, asynchronous Lambda invocation, and WebSockets
  as at-least-once or best-effort transports.
- Keep GPU-intensive Demucs work in AWS Batch. Keep short CPU MIDI extraction
  work in Lambda container images.
- Use one browser-side Web Audio transport for source and stem playback. All
  audible tracks must start against one `AudioContext` clock; independent
  `HTMLAudioElement` clocks are not a DAW transport.
- Keep the audio clock, visual playhead, MIDI scheduling, and React UI on
  separate performance paths. A browser frame drop may skip a visual position,
  but must never trigger a transport, scroll, or MIDI recovery jump.

## System context

~~~mermaid
flowchart LR
    Browser["React / Vite browser client"]
    Cognito["Cognito User Pool"]
    HttpApi["API Gateway HTTP API<br/>JWT authorizer"]
    JobApi["Job API Lambda"]
    YtDlp["yt-dlp ingestion Lambda<br/>CPU / x86_64 image"]
    Jobs["DynamoDB Jobs<br/>durable state"]
    Uploads["Private S3 uploads bucket"]
    EventBridge["EventBridge upload rule"]
    Batch["AWS Batch GPU<br/>Demucs container"]
    Artifacts["Private S3 processed-artifacts bucket"]
    BasicPitch["Basic Pitch Lambda<br/>CPU / x86_64 image"]
    ADTOF["ADTOF Lambda<br/>CPU / x86_64 image"]
    SocketApi["API Gateway WebSocket API"]
    SocketAuth["WebSocket JWT authorizer Lambda"]
    Connections["DynamoDB Connections<br/>temporary subscriptions"]

    Browser <-->|"sign up / sign in<br/>ID token"| Cognito
    Browser -->|"POST /jobs, GET /jobs, DELETE /jobs/id<br/>Bearer ID token"| HttpApi
    HttpApi --> JobApi
    JobApi <--> Jobs
    JobApi -->|"size-constrained presigned POST"| Browser
    JobApi -->|"async invoke job_id + source URL"| YtDlp
    Browser -->|"PUT uploads/job_id/file"| Uploads
    YtDlp -->|"PUT uploads/job_id/linked-audio.wav"| Uploads
    Uploads --> EventBridge --> Batch
    Batch <--> Jobs
    Batch -->|"write stems/job_id/*"| Artifacts
    Batch -->|"async invoke per stem"| BasicPitch
    Batch -->|"async invoke drums"| ADTOF
    BasicPitch <--> Jobs
    ADTOF <--> Jobs
    BasicPitch -->|"write midi/job_id/*"| Artifacts
    ADTOF -->|"write midi/job_id/*"| Artifacts
    Browser -->|"WSS ?token=ID token<br/>subscribe / heartbeat"| SocketApi
    SocketApi --> SocketAuth
    SocketApi --> Connections
    SocketApi --> Jobs
    Batch -. "job_updated" .-> SocketApi
    BasicPitch -. "job_updated" .-> SocketApi
    ADTOF -. "job_updated" .-> SocketApi
    Browser -->|"presigned GET URLs"| Artifacts
~~~

## Components

| Area | Resources and code | Responsibility |
| --- | --- | --- |
| Browser | **frontend-react**, **App.jsx**, StemSplitter components | Authenticates, creates jobs, uploads audio, hydrates job snapshots, reconnects WebSockets, polls pending/partial jobs, exposes the account-backed history modal, and renders/edits audio and MIDI. |
| Browser transport | **AudioMultiTrackPlayer.js**, **useTransportPlayhead.js**, **useTimelineViewport.js**, **useMidiSynth.js**, **useMidiManager.js** | Serializes audio/MIDI decode work, retains only current bounded `AudioBuffer`s and one editable MIDI graph per artifact, exposes an audio-clock transport ref for direct visual/MIDI consumers, renders only viewport-local timeline data, applies immediate gain/mute/solo, and aligns isolated MIDI output buses with the same start time. |
| Authentication | **IaC/auth.yaml**, Cognito User Pool | Provides email/password accounts and ID tokens. There is no Cognito Identity Pool and no browser AWS credentials. |
| Job API | **IaC/api.yaml**, **job_api.py** | Creates durable upload or linked-source jobs, invokes yt-dlp for a linked source, enforces ownership, renders stored artifact keys as fresh signed downloads, and permanently deletes terminal jobs at the owner's request. |
| Link ingestion | **IaC/ingestion.yaml**, **LambdaYtDlp.py** | Downloads a validated public media URL through the optional residential `PROXY_URL`, with Deno/EJS challenge solving and typed curl-cffi Chrome impersonation, converts it to WAV, and writes the job's existing input key in the private uploads bucket. The normal S3/EventBridge route then starts Batch. |
| Jobs store | **IaC/jobs.yaml**, CloudDSPJobs | Stores the job owner, requested mode, state, artifact keys, error, revision, and expiry. |
| Source storage | Foundation uploads bucket | Holds original user audio and publishes Object Created events to EventBridge. |
| Event routing | Processing EventBridge rule | Matches the uploads prefix and submits a GPU Batch job with source bucket/key overrides. |
| Stem processing | **BatchDemucs.py** in AWS Batch | Validates the job, runs Demucs, uploads stems, persists their state, and asynchronously dispatches MIDI extractors. |
| MIDI processing | **LambdaMIDIBasicPitch.py**, **LambdaMIDIADTOF.py** | Produces pitched MIDI for non-drum stems and drum MIDI for the drums stem. |
| Artifact storage | Foundation processed-audio bucket | Holds stems, MIDI, and BPM JSON. It remains private; the browser gets presigned GET URLs only. |
| Real-time delivery | **IaC/realtime.yaml**, WebSocket handlers | Authenticates connections, records subscriptions, accepts heartbeats, and posts compact update hints. |
| Connection registry | CloudDSPConnections | Stores current connection IDs and subscribed jobs. It is TTL-backed and is never the authority for results. |
| Registry and network | ECR, **IaC/network.yaml** | Hosts deployable images and provides private Batch networking, an S3 gateway endpoint, and NAT egress. Basic Pitch, ADTOF, Demucs, and yt-dlp are wired into the root stack. Madmom remains a prototype repository/source. |

## Identity and authorization

### Cognito User Pool

The auth component creates a Cognito User Pool and a public browser app client.
The browser uses **amazon-cognito-identity-js** through
**src/auth/cognito.js**. **AuthPanel.jsx** provides a dismissible sign-in /
sign-up dialog on the processing root route, then implements registration,
confirmation, sign-in, and sign-out. A successful unconfirmed signup stores
only its email and chosen display name in local storage. The user can close the
dialog and continue navigating, then reopen it in verification-only mode after
the email arrives. Passwords and confirmation codes are never persisted.

The public app client ID may be placed in Vite environment configuration. It
is an application identifier, not a secret. No Identity Pool exists, so a
browser cannot receive AWS IAM credentials. Browser S3 access is limited to
individual presigned URLs.

The current template uses verified email/password login with an 8-character
minimum and at least one special symbol, 60-minute ID/access tokens, and a
seven-day refresh token. During registration the browser writes the user's
chosen `preferred_username` standard attribute and renders it after sign-in.
It is a user-facing profile label only: the API and WebSocket authorization
continue to identify the account exclusively by Cognito's immutable `sub`.
Cognito uses the Lite tier by default.

### Job HTTP API

The HTTP API has a native API Gateway JWT authorizer. The browser sends a
Cognito **ID token** in its Authorization header:

~~~http
Authorization: Bearer <Cognito ID token>
~~~

API Gateway validates token issuer and audience before calling **job_api.py**.
The handler reads **sub** from the authorizer context and never trusts a user
ID from a request body. A job that does not exist, or belongs to another user,
returns 404 so the API does not reveal job ownership.

### WebSocket API

A browser WebSocket opening handshake cannot add an arbitrary Authorization
header. The frontend appends the short-lived Cognito ID token as the **token**
query parameter on the WSS endpoint. The custom Lambda authorizer validates:

- Cognito's signing key and RS256 signature
- issuer and browser-client audience
- expiration and required claims
- **token_use** equals **id**

It returns the Cognito subject as API Gateway's **principalId**. The authorizer
and client must not log the token. WSS encrypts the connection, but a query
token is still sensitive and must not be placed in analytics URLs or error
messages. API Gateway does not support authorizer-result caching for WebSocket
APIs, so the token is validated when each connection is established.

## Durable data model

### CloudDSPJobs

The Jobs table uses **job_id** as its partition key. It has the
**user_id-updated_at-index** global secondary index for the saved-jobs library.
`GET /jobs` queries that index with the authenticated Cognito subject and
orders results by most recently updated; it never accepts a caller-provided
user ID. It is on-demand, encrypted, protected by point-in-time recovery, and
uses **expires_at** for TTL cleanup.

An illustrative item:

~~~json
{
  "job_id": "0f0bb9f6-4c98-4d38-8fea-b7d7f700c5f7",
  "user_id": "cognito-subject",
  "status": "midi_processing",
  "input_bucket": "clouddsp-uploads-account-region",
  "input_key": "uploads/0f0bb9f6-4c98-4d38-8fea-b7d7f700c5f7/song.wav",
  "source_filename": "song.wav",
  "source_content_type": "audio/wav",
  "stem_mode": "6-stems",
  "stems": {
    "drums": {
      "status": "ready",
      "s3_key": "stems/0f0bb9f6-4c98-4d38-8fea-b7d7f700c5f7/drums.wav"
    }
  },
  "midi": {
    "drums": {
      "status": "ready",
      "extractor": "adtof",
      "s3_key": "midi/0f0bb9f6-4c98-4d38-8fea-b7d7f700c5f7/drums.mid",
      "bpm_key": "midi/0f0bb9f6-4c98-4d38-8fea-b7d7f700c5f7/drums_bpm.json",
      "tempo_candidate": {
        "bpm": 128.0,
        "beat_count": 92,
        "drum_event_count": 240,
        "interval_consistency": 0.94,
        "credible": true,
        "source": "adtof_drums"
      }
    }
  },
  "tempo": {
    "bpm": 128.0,
    "source": "adtof_drums",
    "confidence": "high",
    "candidate_count": 1,
    "contributing_stems": ["drums"]
  },
  "revision": 8,
  "created_at": "2026-08-05T12:00:00Z",
  "updated_at": "2026-08-05T12:04:20Z",
  "expires_at": 1780000000
}
~~~

The Job API defaults the item expiry to seven days after creation. DynamoDB TTL
deletion is asynchronous, so it is cleanup rather than an exact retention
clock. The foundation stack currently expires noncurrent S3 versions, but not
the current source/artifact version. Align current-object retention with the
job TTL and privacy policy before production data is stored long term.

Before TTL cleanup, the history modal exposes a destructive **Delete job**
action only for `completed` and `failed` records. `DELETE /jobs/{job_id}`
authenticates the owner, removes every S3 object version and delete marker
under `uploads/{job_id}/`, `stems/{job_id}/`, and `midi/{job_id}/`, then
conditionally removes the DynamoDB item. S3 cleanup occurs before the table
delete so a failed storage operation leaves the durable record available for a
retry. This operation is irreversible and deliberately rejects in-progress
states, preventing a Batch or MIDI worker from racing a deletion.

| Status | Meaning |
| --- | --- |
| **source_ingestion** | A linked-source job exists and yt-dlp is downloading/converting its source before it writes the uploads key. |
| **upload_pending** | Job exists; source upload has not yet reached Batch. |
| **stem_processing** | Demucs has validated the job and is processing it. |
| **midi_processing** | Stems are durable and at least one MIDI extractor is queued or processing. |
| **completed** | All MIDI states are ready. |
| **failed** | A source-ingestion, Batch, or terminal MIDI failure. A yt-dlp failure before S3 upload has no original audio artifact. |

Every durable mutation increments **revision**. It is a monotonic version hint,
not a sequence that must be received without gaps. Clients may fetch the same
or a later revision multiple times safely.

### CloudDSPConnections

The Connections table uses **connection_id** as its partition key and
**user_id-last_seen_at-index** to find active connections for a user. A row
contains the owner, subscribed **job_ids**, connection and heartbeat times, and
**expires_at**. Its nominal TTL is 12 hours.

The disconnect route removes a row immediately. Workers tolerate stale records:
a GoneException during notification is logged and DynamoDB TTL eventually
cleans the record. This table must never contain permanent result state or act
as proof that a job belongs to a user.

## Browser-facing contracts

### Create job

**POST /jobs** requires the ID token. The browser sends:

~~~json
{
  "filename": "song.wav",
  "stem_mode": "6-stems",
  "content_type": "audio/wav",
  "size_bytes": 5242880
}
~~~

Allowed stem modes are **2-stems**, **4-stems**, and **6-stems**; the default
is **6-stems**. The response is 201 and includes:

~~~json
{
  "job_id": "uuid",
  "status": "upload_pending",
  "revision": 1,
  "input_key": "uploads/uuid/song.wav",
  "upload_url": "https://bucket.s3.region.amazonaws.com/",
  "upload_fields": {
    "Content-Type": "audio/wav",
    "x-amz-meta-job-id": "uuid",
    "x-amz-meta-stem-mode": "6-stems"
  },
  "max_source_bytes": 268435456
}
~~~

The frontend sends a multipart **POST** to **upload_url** with every returned
`upload_fields` value and the file field. Those fields are part of the signed
policy. It uses S3's `content-length-range` condition to enforce the 256 MiB
source cap even if a caller bypasses the browser validation. The client also
checks `File.size` before it creates a job, while Batch checks the actual S3
`ContentLength` again before it downloads the object.

Direct uploads support WAV, MP3, FLAC, M4A, AAC, OGG, Opus, AIFF, and WebM.
The file picker and Job API enforce this extension/MIME allowlist; the Batch
container performs the final FFprobe audio-stream check because a client can
still mislabel file bytes. The maximum decoded duration is 500 seconds.

### Create linked-source job

**POST /jobs/link** requires the same Cognito ID token. The browser link modal
uses this contract directly:

~~~json
{
  "source_url": "https://media.example/song",
  "stem_mode": "6-stems"
}
~~~

The Job API validates an absolute HTTP(S) URL and rejects embedded credentials,
localhost, and literal private/reserved IPs. The ingestion Lambda repeats those
checks and rejects hostnames that resolve to private/reserved addresses before
yt-dlp contacts the host. It then creates a job in
**source_ingestion** status. It invokes the yt-dlp Lambda asynchronously and
returns 202 with the job ID and its fixed input key:

~~~json
{
  "job_id": "uuid",
  "status": "source_ingestion",
  "revision": 1,
  "input_key": "uploads/uuid/linked-audio.wav"
}
~~~

The URL is intentionally not stored in DynamoDB or returned from the job
snapshot. The job API is the only CloudDSP role allowed to invoke ingestion.
yt-dlp performs a metadata-only request before download and requires a known
duration no greater than 500 seconds. It rejects known/estimated encoded sizes
over 256 MiB, caps unknown-size transfers with a progress hook, then normalizes
the result to 44.1 kHz stereo 16-bit WAV and rejects an output over 128 MiB.
It writes the resulting `audio/wav` at the returned key with `job-id`,
`stem-mode`, `source-type=yt-dlp`, and size metadata. That S3 write matches the
existing EventBridge rule, so it starts the same Demucs Batch worker as a
normal presigned upload. There is no parallel direct-Batch route.

The Job API generates `original_url` only when source audio is known to exist.
After yt-dlp's S3 upload, it records `source_uploaded=true` as it advances the
job to `upload_pending`; if Batch advances first, yt-dlp records that flag
without overwriting Batch's newer status. A linked-source job that fails before
upload deliberately has no `original_url`, preventing a browser request for its
predetermined-but-missing private S3 key. From `upload_pending` onward, the
browser downloads and decodes the original track while it continues to wait for
Batch stem and MIDI updates.

### Read job snapshot

**GET /jobs/{job_id}** returns only an authorized job. It removes internal
source bucket/key and TTL fields. It returns **original_url** for an available
original upload, but deliberately omits it for a yt-dlp job that failed before
uploading source audio. Each persisted **s3_key** becomes a fresh presigned
**url**, and each **bpm_key** becomes a fresh **bpm_url**. Download URLs are
valid for one hour by default. The top-level **tempo** object is the
backend-selected master tempo; it is returned directly and never requires a
client to download or vote on BPM artifacts.

The frontend retains job IDs, not presigned URLs. It requests a new snapshot
after a reload, a notification, polling, or a download-link expiry.

### List saved jobs

**GET /jobs** returns a compact `jobs` list for the current authenticated user,
newest first. A list item has `job_id`, `source_filename`, `status`,
`stem_mode`, `created_at`, `updated_at`, and the current top-level `tempo`.
It intentionally contains no S3 keys or URLs. When a user selects an item,
the browser calls **GET /jobs/{job_id}** to receive the original track, stems,
MIDI artifacts, and master BPM in one owner-checked snapshot.

### Delete saved job

**DELETE /jobs/{job_id}** is available only to the authenticated job owner and
only when the job status is `completed` or `failed`. It returns the deleted job
ID and a count of removed S3 object versions. A missing job and a job owned by
another account both return 404; a nonterminal job returns 409. The browser
requires an explicit confirmation and removes a successful deletion from the
open history list and current workspace immediately.

### WebSocket messages

After WSS connects, the client sends one subscription per active job:

~~~json
{ "action": "subscribe", "job_id": "uuid" }
~~~

The handler checks ownership, stores the subscription, and replies:

~~~json
{ "type": "job_updated", "job_id": "uuid", "revision": 8 }
~~~

The client also sends **{ "action": "heartbeat" }**. The handler refreshes the
connection expiry and replies with **heartbeat_ack**. An invalid route or
subscription returns an **error** message.

Workers send the same compact **job_updated** message. They never send a MIDI
or stem URL over a socket. The client must respond by reading the HTTP snapshot.

## Processing sequence

~~~mermaid
sequenceDiagram
    participant UI as Browser
    participant API as Job API
    participant Jobs as DynamoDB Jobs
    participant S3 as S3
    participant EB as EventBridge
    participant Batch as Demucs Batch
    participant MIDI as MIDI Lambdas
    participant WS as WebSocket API

    UI->>API: POST /jobs with Cognito ID token
    API->>Jobs: create upload_pending item
    API-->>UI: job_id, key, signed PUT URL and headers
    UI->>S3: PUT uploads/job_id/file with signed metadata
    S3->>EB: Object Created
    EB->>Batch: INPUT_BUCKET and FILE_KEY overrides
    Batch->>Jobs: validate item; set stem_processing
    Batch->>S3: write stems/job_id/stem.wav
    Batch->>Jobs: persist stems; initialize MIDI states
    Batch->>MIDI: async invoke one extractor per stem
    MIDI->>Jobs: mark item processing
    MIDI->>S3: write midi/job_id/stem.mid and BPM JSON
    MIDI->>Jobs: persist ready or failed artifact state
    Batch-->>WS: job_updated after durable mutation
    MIDI-->>WS: job_updated after durable mutation
    WS-->>UI: job_updated(job_id, revision)
    UI->>API: GET /jobs/job_id
    API-->>UI: current snapshot with fresh URLs
~~~

The steps in detail:

1. The user signs in. **App.jsx** loads the authenticated account's compact
   saved-job library. It does not restore an active-job queue from browser
   storage; a user explicitly opens a previous job from the history modal.
2. For a local file, the client calls **POST /jobs**, records the returned job
   ID locally, and submits the file through the returned constrained S3 POST
   form to exactly **uploads/{job_id}/{filename}**. For a
   linked source, it calls **POST /jobs/link**; the Job API asynchronously
   invokes yt-dlp, which writes `uploads/{job_id}/linked-audio.wav` itself.
3. The S3 object metadata carries **job-id** and **stem-mode**. It provides an
   integrity/transition check, but DynamoDB remains authoritative.
4. Both source paths create one S3 Object Created event. The rule filters on the uploads
   bucket and the **uploads/** prefix. EventBridge sees bucket/key event
   fields; it cannot inspect object metadata.
5. EventBridge submits the Batch job. Its transformer sets **INPUT_BUCKET** and
   **FILE_KEY** dynamically. The job definition provides output bucket and a
   fallback stem mode.
6. **BatchDemucs.py** derives the job ID from the source key, consistently
   reads the job, validates the stored source bucket/key, reads S3 metadata,
   and uses the durable stem mode if metadata disagrees.
7. Demucs runs **htdemucs** for 2/4 stems or **htdemucs_6s** for six stems. The
   worker uploads output to **stems/{job_id}/{stem}.wav**, with job/stem
   metadata.
8. One DynamoDB update records all ready stems, initializes every MIDI entry as
   queued, and changes the overall status to **midi_processing**.
9. For each actual output stem, the Batch task asynchronously invokes ADTOF
   when the stem name is **drums**, otherwise Basic Pitch. The payload contains
   job ID, processed bucket, stem key/name, and extractor. A dispatch failure
   is persisted on that individual MIDI state.
10. A MIDI Lambda confirms the stem key matches the durable job record, records
    **processing**, and uses a job-and-stem-scoped temporary directory.
11. Basic Pitch produces melodic MIDI and records a Librosa tempo candidate.
    ADTOF runs its CPU drum model with configurable FPS/thresholds, counts its
    predicted drum events, and records a drum tempo candidate only when both
    drum-event and beat-tracking evidence are sufficient. Both write MIDI plus
    a BPM JSON artifact; failed tempo analysis never discards successful MIDI.
12. Each Lambda writes its artifacts under **midi/{job_id}/**, persists the
    result as ready or failed, and recomputes the backend-owned top-level
    **tempo** field. A credible ADTOF drum candidate wins; otherwise credible
    non-vocal candidates are normalized for clear half/double-time values,
    clustered within 3 BPM (or 3%), and resolved using a weighted median. If
    no credible candidate exists, the durable fallback is 120 BPM with
    **confidence=unknown**. The worker then derives final job status when all
    MIDI work is terminal and emits a best-effort update.

## Frontend behavior

**App.jsx** owns CloudDSP processing orchestration. It supplies snapshots to
the DAW components rather than letting UI components call legacy socket
endpoints directly. The current tab holds at most one active workspace job;
the account-backed `GET /jobs` library is the durable history mechanism.

- Configuration is supplied through **VITE_COGNITO_USER_POOL_ID**,
  **VITE_COGNITO_BROWSER_CLIENT_ID**, **VITE_JOB_API_URL**, and
  **VITE_WEBSOCKET_URL**. **frontend-react/.env.example** lists the variables.
  Vite embeds all VITE values in the browser bundle, so they must not contain
  secrets.
- The client subscribes to the currently open job after socket open and every
  reconnect. Reconnection uses capped exponential backoff. It sends a
  heartbeat every two minutes, but heartbeats are never the result transport.
- A pending or incomplete job is polled every five seconds in addition to
  WebSocket delivery. 429 and 5xx errors are retried with a per-job exponential
  backoff. Polling makes correct recovery independent of socket uptime.
- Ready stems appear as their URLs arrive. MIDI canvas rows and the popup
  editor independently show `processing`, `loading`, `ready`, or `failed`, so
  one delayed MIDI file does not block the rest of the workspace. Restoring a
  historical job uses the neutral message “Stems and MIDI will arrive shortly”
  rather than claiming a new Batch job is running.
- A normal snapshot preserves a still-usable prior presigned URL for an
  immutable artifact. This avoids cancelling an in-progress browser download
  on each poll; the client accepts the new URL before the old one is within one
  minute of expiry. Opening a history item explicitly replaces URLs with a
  fresh snapshot.
- The browser compares artifact URLs by stable S3 host/path, not the expiring
  presigned query string. Polling a job therefore cannot re-download/reparse an
  unchanged MIDI object. The loader keeps one editable Tone.js graph and a
  compact immutable MIDI byte snapshot for Revert/Undo rather than two parsed
  graphs. High-frequency playhead work is never driven by React state: React
  receives a one-Hz text-readout position, while direct DOM transforms follow
  the shared audio clock.

### Browser audio and MIDI transport

The source file is shown immediately after selection and can be decoded for
local playback while processing is pending. Every original/stem that is
currently displayed is then fetched from its presigned URL, one complete
fetch-and-decode operation at a time. This bounds the period where both encoded
bytes and decoded PCM exist. It is decoded with `decodeAudioData` and retained
as an `AudioBuffer`. A stable cache key uses the S3 host/path rather than the
presigned query string, because signatures change every snapshot even though
the immutable object has not. Stale history-job work is aborted before decode;
raw encoded buffers are released after decode, and fetch bypasses the browser
HTTP memory cache because the decoded buffer is the intentional current-job
cache.

Play is enabled only after every currently displayed audio track is decoded.
`AudioMultiTrackPlayer` creates an `AudioBufferSourceNode` per track and
schedules all nodes at the same small future `AudioContext.currentTime`. This
is intentionally different from calling `play()` on multiple `<audio>`
elements: Safari and other browsers may resolve those independent media
requests at different times after a pause, resume, or buffering event.

Each source runs through a dedicated `GainNode`. Mute, solo, and MIDI-mode
audio replacement change that gain immediately (with a short click-free ramp),
without waiting for a React effect to set an HTML media property. The Original
track is muted by default once separated stems are available. Pause, seek,
loop, and tempo changes rebuild or update the shared transport rather than
allowing individual tracks to drift. MIDI synthesis receives the same scheduled
transport start time, so its first notes align with the decoded audio.

The transport publishes `{ position, offset, startTime, rate, isPlaying,
revision }` through a ref every animation frame. React commits a whole-second
display position at one Hz only. `useTransportPlayhead` reads that ref, moves
the line and ruler triangle with `translate3d`, and follows the viewport
deterministically after the line reaches its centre. It never treats delayed
rendering as a manual seek. Automatic scrolling advances in two-pixel steps;
the line still moves smoothly every frame. Static `TrackGrid`, ruler marks, and
the popup note layer receive a tile-quantized viewport range, time-sort their
notes, and binary-search to mount only nearby notes/bars with overscan. Normal
notes avoid per-note shadows. The popup uses the same direct playhead logic,
does not animate the hidden workspace below it, and is unmounted while closed.
While the modal is open, its own piano roll is the only mounted note surface;
the occluded workspace grid is not updated for an editor drag.

The main workspace timeline is conditionally mounted only once a job has
artifacts to display. `useTimelineViewport` therefore exposes a callback ref
that records the mounted scroll element in state and installs its scroll/resize
observers at that point; a later mutation of `ref.current` alone would leave
the range stuck on its initial tile. The same callback-ref contract is used by
the popup timeline, while the original mutable ref remains available for
imperative transport scrolling.

`useMidiSynth` is an audio-clock look-ahead scheduler, not a playhead render
subscriber. It indexes sorted notes when MIDI data changes, then wakes every
40 ms to schedule only notes in the next 250 ms. A transport revision resets
the cursor by binary search for play, pause, seek, cycle restart, and tempo
changes. This preserves deterministic MIDI timing without scanning every note
on every browser frame.

### Mixing and output buses

CloudDSP has one shared `AudioContext` and one shared transport clock, but it
does **not** mix every sound through one shared track fader. “Separate buses”
means separate Web Audio output paths that finally converge at
`AudioContext.destination`:

~~~text
Decoded source/stem: AudioBufferSourceNode -> track GainNode -> destination
Generated melodic MIDI: smplr instrument -> its OutputChannel -> destination
Generated drum MIDI: one sampler per drum voice -> that voice OutputChannel -> destination
~~~

The track dB control is deliberately applied to both representations of a
track. For a normal stem, `AudioMultiTrackPlayer` converts the value to linear
gain and sets that stem's `GainNode`. `useMidiSynth` applies the same dB value
to the corresponding smplr `OutputChannel`. Therefore switching a track to
MIDI mode replaces the audible audio stem with a synthesised version at the
same console gain; the audio source itself is ramped to zero while MIDI mode is
enabled. The buses remain sample-synchronised because both are scheduled on the
same `AudioContext`, not because they share an audio node.

Every melodic MIDI track owns its own synth output. ADTOF drum MIDI is more
granular: Kick, Snare, Tom, Hi-hats, and Cymbal each own a one-sample sampler
and output bus. The parent **Drums** fader applies to the drum-audio stem and
to all five MIDI outputs. A child drum fader applies only to that child MIDI
output; its effective output level is the parent dB value plus the child dB
value. Child rows have no corresponding audio stem, so their faders never
alter the mixed `drums.wav` audio.

The MIDI buses use output gain rather than altering MIDI note velocity. This
allows positive gain to remain effective for notes already at MIDI velocity
127, while retaining the original MIDI velocities for the piano roll and MIDI
export.

This choice trades browser memory for reliability: a long job keeps decoded
PCM for every displayed track. `audioMemoryMetrics` exposes decoded bytes,
temporary bytes, peak decoded bytes, and current track count for Safari
diagnostics. Full PCM has a linear cost of `seconds × sample rate × channels ×
4 bytes`; one original plus six four-minute 44.1 kHz stereo tracks is about
565 MiB before native decoder overhead. The UI does not repeatedly download an
artifact merely because a fresh URL arrived. If a source fails to fetch, check
the processed-bucket CORS rule for the exact frontend origin and the browser
network request before changing transport code.

MIDI parsing does not allocate a sampled instrument. `useMidiManager` uses a
persistent serial queue for MIDI fetch/parse and instrument construction, so a
history snapshot or central MIDI toggle cannot begin several large decodes at
once. It creates an instrument only when MIDI mode is enabled or the
corresponding editor opens; piano loading is constrained to pitches present in
the track and guitar/bass use the lower-footprint FluidR3 soundfont kit.
Disabling MIDI disposes its smplr graph/sample ownership, and a job change
disposes every old instrument. This is mandatory: `stop()` alone is
insufficient to release sampled-instrument resources.

### ADTOF drum representation

ADTOF writes one drums MIDI file, but its five fixed General MIDI pitches are
separate musical classes. `DrumMidi.js` is the canonical mapping used for
parsing, display, editing, and playback:

| MIDI pitch | Lane | Browser sampler voice | Playback adjustment |
| --- | --- | --- | --- |
| 35 | Kick | `kick` | 1.35 velocity scale |
| 38 | Snare | `snare` | 1.18 velocity scale |
| 47 | Tom | `mid-tom` | 0.70 velocity scale |
| 42 | Hi-hats | `hihat-close` | 0.55 velocity scale |
| 49 | Cymbal | `cymbal` | default |

The main Drums row is collapsed by default. Expanding it exposes MIDI-only
subtracks with independent mute, solo, and ±12 dB gain controls; these do not
mute or otherwise alter the parent drum-audio stem. The popup drum editor
exposes the same per-voice mute/solo controls and uses the compact five-sample
808 kit rather than a piano soundfont.

Generated melodic MIDI uses a separate playback-only balance: guitar is 2.00x
and piano is 0.60x. These scales affect both transport playback and popup-note
auditioning, but never change the original stem audio or exported MIDI data.

The frontend is a Vite single-page application. It does not use server-side
rendering or React Server Components.

## Storage and compute

### S3 and artifact naming

| Bucket | Key format | Writer and reader |
| --- | --- | --- |
| uploads | **uploads/{job_id}/{filename}** | Browser writes with a size-constrained presigned POST; Batch reads. |
| uploads | **uploads/{job_id}/linked-audio.wav** | yt-dlp writes after a Job API invocation; Batch reads through the same event rule. |
| processed audio | **stems/{job_id}/{stem}.wav** | Batch writes; browser reads through signed GET. |
| processed audio | **midi/{job_id}/{stem}.mid** and **midi/{job_id}/{stem}_bpm.json** | MIDI Lambdas write; browser reads through signed GET. |

Both buckets are private, AES-256 encrypted, versioned, bucket-owner-enforced,
and protected with public-access blocks. CORS permits configured frontend
origins. Stack deletion/replacement retains these buckets. Noncurrent versions
expire after 30 days and incomplete multipart uploads abort after seven days.

### Batch GPU environment

The network stack creates a VPC with two private Batch subnets in separate
Availability Zones, an egress-only security group, an S3 Gateway endpoint, and
one NAT Gateway. The endpoint keeps S3 traffic off NAT; NAT still provides ECR
image pulls, CloudWatch Logs, Lambda invocation, and other HTTPS egress.

The processing stack defines a managed EC2 GPU compute environment:

- Default instance type: **g4dn.xlarge**.
- Minimum and desired capacity: zero vCPUs. This reduces idle costs but
  introduces instance-provisioning and image-pull cold starts.
- Maximum capacity: eight vCPUs by default.
- Demucs task: one GPU, four vCPUs, 14,336 MiB memory, and one-hour timeout.
- Batch job retry strategy: one attempt.

The compute environment omits a custom service role. For a managed
environment, AWS Batch uses or creates its **AWSServiceRoleForBatch**
service-linked role, which is the supported role for capacity management and
future infrastructure updates.

The single NAT Gateway is a cost optimization and a single-AZ dependency.
Evaluate per-AZ NAT gateways and/or AWS interface endpoints for a
high-availability production environment.

### MIDI Lambda images and ECR

Basic Pitch and ADTOF are image-package Lambda functions using x86_64. Their
images must be built for **linux/amd64**. Both currently use the account's
3,008 MiB Lambda-memory limit and 1,024 MiB ephemeral storage. Basic Pitch
defaults to a 600-second timeout; ADTOF uses 300 seconds. Lambda memory also
provides CPU allocation, so benchmark with representative stem duration before
tuning cost/performance.

The images use job-specific directories beneath /tmp and remove them before or
after inference, avoiding warm-Lambda output collisions. ECR repositories scan
on push, remove untagged images after seven days, and retain the newest ten
images.

### Linked-source Lambda image

yt-dlp is a separate x86_64 Lambda image, also built for **linux/amd64**. It
uses 3,008 MiB memory, a 900-second timeout, and 4,096 MiB `/tmp` storage so
the downloaded media and WAV conversion can coexist. It is intentionally not
attached to the private Batch VPC: source retrieval needs public internet
egress, while Batch's private VPC remains scoped to processing. It enforces an
eight-minute media-duration limit by default and cleans its
`/tmp/clouddsp-ytdlp/{job_id}` directory after every invocation.

The image uses the Python 3.12 Amazon Linux 2023 Lambda base because current
Deno requires a newer glibc than the Python 3.11 Amazon Linux 2 base. The
handler pins yt-dlp and supplies Deno, `yt-dlp-ejs`, and `curl-cffi`. The
`YTDLP_IMPERSONATE` operator setting is parsed into yt-dlp's Python
`ImpersonateTarget` type before constructing `YoutubeDL`; passing the CLI
string directly is invalid in the embedded API. HTTP 403 from a media host is a
host/proxy/authentication issue, not an S3 or Batch failure.

An existing ECR image must be rebuilt and pushed after any handler or Docker
change. CloudFormation only pulls an image; it does not rebuild source code.

## Infrastructure composition and deployment

The root template **IaC/cloud-dsp.yaml** composes the components below and
passes stack outputs rather than embedding bucket names, ARNs, API IDs, or
regions in code.

~~~mermaid
flowchart TD
    Foundation["foundation<br/>S3 + ECR"]
    Jobs["jobs<br/>DynamoDB Jobs"]
    Auth["auth<br/>Cognito"]
    Network["network<br/>VPC + NAT + S3 endpoint"]
    Realtime["realtime<br/>WebSocket + Connections"]
    Midi["midi-lambdas<br/>Basic Pitch + ADTOF"]
    Ingestion["ingestion<br/>yt-dlp Lambda"]
    Processing["processing<br/>Batch + EventBridge"]
    Api["api<br/>HTTP Job API"]

    Jobs --> Realtime
    Auth --> Realtime
    Foundation --> Midi
    Jobs --> Midi
    Realtime --> Midi
    Foundation --> Ingestion
    Jobs --> Ingestion
    Realtime --> Ingestion
    Foundation --> Processing
    Jobs --> Processing
    Realtime --> Processing
    Network --> Processing
    Midi --> Processing
    Foundation --> Api
    Jobs --> Api
    Auth --> Api
    Ingestion --> Api
~~~

The root stack expects two deployment locations outside these components:

1. A template bucket containing nested CloudFormation YAML under a configurable
   prefix.
2. An artifact bucket containing zip packages for the Job API, WebSocket
   handler, and WebSocket authorizer.

Zip packages must contain local dependencies. Package **cloud_job_workflow.py**
with any handler that imports it. Package **PyJWT[crypto]** with the WebSocket
authorizer for Lambda ARM64 Linux; local macOS packages are not suitable for
that runtime. Build/push Demucs, Basic Pitch, and ADTOF images before updating
the stacks that reference their tags or immutable digests.

### First deployment bootstrap

Container images introduce a deliberate two-phase deployment. An ECR repository
must exist before an image can be pushed, while Lambda and Batch cannot be
created from an image tag that does not exist yet.

1. Upload the nested templates and ZIP Lambda artifacts to the template and
   artifact buckets. `TemplatePrefix` must be empty when YAML files are at the
   template bucket root, or include the trailing slash when they are under a
   folder. For a Job API code change, upload the ZIP under a new key and update
   the root stack's `JobApiCodeS3Key`; overwriting an object at the same key is
   not a reliable Lambda deployment signal.
2. Create the root stack with **DeployProcessingWorkers=false**. This creates
   Foundation (including ECR), Jobs, Auth, Network, Realtime, and the Job API;
   it intentionally skips the image-based MIDI Lambdas and Batch processing
   stack.
3. Build and push the Basic Pitch, ADTOF, Demucs, and yt-dlp images to the
   Foundation ECR repositories with the desired tags.
4. Update the same root stack with **DeployProcessingWorkers=true** and those
   image tags. CloudFormation then creates the MIDI Lambda and Batch
   processing nested stacks.

The root outputs for the Basic Pitch and ADTOF Lambda ARNs and Demucs queue are
absent during the bootstrap phase and appear after worker processing is
enabled. This avoids a circular requirement without manually creating CloudDSP
ECR repositories in the console.

Use **ProjectName** (default **clouddsp**) and **EnvironmentName** (default
**dev**) consistently. IAM role and inline policy names include both. S3/ECR
resource names must meet lowercase naming rules. Configure deployed frontend
origins in **AllowedFrontendOrigins** before release.

## Security controls

- Job ownership is enforced at both HTTP read and WebSocket subscription time.
- The authenticated Job API alone invokes yt-dlp. It creates the job before
  download; the ingestion Lambda validates that record and can write only the
  `uploads/*` key scoped to it. A linked URL is not persisted in the job item.
- Browser access is limited to API requests authenticated by Cognito and
  presigned, narrow S3 object transfers.
- S3 object permissions are scoped to required buckets/prefixes; workers do not
  receive unrestricted S3 access.
- Batch only invokes the named MIDI Lambda ARNs. Lambda/Batch notification
  permissions are scoped to the configured WebSocket API/stage.
- S3 and DynamoDB data stores are encrypted; browser object access uses
  short-lived URLs, and component roles are scoped with least-privilege IAM.
- Never hardcode AWS credentials, secret keys, endpoint-specific credentials,
  or tokens in source or a Vite environment file.

## Failure and recovery behavior

| Condition | Current behavior | Expected result |
| --- | --- | --- |
| Browser reload or socket disconnect | Reconnect/resubscribe if a job is open in the current tab. After a full reload, load the authenticated saved-job library and explicitly reopen a job. | Existing results remain available without rerunning DSP. |
| Missed or duplicate update | Update is only a snapshot-fetch hint. | Duplicate/out-of-order WebSocket messages are harmless. |
| Expired artifact URL | API generates a new URL from stored key; the frontend replaces cached URLs before their safety window expires. | Refresh the job snapshot without reprocessing audio. |
| Stem/audio fetch stalls | Browser transport waits for all currently displayed tracks to decode and logs the failing track. | Inspect the exact S3 request and confirm the bucket CORS origin, GET/HEAD methods, and URL validity. |
| Worker exception caught in code | Batch/Lambda persists job or MIDI failure before notifying. | UI can display a job/stem error. |
| Linked-source ingestion failure | yt-dlp marks only a still-`source_ingestion` job failed before notifying; an asynchronous retry of that terminal job returns `skipped`. | A late retry cannot overwrite a newer Batch state or produce repeated Lambda errors. |
| EventBridge cannot submit Batch | Retries for up to one hour, at most 24 attempts, then writes to processing SQS DLQ. | Requires operational inspection/redrive. |
| Stale socket | Disconnect, GoneException, or TTL cleanup removes its record. | Durable state is unaffected. |

## Operational limitations and next hardening

The durable job architecture fixes the earlier connection-ID design: results no
longer depend on the particular browser connection that existed at upload time.
Some production hardening remains:

1. **Duplicate Batch execution:** current workers tolerate duplicate delivery at
   the snapshot/UI level, but Demucs does not yet obtain a conditional
   processing lease before running. An at-least-once S3/EventBridge delivery can
   start duplicate GPU work for the same job. Add a conditional
   upload_pending-to-stem_processing claim or a lease token before scaled
   production use.
2. **Monitoring:** add CloudWatch alarms and a runbook for queue backlog,
   Batch failures, Lambda errors/throttles, EventBridge DLQ messages, and jobs
   stuck in non-terminal status. A Batch task that never starts cannot update
   DynamoDB from inside its Python code.
3. **Retention/privacy:** define current-object S3 lifecycle expiry and
   deletion/erasure behavior consistent with seven-day job TTL and product
   privacy requirements.
4. **Deployment verification:** package zip dependencies and images in CI, then
   test real Cognito authentication, CORS, upload, Batch, MIDI, reconnect, and
   URL-expiry paths in a deployed environment.
5. **History retention:** users can permanently delete completed/failed jobs
   and their job-scoped artifacts. Define archival and automatic current-object
   retention before treating the seven-day TTL as a customer-facing guarantee.
6. **Browser memory:** decoded `AudioBuffer` transport avoids cross-browser
   drift but grows with stem count and track duration. Monitor the built-in
   decoded-buffer metrics separately from Safari process memory, sampled
   instruments, and DOM nodes. Benchmark realistic multi-minute projects and
   move to a streaming/chunked or server-generated preview-stem transport if
   long-form audio becomes a product requirement.
7. **Cost and availability:** benchmark Lambda memory settings and real audio
   duration, tune Batch capacity, and revisit the single-NAT design before a
   highly available production launch.

## Operations checklist

- Correlate worker and API logs by **job_id**, not connection ID.
- Inspect the EventBridge-to-Batch DLQ when a job remains upload_pending.
- Inspect Batch logs when a job remains stem_processing; inspect MIDI Lambda
  logs and job MIDI entries when one stem fails.
- Regenerate URLs by reading a job snapshot rather than creating permanent URL
  storage.
- Treat ECR scan findings and frontend dependency audits as deliberate,
  tested maintenance work; do not use unreviewed bulk upgrade commands.
