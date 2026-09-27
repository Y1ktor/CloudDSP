#!/usr/bin/env ruby
# Adopt only the running MinIO StatefulSet, both stable Services, and the S3
# Ingress. A fresh stopped-volume snapshot and isolated S3 restore test gates
# Helm ownership; buckets, IAM state, PVC/PV, Secrets, and bootstrap Jobs stay
# outside the release and are never deleted by this script.
require_relative 'stateless-release'

StatelessRelease.new(
  component: 'minio',
  namespace: 'clouddsp-data',
  release: 'clouddsp-minio',
  source_files: %w[minio-statefulset.yaml minio-service.yaml minio-headless-service.yaml minio-s3-ingress.yaml],
  resources: %w[statefulset/clouddsp-minio service/clouddsp-minio service/clouddsp-minio-headless ingress/clouddsp-minio-s3],
  pod_selector: 'app.kubernetes.io/name=minio,app.kubernetes.io/instance=clouddsp-minio,app.kubernetes.io/component=object-storage',
  workload_kind: 'StatefulSet',
  pvc_name: 'minio-data-clouddsp-minio-0',
  before_adopt: [StatelessRelease::ROOT.join('scripts', 'minio-backup-and-restore-test.py').to_s],
  verify_running_digest: true,
  health_host: 'minio.localhost',
  health_path: '/minio/health/ready',
  smoke_job: {
    name: 'minio-s3-api-smoke',
    manifest: 'tests/minio-smoke/minio-s3-api-smoke-job.yaml'
  }
).run(ARGV.length == 1 ? ARGV.first : nil)
