# Score OMR consumer

The Linux ARM64 CPU image runs homr 0.7.0, Poppler, and music21. It consumes
`clouddsp.score-intake` with one unacknowledged delivery per Pod. KEDA sets the
Deployment to 0–2 Pods from ready plus unacknowledged queue depth. The worker
does not create Kubernetes Jobs or expose a network listener.

For each notification the worker checks the canonical `score-inputs/{job_id}`
path, reads the owner-bound intent from PostgreSQL, and compares the live MinIO
object's size and content type. A v011 lease advances the row through
`upload_pending` → `source_uploaded` → `processing` → `completed` or `failed`.
Duplicate notifications cannot claim an
active lease. PDF sources are limited to four pages rendered at a fixed
300 DPI, matching the evaluated Mac benchmark. Page MediaBoxes are checked
before rendering and decoded images afterward, with a 36-million-pixel cap.
Homr writes MusicXML per page. music21 combines pages using a common page
boundary and each measure's original relative offset, so incomplete staves
cannot shift the next page. MIDI is exported from that recognized timing;
a separate copy exports MusicXML with automatic notation repair disabled.
This avoids rewriting rhythms or failing on sparse polyphonic voice IDs.
The worker rejects zero-note output.
Both private objects are uploaded before the lease-guarded completion update;
only then is the RabbitMQ delivery acknowledged. Bounded transient retries
pass through the existing 30-second score retry queue. Exhausted transient
errors are dead-lettered for operator recovery. They also set `failed` when
PostgreSQL is reachable and no other active lease owns the row. All browser result URLs are short lived,
owner checked API signatures generated from stable keys.

Each page has a 120-second inference deadline, with four pages maximum and
a 900-second lease. ONNX sessions use two intra-operation threads, one
inter-operation thread, and no idle spinning; OpenCV uses one thread. This
keeps host-wide default pools from overwhelming the Pod's two-CPU limit.
The original eight-note fixture took 51 seconds and the first Debussy page
took 89 seconds in isolated Linux ARM64 two-CPU containers. These are local
timings, not an AWS or recognition-accuracy benchmark.

Build with `bash k8Deployment/kubernetes/scripts/images/build-score-omr-image.sh`.
Run worker tests inside the image with its dependency environment; test source
lives in `tests/`. The live smoke also covers duplicate notifications, invalid
images, expired lease recovery, and the durable three-attempt ceiling.

After committing the versioned bootstrap resources, provision the worker:

```sh
python3 k8Deployment/kubernetes/scripts/stages/credentials/score-omr-bootstrap.py \
  --secrets-file k8Deployment/.local/score-omr-credentials.json
```

This creates three restricted app-namespace runtime Secrets and finite
database/RabbitMQ/MinIO bootstrap Jobs, then removes their temporary
data-namespace credential copies. Keep the ignored JSON for recovery.
The Flux chart and image digest must be available before adding its
HelmRelease to the cluster root.

The local CPU profile is suitable for evaluation. Homr can misread key
signatures or complex notation even from clean pages; the resulting MIDI is
editable, not a guaranteed faithful score. Retention cleanup of score source
and result objects is not yet implemented.

## Debussy benchmark regression

`tests/check_benchmark.py` runs the real PDF renderer, homr and both artifact
exporters, then compares page MusicXML and combined MIDI byte-for-byte with
the CPU benchmark. Supply the original PDF and benchmark directory as local
read-only mounts; user sheet music and reference outputs are not committed.
For example, inside the worker image with `PYTHONPATH=/app` and test source
mounted read-only at `/tests`:

```sh
python /tests/check_benchmark.py --pdf /input/debussy.pdf \
  --benchmark /benchmark --output /comparison/debussy
```

`/benchmark` contains `cpu/debussy-{1,2}.musicxml` and
`midi/cpu/debussy-combined.mid`. The output directory must be new and writable.
The report records image dimensions, page XML equality, MIDI equality, note
count and duration. Matching the old benchmark preserves its known key and
note recognition errors; this is a pipeline regression check, not an accuracy
score or a corrected musical transcription. Existing completed job artifacts
are retained; a new upload uses the corrected worker after its rollout.

### 2026-10-08 diagnosis and correction

Worker 0.1.3 rendered the supplied two-page Debussy PDF at 2190 × 3000,
whereas the original benchmark used 2700 × 3700 at 300 DPI. With original
benchmark page PNGs, the same Linux ARM64 image and two-CPU limit produced
byte-identical page XML, isolating the recognition change to PDF rasterization.
The old append-based assembly also differed from the benchmark, and default
MusicXML notation repair failed on its sparse voice IDs.

Worker `0.1.4-benchmark-parity` restores fixed 300 DPI and the benchmark's
common page offset. It writes MIDI first and exports a separate MusicXML copy
without notation repair. The two-page local regression produced identical
page XML and combined MIDI: 666 note-on events, 66 seconds and MIDI SHA-256
`01a205f18a6701cda3f802cb9bd693ccf78e2b8b7812900edbbfd380930d1f4d`.
The corrected MusicXML also contains 666 pitched notes. Recognition still
includes the benchmark's known incorrect key changes; no musical correction
or new model was applied. CPU, memory and KEDA 0–2 limits remain unchanged.

The published ARM64 image passed the full two-page PDF regression in 27.0 s
under a two-CPU/4-GiB limit, with both page XML files and MIDI byte-identical
to the original Mac CPU benchmark. All 13 worker unit tests passed in that
image. Helm lint/template and Kubernetes server validation passed. Flux
release `470987d` installed the locked digest and retained KEDA min 0/max 2.
The live v004 smoke then completed inference, authenticated history and
source/result downloads, duplicate delivery, invalid-image failure, expired
lease recovery, durable attempt exhaustion and delayed retry. Its initial
small fixture completed in 18.1 s; all disposable rows, objects and identities
were removed. This live fixture checks pipeline behavior separately from the
local Debussy comparison.
