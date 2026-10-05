# Bind only ADTOF's Deployment/ScaledObject release to shared Flux verification.
# The chart omits replicas, and the narrow drift exception leaves /scale with
# KEDA. All other source/render/stored/live and HPA ownership checks remain.
require_relative 'flux-ownership'

module AdtofFluxOwnership
  include FluxOwnership
  VALUES_FILE = './k8Deployment/kubernetes/helm/adtof/values.yaml'.freeze
  REPLICA_IGNORE = {
    'paths' => ['/spec/replicas'],
    'target' => { 'group' => 'apps', 'version' => 'v1', 'kind' => 'Deployment',
                  'name' => 'clouddsp-adtof', 'namespace' => 'clouddsp-app' }
  }.freeze

  private

  def flux_ownership_component
    'adtof'
  end

  def validate_flux_ownership_record
    super
    record = @flux_ownership_helmrelease
    chart = record.dig('spec', 'chart', 'spec')
    ensure_true(chart.is_a?(Hash) && chart['chart'] == './k8Deployment/kubernetes/helm/adtof' &&
                chart['reconcileStrategy'] == 'Revision' &&
                chart['sourceRef'] == { 'kind' => 'GitRepository', 'name' => 'flux-system', 'namespace' => 'flux-system' } &&
                chart['valuesFiles'] == [VALUES_FILE] && chart['ignoreMissingValuesFiles'] == false &&
                record.dig('spec', 'values').to_h.empty? && record.dig('spec', 'valuesFrom').to_a.empty?,
                'ADTOF Flux chart source or values differs from the reviewed configuration')
    ensure_true(record.dig('spec', 'dependsOn') == [{ 'name' => 'clouddsp-scaling-auth', 'namespace' => 'flux-system' }],
                'ADTOF Flux requires the shared scaling-auth readiness dependency')
    ensure_true(record.dig('spec', 'driftDetection') == { 'mode' => 'enabled', 'ignore' => [REPLICA_IGNORE] },
                'ADTOF Flux drift correction must preserve only the reviewed KEDA replica field')
  end
end
