# Live homr score conversion smoke

After the score worker chart reaches Ready, apply the immutable v002
ConfigMap and one-shot Job in this directory. The fixture is an original
two-bar C-major engraving generated from `fixture.musicxml` with MuseScore
and rendered at 1800 pixels using Poppler. A local homr 0.7.0 run recognized
all eight quarter notes before it was embedded in the ConfigMap.

The Job creates a disposable Keycloak user and client, creates an authenticated
score job, uploads the JPEG through its signed MinIO form, polls the
owner-bound status API, checks PostgreSQL completion and both private result
objects, then removes its user, client, row, source, and result objects. It
also verifies all eight notes, signed downloads, active and terminal duplicate
uploads, invalid-image failure, expired-lease recovery, and the persistent
three-attempt ceiling, and delayed retries after a transient storage error. RabbitMQ is inspected without consuming other messages.
It fails if homr produces no playable MIDI or the processing queue never hands
the job to a worker. The Job and ConfigMap stay outside Flux because they
are disposable test resources.

```sh
kubectl --context k3d-clouddsp-local apply -f k8Deployment/kubernetes/tests/score-omr-smoke/score-omr-smoke-v002-configmap.yaml
kubectl --context k3d-clouddsp-local apply -f k8Deployment/kubernetes/tests/score-omr-smoke/score-omr-smoke-v002-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data wait --for=condition=complete job/score-omr-smoke-v002 --timeout=900s
kubectl --context k3d-clouddsp-local -n clouddsp-data logs job/score-omr-smoke-v002
```

## Latest local verification

On 2026-10-07, v002 completed successfully in 3m56s using the deployed homr
0.7.0 CPU consumer. The first conversion returned all eight notes in 79.5s,
including activation from zero replicas. Signed MIDI/MusicXML downloads,
active and terminal duplicate uploads, invalid-image failure, expired-lease
recovery, the durable attempt cap, and the 30-second retry handoff all passed.
All five disposable score rows and their objects, plus the temporary Keycloak
user/client, were removed. KEDA reached two workers during duplicate delivery.

Worker unit tests (9), API tests (74), frontend tests (47), both frontend
profile builds, Helm lint/template, and Kubernetes server dry runs passed.

If an interrupted test leaves its disposable identity, use
`python3 k8Deployment/kubernetes/tests/score-omr-smoke/cleanup-identity.py --pod-suffix XXXXX`
with the five-character suffix from that test's Pod name. This removes only
the exact smoke user and client; source/result cleanup remains in the test.

## History rollout verification (v004)

The immutable v004 Job extends v002 with anonymous rejection, two real
Keycloak subjects, separate score/stem history lists, PostgreSQL retention,
and exact source/MIDI restoration through signed downloads. Opening history
must leave the completed job's attempt count and lease unchanged. A pending
stem job and expired score job are created only for this test and removed
alongside both disposable users and the client. Existing user data is untouched.
The original inference, duplicate, failure and retry checks still run. v004
refreshes the disposable access tokens before history checks so cold CPU
startup does not consume the short Keycloak master-realm token lifetime.

```sh
kubectl --context k3d-clouddsp-local apply -f k8Deployment/kubernetes/tests/score-omr-smoke/score-omr-smoke-v004-configmap.yaml
kubectl --context k3d-clouddsp-local apply -f k8Deployment/kubernetes/tests/score-omr-smoke/score-omr-smoke-v004-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data logs -f job/score-omr-smoke-v004
```

The finite Job has a 900-second deadline and a 600-second TTL after completion.
Inspect its `Complete` condition as well as its safe summary logs. No secrets,
private storage coordinates, or signed URLs are printed.

## Score history rollout verified — 2026-10-08

Flux deployed API `0.0.12-score-history` and frontend `0.6.6-score-history`
from release commit `355b081`. Running Pod image IDs matched the immutable
lock references, both HelmReleases became Ready, and the native release
verification scripts confirmed Flux ownership, manifests, readiness and
protected browser routes. The committed score-source read-only IAM bootstrap
completed before the API rollout. The deployed score and stem pages loaded
through the local ingress; anonymous score history returned HTTP 401.

Both v003 and v004 completed successfully. v003 took 90 seconds; its first
homr transcription returned eight notes in 24.2 seconds. v004 also passed the
explicit token renewal and returned eight notes in 9.1 seconds on the warm
local CPU worker. These figures describe the small original fixture in the
ARM64 k3d environment, rather than representative multi-page throughput.

Live checks proved authenticated score listing, separate stem listing, two
account subjects, expired-row exclusion, signed source/MIDI restoration, and
unchanged attempt/lease state after opening a saved score. Duplicate delivery,
invalid-image failure, expired lease recovery, the durable attempt ceiling,
and delayed retry all passed. Every disposable score/stem row, source/result
object, both users and the temporary client were removed by the test.

API unit tests (80), frontend tests (52), lint, both frontend profile builds,
Helm lint/template and server schema validation passed. Existing frontend
hook-dependency and chunk-size warnings remain unchanged. Browser fixture
checks verified context-sensitive History, account reset, tab persistence,
main/popup BPM and meter synchronization, and the MIDI playback clock.
The deployed anonymous pages also passed navigation checks. Audible piano
sample playback was not verified in the earlier Vite fixture because that
development origin could not load the samples.
