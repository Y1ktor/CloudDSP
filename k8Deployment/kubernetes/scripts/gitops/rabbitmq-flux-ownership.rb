# Opt in only the reviewed single-broker release to Flux ownership. The parent
# retains exact chart/source/stored/live specs, bound PVC, Pod and image gates.
# Existing vhosts, identities, messages and topology Jobs are not chart objects.
require_relative 'flux-ownership'

module RabbitMQFluxOwnership
  include FluxOwnership
  VALUES_FILE = './k8Deployment/kubernetes/helm/rabbitmq/values.yaml'.freeze

  private

  def flux_ownership_component
    'rabbitmq'
  end

  def validate_flux_ownership_record
    super
    spec = @flux_ownership_helmrelease.fetch('spec')
    chart = spec.dig('chart', 'spec')
    ensure_true(chart.is_a?(Hash) && chart['chart'] == './k8Deployment/kubernetes/helm/rabbitmq' &&
                chart['reconcileStrategy'] == 'Revision' &&
                chart['sourceRef'] == { 'kind' => 'GitRepository', 'name' => 'flux-system', 'namespace' => 'flux-system' } &&
                chart['valuesFiles'] == [VALUES_FILE] && chart['ignoreMissingValuesFiles'] == false &&
                spec.fetch('values', {}).empty? && spec.fetch('valuesFrom', []).empty?,
                'RabbitMQ Flux chart source or values differs from the reviewed configuration')
    ensure_true(spec['serviceAccountName'] == 'clouddsp-rabbitmq-helm' &&
                spec['driftDetection'] == { 'mode' => 'enabled' } && spec.fetch('dependsOn', []).empty?,
                'RabbitMQ Flux delivery identity, drift policy or dependencies differ')
    # Safety settings are part of the reviewed lifecycle boundary too. Reject
    # force, rollback/uninstall remediation, hooks, or hidden values overrides.
    reviewed = YAML.load_file(self.class::ROOT.join('gitops/clusters/clouddsp-local/rabbitmq/helmrelease.yaml')).fetch('spec')
    ensure_true(spec == reviewed, 'RabbitMQ Flux spec differs from the reviewed lifecycle configuration')
  end
end
