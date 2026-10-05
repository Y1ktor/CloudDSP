# Bind the shared TriggerAuthentication-only runner to Flux ownership checks.
# KEDA, runtime credentials, worker ScaledObjects, generated HPAs, and worker
# Deployments keep their separate ownership and the parent's exact checks.
require_relative 'flux-ownership'

module ScalingAuthFluxOwnership
  include FluxOwnership
  VALUES_FILE = './k8Deployment/kubernetes/helm/scaling-auth/values.yaml'.freeze

  private

  def flux_ownership_component
    'scaling-auth'
  end

  def validate_flux_ownership_record
    super
    chart = @flux_ownership_helmrelease.dig('spec', 'chart', 'spec')
    ensure_true(chart.is_a?(Hash) &&
                chart['chart'] == './k8Deployment/kubernetes/helm/scaling-auth' &&
                chart['reconcileStrategy'] == 'Revision' &&
                chart['sourceRef'] == { 'kind' => 'GitRepository', 'name' => 'flux-system', 'namespace' => 'flux-system' } &&
                chart['valuesFiles'] == [VALUES_FILE] && chart['ignoreMissingValuesFiles'] == false &&
                @flux_ownership_helmrelease.dig('spec', 'values').to_h.empty? &&
                @flux_ownership_helmrelease.dig('spec', 'valuesFrom').to_a.empty?,
                'Scaling authentication Flux chart source or values differs from the reviewed configuration')
    ensure_true(@flux_ownership_helmrelease.dig('spec', 'dependsOn') ==
                  [{ 'name' => 'keda', 'namespace' => 'flux-system' },
                   { 'name' => 'clouddsp-rabbitmq', 'namespace' => 'flux-system' }],
                'Scaling authentication Flux dependencies must be the reviewed KEDA and RabbitMQ HelmReleases')
  end

  def reconcile_release
    return super unless @flux_ownership_helmrelease

    puts 'scaling-auth is reconciled by Flux; verified its existing release without a direct Helm upgrade.'
  end
end
