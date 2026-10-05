#!/usr/bin/env ruby
# Helm owns the MinIO StatefulSet, both stable Services, and the S3 Ingress.
# Existing-resource adoption requires a stopped-volume snapshot and isolated
# S3 restore. Fresh creation instead requires an absent release, objects,
# generated claim, and Pod plus verified root and AMQP Secrets. Buckets, IAM,
# PVC/PV, Secrets, and bootstrap Jobs remain outside this Helm release.
require_relative '../lib/paths'
require_relative '../lib/helm-release'

HelmRelease.new(
  component: 'minio',
  namespace: 'clouddsp-data',
  release: 'clouddsp-minio',
  source_files: %w[minio-statefulset.yaml minio-service.yaml minio-headless-service.yaml minio-s3-ingress.yaml],
  resources: %w[statefulset/clouddsp-minio service/clouddsp-minio service/clouddsp-minio-headless ingress/clouddsp-minio-s3],
  pod_selector: 'app.kubernetes.io/name=minio,app.kubernetes.io/instance=clouddsp-minio,app.kubernetes.io/component=object-storage',
  workload_kind: 'StatefulSet',
  pvc_name: 'minio-data-clouddsp-minio-0',
  before_adopt: [CloudDSPPaths.script('minio-backup-and-restore-test.py').to_s],
  allow_fresh_install: true,
  before_install: [
    ['ruby', CloudDSPPaths.script('minio-root-secret-stage.rb').to_s, 'verify'],
    ['ruby', CloudDSPPaths.script('minio-amqp-secret-stage.rb').to_s, 'verify']
  ],
  fresh_install_timeout: '5m',
  verify_running_digest: true,
  health_host: 'minio.localhost',
  health_path: '/minio/health/ready',
  smoke_job: {
    name: 'minio-s3-api-smoke',
    manifest: 'tests/minio-smoke/minio-s3-api-smoke-job.yaml'
  }
).run(ARGV.length == 1 ? ARGV.first : nil)
