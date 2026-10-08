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

## History rollout verification (v003)

The immutable v003 Job extends v002 with anonymous rejection, two real
Keycloak subjects, separate score/stem history lists, PostgreSQL retention,
and exact source/MIDI restoration through signed downloads. Opening history
must leave the completed job's attempt count and lease unchanged. A pending
stem job and expired score job are created only for this test and removed
alongside both disposable users and the client. Existing user data is untouched.
The original inference, duplicate, failure and retry checks still run.

```sh
kubectl --context k3d-clouddsp-local apply -f k8Deployment/kubernetes/tests/score-omr-smoke/score-omr-smoke-v003-configmap.yaml
kubectl --context k3d-clouddsp-local apply -f k8Deployment/kubernetes/tests/score-omr-smoke/score-omr-smoke-v003-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data logs -f job/score-omr-smoke-v003
```

The finite Job has a 900-second deadline and a 600-second TTL after completion.
Inspect its `Complete` condition as well as its safe summary logs. No secrets,
private storage coordinates, or signed URLs are printed.
