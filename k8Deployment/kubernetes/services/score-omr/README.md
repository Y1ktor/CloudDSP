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
active lease. PDF sources are limited to four rendered pages; images are
limited to 36 million pixels. Homr writes MusicXML per page, music21 combines
it into one MusicXML and MIDI, and the worker rejects zero-note output.
Both private objects are uploaded before the lease-guarded completion update;
only then is the RabbitMQ delivery acknowledged. Bounded transient retries
pass through the existing 30-second score retry queue. An exhausted error
sets `failed` when PostgreSQL is reachable, or dead-letters the delivery for
operator recovery when it is not. All browser result URLs are short lived,
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
data-namespace credential copies. Keep the ignored JSON for recovery or
rotation. The Flux chart and image digest must be available before adding its
HelmRelease to the cluster root.

The local CPU profile is suitable for evaluation. Homr can misread key
signatures or complex notation even from clean pages; the resulting MIDI is
editable, not a guaranteed faithful score. Retention cleanup of score source
and result objects is not yet implemented.
