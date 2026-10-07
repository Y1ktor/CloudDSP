# Score upload integration smoke

Run this after the `0.0.10-score-upload` Job API and `0.6.3-score-upload`
frontend images, schema migration v010, MinIO SCORE AMQP target and prefix
policy/notification, RabbitMQ score topology, and broker NetworkPolicy are
ready. The test does not start an OMR consumer.

```bash
ruby k8Deployment/kubernetes/scripts/stages/database/job-api-postgresql-stage.rb verify
ruby k8Deployment/kubernetes/scripts/stages/rabbitmq/rabbitmq-score-intake-topology.rb verify
ruby k8Deployment/kubernetes/scripts/stages/minio/minio-score-upload-stage.rb verify
kubectl --context k3d-clouddsp-local -n clouddsp-data create -f k8Deployment/kubernetes/tests/score-upload-smoke/score-upload-smoke-v003-configmap.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data create -f k8Deployment/kubernetes/tests/score-upload-smoke/score-upload-smoke-v003-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data wait --for=condition=complete job/score-upload-smoke-v003 --timeout=300s
kubectl --context k3d-clouddsp-local -n clouddsp-data logs job/score-upload-smoke-v003
```

The versioned Job requires an empty score queue and no consumer. It creates a
disposable Keycloak client and user, obtains a real user token, calls the Job
API, checks the owner-bound PostgreSQL row, uploads one PNG through the signed
MinIO form, checks its private object and metadata, and confirms one matching
event is waiting in `clouddsp.score-intake`. It consumes only that test event,
then deletes its object, row, client, and user. Inspect a failed Job and its
queue before retrying; do not clear unrelated messages. The Job has no
ServiceAccount API token and its credentials come from existing Secrets.

The static contract test is independent of the cluster:

```bash
ruby k8Deployment/kubernetes/tests/score-upload-smoke/test_score_upload_wiring.rb
```
