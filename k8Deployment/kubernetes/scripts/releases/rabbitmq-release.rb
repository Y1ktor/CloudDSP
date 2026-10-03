#!/usr/bin/env ruby
# Helm owns the broker StatefulSet, its three stable Services, and its ingress
# NetworkPolicy. Existing broker adoption requires a stopped-volume backup and
# isolated restore; fresh installation instead requires absent resources,
# generated storage, and Pod plus a verified administrator Secret. The PVC/PV,
# messages, credentials, topology Jobs, and KEDA remain outside this release.
require_relative '../lib/paths'
require_relative '../lib/helm-release'

HelmRelease.new(
  component: 'rabbitmq',
  namespace: 'clouddsp-data',
  release: 'clouddsp-rabbitmq',
  source_files: %w[rabbitmq-statefulset.yaml rabbitmq-service.yaml rabbitmq-headless-service.yaml rabbitmq-management-service.yaml rabbitmq-ingress-network-policy.yaml],
  resources: %w[statefulset/clouddsp-rabbitmq service/clouddsp-rabbitmq service/clouddsp-rabbitmq-headless service/clouddsp-rabbitmq-management networkpolicy/clouddsp-rabbitmq-ingress],
  pod_selector: 'app.kubernetes.io/name=rabbitmq,app.kubernetes.io/instance=clouddsp-rabbitmq,app.kubernetes.io/component=message-broker',
  workload_kind: 'StatefulSet',
  pvc_name: 'rabbitmq-data-clouddsp-rabbitmq-0',
  before_adopt: [CloudDSPPaths.script('rabbitmq-backup-and-restore-test.py').to_s],
  allow_fresh_install: true,
  before_install: ['ruby', CloudDSPPaths.script('rabbitmq-secret-stage.rb').to_s, 'verify'],
  fresh_install_timeout: '5m',
  verify_running_digest: true,
  smoke_job: {
    name: 'rabbitmq-amqp-smoke',
    manifest: 'tests/rabbitmq-smoke/rabbitmq-amqp-smoke-job.yaml'
  }
).run(ARGV.length == 1 ? ARGV.first : nil)
