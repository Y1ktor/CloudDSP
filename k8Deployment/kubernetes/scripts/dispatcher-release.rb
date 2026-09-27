#!/usr/bin/env ruby
# The legacy Demucs-only outbox publisher is a single internal Deployment.
# This script adopts only that controller; the generic dispatcher and all
# database/broker bootstrap resources keep their existing separate ownership.
require_relative 'stateless-release'

StatelessRelease.new(
  component: 'dispatcher',
  namespace: 'clouddsp-app',
  release: 'clouddsp-dispatcher',
  source_files: %w[dispatcher-deployment.yaml],
  resources: %w[deployment/clouddsp-dispatcher],
  pod_selector: 'app.kubernetes.io/name=dispatcher,app.kubernetes.io/instance=clouddsp-dispatcher,app.kubernetes.io/component=outbox-publisher',
  image_lock_key: 'dispatcher-demucs-only',
  verify_running_digest: true
).run(ARGV.length == 1 ? ARGV.first : nil)
