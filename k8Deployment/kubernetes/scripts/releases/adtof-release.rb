#!/usr/bin/env ruby
# Adopt the ADTOF Deployment and its queue scaler as one release. KEDA,
# shared TriggerAuthentication, runtime Secrets, and the generated HPA retain
# their existing owners. Helm values select idle zero or a Ready warm minimum.
require_relative '../lib/paths'
require_relative '../lib/helm-release'
require_relative '../gitops/adtof-flux-ownership'

class AdtofRelease < HelmRelease
  SCALER = 'clouddsp-adtof-rabbitmq-scaler'
  HPA = "keda-hpa-#{SCALER}"

  def initialize
    super(
      component: 'adtof',
      namespace: 'clouddsp-app',
      release: 'clouddsp-adtof',
      source_files: %w[adtof-deployment.yaml adtof-scaledobject.yaml],
      resources: ["deployment/clouddsp-adtof", "scaledobject/#{SCALER}"],
      pod_selector: 'app.kubernetes.io/name=adtof,app.kubernetes.io/instance=clouddsp-adtof,app.kubernetes.io/component=drum-midi-worker',
      expected_replicas: 0,
      worker_scaling: true,
      allow_fresh_install: true,
      before_install: [
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/database/worker-database-stage.rb adtof verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb rabbitmq adtof verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/adtof-minio-secret-stage.rb verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/minio/minio-adtof-iam-stage.rb verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/releases/scaling-auth-release.rb verify-prerequisites]
      ],
      smoke_job: { name: 'adtof-worker-smoke', manifest: 'tests/adtof-worker-smoke/adtof-worker-smoke-job.yaml' },
      smoke_timeout_seconds: 840
    )
  end

  private

  def check_cluster(owner, allow_failed_release: false)
    result = super
    scaler = result.fetch(:objects).fetch(['ScaledObject', 'clouddsp-app', SCALER])
    ensure_true(scaler.dig('status', 'conditions').any? { |item| item['type'] == 'Ready' && item['status'] == 'True' },
                'ADTOF ScaledObject is not Ready')
    ensure_true(scaler.dig('spec', 'triggers', 0, 'authenticationRef', 'name') == 'clouddsp-rabbitmq-scaler-authentication',
                'ADTOF scaler no longer references shared KEDA authentication')
    hpa = JSON.parse(kubectl('get', "hpa/#{HPA}", '--output', 'json'))
    ensure_true(hpa.dig('spec', 'scaleTargetRef') == { 'apiVersion' => 'apps/v1', 'kind' => 'Deployment', 'name' => 'clouddsp-adtof' },
                'generated HPA target changed')
    owners = hpa.dig('metadata', 'ownerReferences') || []
    ensure_true(owners.length == 1 && owners.first['controller'] == true &&
                owners.first['kind'] == 'ScaledObject' && owners.first['name'] == SCALER &&
                owners.first['uid'] == scaler.dig('metadata', 'uid'),
                'generated HPA is not owned by the reviewed ScaledObject')
    result.merge(hpa_uid: hpa.dig('metadata', 'uid'), scaler_generation: scaler.dig('metadata', 'generation'))
  end

  def verify_stable_identity(before, after)
    super
    ensure_true(before.fetch(:hpa_uid) == after.fetch(:hpa_uid), 'KEDA generated HPA was replaced during adoption')
    ensure_true(before.fetch(:scaler_generation) == after.fetch(:scaler_generation),
                'ScaledObject spec generation changed during ownership-only adoption')
  end
end

module CloudDSPAdtofRelease
  # Tests and CLI use this same runner, retaining the worker's native HPA,
  # policy, idle/warm, and versioned smoke gates with optional Flux delivery.
  def self.build
    AdtofRelease.new.extend(AdtofFluxOwnership)
  end
end

CloudDSPAdtofRelease.build.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
