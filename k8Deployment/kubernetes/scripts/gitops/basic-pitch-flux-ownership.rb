# Bind only Basic Pitch's Deployment/ScaledObject release to shared Flux verification.
# The chart omits replicas, and the narrow drift exception leaves /scale with
# KEDA. All other source/render/stored/live and HPA ownership checks remain.
require_relative 'flux-ownership'

module BasicPitchFluxOwnership
  include FluxOwnership
  VALUES_FILE = './k8Deployment/kubernetes/helm/basic-pitch/values.yaml'.freeze
  REPLICA_IGNORE = {
    'paths' => ['/spec/replicas'],
    'target' => { 'group' => 'apps', 'version' => 'v1', 'kind' => 'Deployment',
                  'name' => 'clouddsp-basic-pitch', 'namespace' => 'clouddsp-app' }
  }.freeze

  private

  # Historical native upgrades must also stop when any Flux release exists,
  # including an unhealthy or suspended release. All use check_tools before
  # chart rendering or a Helm write; API lookup failures remain closed.
  def flux_direct_write_modes
    super + %w[upgrade-scaling upgrade-stabilization upgrade-numba]
  end

  def flux_ownership_component
    'basic-pitch'
  end

  def validate_flux_ownership_record
    super
    record = @flux_ownership_helmrelease
    chart = record.dig('spec', 'chart', 'spec')
    ensure_true(chart.is_a?(Hash) && chart['chart'] == './k8Deployment/kubernetes/helm/basic-pitch' &&
                chart['reconcileStrategy'] == 'Revision' &&
                chart['sourceRef'] == { 'kind' => 'GitRepository', 'name' => 'flux-system', 'namespace' => 'flux-system' } &&
                chart['valuesFiles'] == [VALUES_FILE] && chart['ignoreMissingValuesFiles'] == false &&
                record.dig('spec', 'values').to_h.empty? && record.dig('spec', 'valuesFrom').to_a.empty?,
                'Basic Pitch Flux chart source or values differs from the reviewed configuration')
    ensure_true(record.dig('spec', 'dependsOn') == [{ 'name' => 'clouddsp-minio', 'namespace' => 'flux-system' },
                                                    { 'name' => 'clouddsp-scaling-auth', 'namespace' => 'flux-system' }],
                'Basic Pitch Flux requires MinIO and shared scaling-auth readiness dependencies')
    ensure_true(record.dig('spec', 'driftDetection') == { 'mode' => 'enabled', 'ignore' => [REPLICA_IGNORE] },
                'Basic Pitch Flux drift correction must preserve only the reviewed KEDA replica field')
  end
end
