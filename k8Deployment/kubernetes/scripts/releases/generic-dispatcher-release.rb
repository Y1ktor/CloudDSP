#!/usr/bin/env ruby
# Adopt only the route-aware dispatcher Deployment. The legacy Demucs-only
# controller, its separate Helm release, and all bootstrap/Secret resources
# remain outside this release. Source and live specs must match before Helm
# can change the existing Deployment's ownership metadata.
require_relative '../lib/paths'
require_relative '../lib/helm-release'

HelmRelease.new(
  component: 'generic-dispatcher',
  source_directory: 'dispatcher',
  namespace: 'clouddsp-app',
  release: 'clouddsp-generic-dispatcher',
  source_files: %w[dispatcher-generic-deployment.yaml],
  resources: %w[deployment/clouddsp-generic-dispatcher],
  pod_selector: 'app.kubernetes.io/name=dispatcher,app.kubernetes.io/instance=clouddsp-generic-dispatcher,app.kubernetes.io/component=generic-outbox-publisher',
  image_lock_key: 'dispatcher',
  verify_running_digest: true,
  allow_fresh_install: true,
  before_install: [
    %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb database dispatcher verify],
    %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb rabbitmq dispatcher verify],
    %w[ruby ./k8Deployment/kubernetes/scripts/releases/job-api-release.rb verify]
  ]
).run(ARGV.length == 1 ? ARGV.first : nil)
