# MIDI-to-sheet local rollout verification — 2026-10-08

## Delivered

Source commit `24fc3a7`; pinned images enabled in `79e2a99`. Follow-up commits
`ebe6150` and `961743f` add the chart to Flux's sparse source and the named
integration test to the existing broker management network allowlist.

- MuseScore Studio **4.7.5**, native Linux ARM64; official AppImage SHA-256
  `034f0257fd21ed6b7d4863714dbf8e97cc86d454beb79aaa845a5ade3ff99e36`.
- API `0.0.13-midi-sheet`, frontend `0.6.7-midi-sheet`, renderer `0.1.0`.
  Immutable references and build provenance are in `images.lock.yaml`.
- v012, three restricted worker identities, API source/result prefix IAM,
  MIDISHEET notification target/rule, and isolated intake/retry/DLQ installed.
- Renderer, API, frontend, MinIO, and RabbitMQ Flux releases all Ready.
- KEDA min **0**, max **2**, ready with unacknowledged deliveries included;
  renderer returned to **zero replicas** after the test.

## Verification

- **55 frontend tests**, **95 API tests**, **9 worker tests** passed.
- Frontend lint passed with the three existing `useMidiExport` dependency
  warnings. Both cloud/local builds passed; existing bundle-size notices remain.
- Helm lint/template, YAML parsing, Kubernetes API-server dry runs, Kustomize
  build, and `git diff --check` passed for affected boundaries.
- Browser fixture loaded eight notes with **zero API or upload requests**.
  Deleted one note in the popup, changed BPM to 89 and meter to 6/8, then queued.
  The captured uploaded MIDI contained **seven notes**, **89 BPM**, and **6/8**.
  Direction switching preserved unsubmitted notes/tempo with no upload, and
  History selected `/sheet-jobs` or `/score-jobs` according to direction.
  The fixture used synthetic API responses; the separate test below used real
  Keycloak/PostgreSQL/MinIO/RabbitMQ/MuseScore services.
- Live **`midi-sheet-smoke-v002` completed successfully**: disposable Keycloak
  identities, authenticated creation, constrained MinIO POST, native event
  delivery, real MuseScore output, signed source/PDF/MusicXML restoration,
  retained history, unauthenticated/foreign/expired rejection, studio isolation,
  duplicate delivery, expired lease recovery, durable attempt cap, delayed
  storage retry, and removal of its own rows/objects/identities. Initial
  conversion reached completion in **18.1 seconds**, including queue wakeup and
  Pod startup; this is not an isolated rendering or AWS performance benchmark.
- Offline read-only container exported the existing Debussy MIDI to a valid
  three-page PDF and MusicXML. MuseScore exited zero. All three PDF pages were
  rendered with Poppler and visually inspected for missing glyphs/blank output.
- Deployed `/score-to-midi` page was reloaded and switched to MIDI-to-sheet;
  anonymous input/queue controls remained disabled by Keycloak gating.

The first live run completed conversion/history but failed its management-port
queue assertion because the new test name was absent from the explicit network
allowlist. Its test resources were cleaned. v002 reran all assertions after the
scoped policy fix. The chart initially stalled on Flux's sparse source archive;
the committed source allowlist and chart reconciliation resolved it.

## Current limits

MID/MIDI up to 10 MiB; type 0/1 metrical timing, 30 minutes, 64 tracks,
100,000 events, 20,000 notes; rendering deadline 120 seconds. Outputs are PDF
and MusicXML. Existing local gaps remain: daily quotas and scheduled physical
retention cleanup; expired rows are hidden by owner-bound API reads. This
rollout validates Apple Silicon k3d CPU processing, not AWS/Lambda deployment.
