# Bind only Demucs's Deployment/ScaledObject release to shared Flux verification.
# The chart omits replicas, and the narrow drift exception leaves /scale with
# KEDA. All other source/render/stored/live and HPA ownership checks remain.
require_relative 'flux-ownership'

module DemucsFluxOwnership
  include FluxOwnership
  VALUES_FILE = './k8Deployment/kubernetes/helm/demucs/values.yaml'.freeze
  REPLICA_IGNORE = {
    'paths' => ['/spec/replicas'],
    'target' => { 'group' => 'apps', 'version' => 'v1', 'kind' => 'Deployment',
                  'name' => 'clouddsp-demucs', 'namespace' => 'clouddsp-app' }
  }.freeze

  private

  # Maintenance verifies the reconciled release without a competing native
  # write. The parent still checks exact manifests, idle state, and HPA owner.
  def reconcile_release
    return super unless @flux_ownership_helmrelease

    puts 'demucs is reconciled by Flux; verified its existing release without a direct Helm upgrade.'
  end

  def flux_ownership_component
    'demucs'
  end

  def validate_flux_ownership_record
    super
    record = @flux_ownership_helmrelease
    chart = record.dig('spec', 'chart', 'spec')
    ensure_true(chart.is_a?(Hash) && chart['chart'] == './k8Deployment/kubernetes/helm/demucs' &&
                chart['reconcileStrategy'] == 'Revision' &&
                chart['sourceRef'] == { 'kind' => 'GitRepository', 'name' => 'flux-system', 'namespace' => 'flux-system' } &&
                chart['valuesFiles'] == [VALUES_FILE] && chart['ignoreMissingValuesFiles'] == false &&
                record.dig('spec', 'values').to_h.empty? && record.dig('spec', 'valuesFrom').to_a.empty?,
                'Demucs Flux chart source or values differs from the reviewed configuration')
    ensure_true(record.dig('spec', 'dependsOn') == [{ 'name' => 'clouddsp-minio', 'namespace' => 'flux-system' },
                                                    { 'name' => 'clouddsp-scaling-auth', 'namespace' => 'flux-system' }],
                'Demucs Flux requires MinIO and shared scaling-auth readiness dependencies')
    ensure_true(record.dig('spec', 'driftDetection') == { 'mode' => 'enabled', 'ignore' => [REPLICA_IGNORE] },
                'Demucs Flux drift correction must preserve only the reviewed KEDA replica field')
  end
end
