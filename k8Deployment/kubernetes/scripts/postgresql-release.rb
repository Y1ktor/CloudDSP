#!/usr/bin/env ruby
# Adopt one existing StatefulSet and its two stable Services only after exact
# source/render/live parity and an isolated restore of a fresh logical backup.
# Generated PVC/PV data, administrator Secret, schemas, roles, migrations, and
# bootstrap Jobs remain outside the Helm release and are never deleted here.
require_relative 'stateless-release'

StatelessRelease.new(
  component: 'postgresql',
  namespace: 'clouddsp-data',
  release: 'clouddsp-postgresql',
  source_files: %w[postgresql-statefulset.yaml postgresql-service.yaml postgresql-headless-service.yaml],
  resources: %w[statefulset/clouddsp-postgresql service/clouddsp-postgresql service/clouddsp-postgresql-headless],
  pod_selector: 'app.kubernetes.io/name=postgresql,app.kubernetes.io/instance=clouddsp-postgresql,app.kubernetes.io/component=database',
  workload_kind: 'StatefulSet',
  pvc_name: 'postgres-data-clouddsp-postgresql-0',
  before_adopt: [StatelessRelease::ROOT.join('scripts', 'postgresql-backup-and-restore-test.sh').to_s],
  smoke_job: {
    name: 'postgresql-read-write-smoke',
    manifest: 'tests/postgresql-smoke/postgresql-read-write-smoke-job.yaml'
  }
).run(ARGV.length == 1 ? ARGV.first : nil)
