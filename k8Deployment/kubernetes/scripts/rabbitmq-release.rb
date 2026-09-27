#!/usr/bin/env ruby
# Adopt only the running broker StatefulSet, its three stable Services, and
# its ingress NetworkPolicy. A stopped-volume backup and isolated broker
# restore gate Helm ownership; the generated PVC/PV, messages, credentials,
# topology bootstrap Jobs, and KEDA resources remain outside this release.
require_relative 'stateless-release'

StatelessRelease.new(
  component: 'rabbitmq',
  namespace: 'clouddsp-data',
  release: 'clouddsp-rabbitmq',
  source_files: %w[rabbitmq-statefulset.yaml rabbitmq-service.yaml rabbitmq-headless-service.yaml rabbitmq-management-service.yaml rabbitmq-ingress-network-policy.yaml],
  resources: %w[statefulset/clouddsp-rabbitmq service/clouddsp-rabbitmq service/clouddsp-rabbitmq-headless service/clouddsp-rabbitmq-management networkpolicy/clouddsp-rabbitmq-ingress],
  pod_selector: 'app.kubernetes.io/name=rabbitmq,app.kubernetes.io/instance=clouddsp-rabbitmq,app.kubernetes.io/component=message-broker',
  workload_kind: 'StatefulSet',
  pvc_name: 'rabbitmq-data-clouddsp-rabbitmq-0',
  before_adopt: [StatelessRelease::ROOT.join('scripts', 'rabbitmq-backup-and-restore-test.py').to_s],
  verify_running_digest: true,
  smoke_job: {
    name: 'rabbitmq-amqp-smoke',
    manifest: 'tests/rabbitmq-smoke/rabbitmq-amqp-smoke-job.yaml'
  }
).run(ARGV.length == 1 ? ARGV.first : nil)
