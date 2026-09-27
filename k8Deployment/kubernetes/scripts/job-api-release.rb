#!/usr/bin/env ruby
# Adopt only the already-running Job API Deployment, internal Service, and
# same-origin Ingress. Database bootstrap/migrations, the frontend route,
# runtime Secrets, and smoke Jobs retain their separate lifecycle boundaries.
require_relative 'stateless-release'

StatelessRelease.new(
  component: 'job-api',
  source_directory: 'api',
  namespace: 'clouddsp-app',
  release: 'clouddsp-job-api',
  source_files: %w[job-api-deployment.yaml job-api-service.yaml job-api-ingress.yaml],
  resources: %w[deployment/clouddsp-job-api service/clouddsp-job-api ingress/clouddsp-job-api],
  pod_selector: 'app.kubernetes.io/name=job-api,app.kubernetes.io/instance=clouddsp-job-api,app.kubernetes.io/component=api',
  image_lock_key: 'job-api',
  verify_running_digest: true,
  health_host: 'clouddsp.localhost',
  health_path: '/auth/me',
  health_status: '401',
  additional_http_checks: [['/jobs', '401']]
).run(ARGV.length == 1 ? ARGV.first : nil)
