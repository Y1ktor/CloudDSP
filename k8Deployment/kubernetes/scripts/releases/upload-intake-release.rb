#!/usr/bin/env ruby
# This release owns only the outbound MinIO event consumer Deployment.
# The shared checker requires source/render/live equality before an explicit
# one-time Helm ownership change and preserves Deployment and Pod identity.
# The Flux adapter reserves writes while its HelmRelease exists; the build
# method keeps this exact configuration importable for deployment regressions.
require_relative '../lib/paths'
require_relative '../lib/helm-release'
require_relative '../gitops/upload-intake-flux-ownership'

module CloudDSPUploadIntakeRelease
  def self.build
    HelmRelease.new(
      component: 'upload-intake',
      namespace: 'clouddsp-app',
      release: 'clouddsp-upload-intake',
      source_files: %w[upload-intake-deployment.yaml],
      resources: %w[deployment/clouddsp-upload-intake],
      pod_selector: 'app.kubernetes.io/name=upload-intake,app.kubernetes.io/instance=clouddsp-upload-intake,app.kubernetes.io/component=source-event-consumer',
      verify_running_digest: true,
      allow_fresh_install: true,
      before_install: [
        %w[ruby ./k8Deployment/kubernetes/scripts/releases/job-api-release.rb verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb database upload-intake verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/upload-intake-rabbitmq-secret-stage.rb verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/rabbitmq/rabbitmq-source-intake-bootstrap.rb verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/upload-intake-minio-secret-stage.rb verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/minio/minio-upload-intake-iam-stage.rb verify]
      ]
    ).extend(UploadIntakeFluxOwnership)
  end
end

CloudDSPUploadIntakeRelease.build.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
