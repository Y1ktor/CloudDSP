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
three-attempt ceiling. RabbitMQ is inspected without consuming other messages.
It fails if homr produces no playable MIDI or the processing queue never hands
the job to a worker. The Job and ConfigMap stay outside Flux because they
are disposable test resources.

```sh
kubectl --context k3d-clouddsp-local apply -f k8Deployment/kubernetes/tests/score-omr-smoke/score-omr-smoke-v002-configmap.yaml
kubectl --context k3d-clouddsp-local apply -f k8Deployment/kubernetes/tests/score-omr-smoke/score-omr-smoke-v002-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data wait --for=condition=complete job/score-omr-smoke-v002 --timeout=900s
kubectl --context k3d-clouddsp-local -n clouddsp-data logs job/score-omr-smoke-v002
```
