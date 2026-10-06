#!/usr/bin/env ruby
# The legacy Demucs-only outbox publisher is a single internal Deployment.
# This script adopts only that controller; the generic dispatcher and all
# database/broker bootstrap resources keep their existing separate ownership.
require_relative '../lib/paths'
require_relative '../lib/helm-release'
require_relative '../gitops/dispatcher-flux-ownership'

module CloudDSPDispatcherRelease
  # Expose the real runner to deployment regressions while keeping CLI use.
  def self.build
    HelmRelease.new(
      component: 'dispatcher',
      namespace: 'clouddsp-app',
      release: 'clouddsp-dispatcher',
      source_files: %w[dispatcher-deployment.yaml],
      resources: %w[deployment/clouddsp-dispatcher],
      pod_selector: 'app.kubernetes.io/name=dispatcher,app.kubernetes.io/instance=clouddsp-dispatcher,app.kubernetes.io/component=outbox-publisher',
      image_lock_key: 'dispatcher-demucs-only',
      verify_running_digest: true,
      allow_fresh_install: true,
      before_install: [
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb database dispatcher verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb rabbitmq dispatcher verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/releases/job-api-release.rb verify]
      ]
    ).extend(DispatcherFluxOwnership)
  end
end

CloudDSPDispatcherRelease.build.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
