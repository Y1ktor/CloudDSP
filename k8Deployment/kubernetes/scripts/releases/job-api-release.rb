#!/usr/bin/env ruby
# Own only the Job API Deployment, internal Service, and same-origin Ingress.
# A fresh install requires the reviewed database/migrations, restricted MinIO
# identity, and Keycloak realm; their Secrets and durable state remain outside
# Helm. The frontend route and smoke Jobs keep separate lifecycle boundaries.
# The opt-in Flux adapter reserves direct writes while its HelmRelease exists;
# native Helm history, runtime checks, and fresh prerequisites remain intact.
require_relative '../lib/paths'
require_relative '../lib/helm-release'
require_relative '../gitops/job-api-flux-ownership'

module CloudDSPJobApiRelease
  def self.build
    HelmRelease.new(
      component: 'job-api',
      source_directory: 'job-api',
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
      additional_http_checks: [['/jobs', '401']],
      # The shared fresh path refuses an existing release or any of its three
      # named workload objects. These read-only gates then prove credentials,
      # schema, storage policy, and OIDC audience before Helm starts the Pod.
      allow_fresh_install: true,
      before_install: [
        ['ruby', CloudDSPPaths.script('job-api-database-secret-stage.rb').to_s, 'verify'],
        ['ruby', CloudDSPPaths.script('job-api-postgresql-stage.rb').to_s, 'verify'],
        ['ruby', CloudDSPPaths.script('job-api-minio-secret-stage.rb').to_s, 'verify'],
        ['ruby', CloudDSPPaths.script('minio-job-api-iam-stage.rb').to_s, 'verify'],
        ['ruby', CloudDSPPaths.script('minio-score-upload-stage.rb').to_s, 'verify'],
        ['ruby', CloudDSPPaths.script('keycloak-config-verify.rb').to_s, 'verify']
      ]
    ).extend(JobApiFluxOwnership)
  end
end

CloudDSPJobApiRelease.build.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
