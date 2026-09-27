#!/usr/bin/env ruby
# Mailpit's exact local adoption boundary. The shared stateless-release helper
# validates source/chart/live parity before any Helm ownership transfer; this
# entry point supplies only Mailpit's reviewed names, route, and smoke Job.
require_relative 'stateless-release'

StatelessRelease.new(
  component: 'mailpit',
  namespace: 'clouddsp-data',
  release: 'clouddsp-mailpit',
  source_files: %w[mailpit-deployment.yaml mailpit-services.yaml mailpit-ingress.yaml],
  resources: %w[deployment/clouddsp-mailpit service/clouddsp-mailpit-smtp service/clouddsp-mailpit ingress/clouddsp-mailpit],
  pod_selector: 'app.kubernetes.io/name=mailpit,app.kubernetes.io/instance=clouddsp-mailpit,app.kubernetes.io/component=test-email',
  health_host: 'mailpit.localhost',
  health_path: '/readyz',
  smoke_job: {
    name: 'mailpit-smtp-capture-smoke',
    manifest: 'tests/mailpit-smoke/mailpit-smtp-capture-smoke-job.yaml'
  }
).run(ARGV.length == 1 ? ARGV.first : nil)
