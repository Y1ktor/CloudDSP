#!/usr/bin/env ruby
# Adopt the Demucs Deployment and its queue scaler as one release. KEDA,
# shared TriggerAuthentication, runtime Secrets, and the generated HPA retain
# their existing owners. Helm values select idle zero or a Ready warm minimum.
require_relative '../lib/paths'
require_relative '../lib/helm-release'
require_relative '../gitops/demucs-flux-ownership'

class DemucsRelease < HelmRelease
  SCALER = 'clouddsp-demucs-rabbitmq-scaler'
  HPA = "keda-hpa-#{SCALER}"

  # Maintenance is allowed only for an already deployed, strictly idle
  # release. The optional Flux adapter replaces just the delivery write;
  # complete source/stored/live and stable resource identity checks remain.
  def run(mode)
    return super unless mode == 'reconcile'

    check_tools
    check_chart_and_source
    ensure_true(@expected_replicas.zero?, 'reconcile requires autoscaling.minReplicaCount: 0')
    before = check_cluster('Helm')
    reconcile_release
    after = check_cluster('Helm')
    verify_stable_identity(before, after)
    puts 'demucs delivery reconciliation verified; worker, scaler, HPA, and idle state preserved.'
  rescue StandardError => error
    warn "demucs #{mode} stopped: #{error.message}"
    exit 1
  end

  def initialize
    super(
      component: 'demucs',
      namespace: 'clouddsp-app',
      release: 'clouddsp-demucs',
      source_files: %w[demucs-deployment.yaml demucs-scaledobject.yaml],
      resources: ["deployment/clouddsp-demucs", "scaledobject/#{SCALER}"],
      pod_selector: 'app.kubernetes.io/name=demucs,app.kubernetes.io/instance=clouddsp-demucs,app.kubernetes.io/component=audio-separation-worker',
      expected_replicas: 0,
      worker_scaling: true,
      allow_fresh_install: true,
      before_install: [
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb database demucs verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb rabbitmq demucs verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/demucs-minio-secret-stage.rb verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/stages/minio/minio-demucs-iam-stage.rb verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/releases/scaling-auth-release.rb verify-prerequisites]
      ],
      smoke_job: { name: 'demucs-worker-smoke', manifest: 'tests/demucs-worker-smoke/demucs-worker-smoke-job.yaml' },
      smoke_timeout_seconds: 900
    )
  end

  private

  def usage
    super + '|reconcile'
  end

  def reconcile_release
    output = command('helm', 'upgrade', @release, @chart.to_s,
                     '--kube-context', CONTEXT, '--namespace', @namespace,
                     '--wait', '--timeout', '3m')
    puts output.lines.grep(/^(NAME|NAMESPACE|STATUS|REVISION):/)
  end


  def check_cluster(owner, allow_failed_release: false)
    result = super
    scaler = result.fetch(:objects).fetch(['ScaledObject', 'clouddsp-app', SCALER])
    ensure_true(scaler.dig('status', 'conditions').any? { |item| item['type'] == 'Ready' && item['status'] == 'True' },
                'Demucs ScaledObject is not Ready')
    ensure_true(scaler.dig('spec', 'triggers', 0, 'authenticationRef', 'name') == 'clouddsp-rabbitmq-scaler-authentication' &&
                scaler.dig('spec', 'triggers', 1, 'authenticationRef', 'name') == 'clouddsp-demucs-postgresql-scaler-authentication',
                'Demucs scaler no longer references both shared KEDA authentications')
    hpa = JSON.parse(kubectl('get', "hpa/#{HPA}", '--output', 'json'))
    ensure_true(hpa.dig('spec', 'scaleTargetRef') == { 'apiVersion' => 'apps/v1', 'kind' => 'Deployment', 'name' => 'clouddsp-demucs' },
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

module CloudDSPDemucsRelease
  # Tests and CLI use the same runner, retaining its worker/HPA and smoke gates.
  def self.build
    DemucsRelease.new.extend(DemucsFluxOwnership)
  end
end

CloudDSPDemucsRelease.build.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
