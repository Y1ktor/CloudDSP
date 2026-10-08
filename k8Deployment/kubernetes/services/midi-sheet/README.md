# MIDI to Sheet (local Kubernetes)

## Workflow and contract

Browse MIDI reads `.mid`/`.midi` locally into the shared piano roll. It makes
no job-creation or MinIO request. Queue sheet invokes the editor export, clones
all edited tracks and controller events, writes the selected BPM and meter,
then submits that exact MIDI snapshot. Tempo changes preserve musical tick
positions. Loop, mute, solo, and viewport settings do not trim the export.

Authenticated `POST /sheet-jobs` accepts `direction: midi_to_sheet`, basename,
MIDI content type, and size (1 byte–10 MiB). Keycloak supplies the immutable
owner. PostgreSQL `midi_sheet_jobs` owns UUID, state, 14-day retention, revision,
lease, and stable private keys. The API returns a size-constrained POST form;
the browser uploads to `clouddsp-uploads/midi-sheet-inputs/{uuid}/source.mid`.
Both accepted filename suffixes use this canonical object suffix.

MinIO MIDISHEET's persistent confirmed AMQP target routes `midi.sheet.upload.created`
to `clouddsp.midi-sheet-intake` in `/clouddsp`. This queue has a 30-second retry
queue and DLQ using existing exchanges. The independent CPU renderer verifies
the source against its durable row and metadata, claims a 180-second lease,
and runs MuseScore 4.7.5 with a 120-second deadline. MIDI limits: type 0/1,
metrical timing, 30 minutes, 64 tracks, 100,000 events, 20,000 notes.

The official ARM64 AppImage is SHA-256 verified during build. Debian Trixie
provides its required glibc; isolated Xvfb supplies the xcb display the Linux
binary selects. There is no desktop, GPU, runtime internet, Kubernetes token,
or runtime administrator credential. The container root is read-only;
network access is restricted to DNS, PostgreSQL, MinIO, and RabbitMQ.

Outputs are private `midi-sheet-results/{uuid}/result.pdf` and `result.musicxml`.
The worker persists both before completing the row and acknowledging delivery.
A lease token guards updates; terminal duplicates skip processing, expired
leases can be reclaimed, and the durable attempt budget is three. Transient
errors require a confirmed retry publication before ACK. Exhausted deliveries
remain in the DLQ for operator recovery.

`GET /sheet-jobs` and `GET /sheet-jobs/{uuid}` enforce owner and expiry. Detail
signs fresh source/PDF/MusicXML URLs after owner verification. History follows
the active direction: MIDI-to-sheet, score-to-MIDI, or stem jobs. Opening a job
restores its submitted MIDI and result or resumes polling without requeueing.
Account changes discard files, history, pending requests, and editor resources.
Current browser edits are uploaded only on Queue; previous job results represent
the submitted version. PDF opens in the browser's native viewer; MusicXML is
downloadable. No CSP frame/object exceptions were added.

KEDA scales this Deployment from zero to two, including unacknowledged work.
Requests: 250m CPU/512 MiB; limits: 2 CPU/2 GiB. No Service or Ingress is needed.

## Delivery

Commit source and finite manifests first. Publish with
`scripts/images/build-midi-sheet-image.sh`, `build-job-api-image.sh`, and
`build-frontend-image.sh`; update their immutable image locks/Helm values.
Reconcile the MinIO chart's MIDISHEET target before notification bootstrap.

```sh
python3 kubernetes/scripts/stages/credentials/midi-sheet-bootstrap.py \
  --secrets-file /absolute/ignored/path/midi-sheet-credentials.json
```

This applies v012, queues, prefix IAM and restricted DB/AMQP/MinIO identities,
then removes temporary data-namespace credential copies. Existing audio/OMR
policies and bucket rules remain independently attached. Add `midi-sheet` to
the Flux cluster root after prerequisites succeed. Flux owns its Deployment,
NetworkPolicy and ScaledObject; finite state/bootstrap Jobs stay outside Flux.

Validate API/worker tests, both frontend profiles, Helm, API-server dry runs,
and `tests/midi-sheet-smoke` after rollout. The live smoke uses disposable
Keycloak identities and original eight-note MIDI. It checks upload, native
events, real engraving, private artifacts, authentication, owner/studio
isolation, retention, duplicate deliveries, stale leases, retries, and cleanup.

Local daily quotas and scheduled retention cleanup remain unimplemented,
consistent with the other local job routes. Expired rows are hidden in reads.
AWS deployment of this backend is separate work.
