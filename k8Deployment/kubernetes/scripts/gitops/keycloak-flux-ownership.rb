# Opt in only the existing Keycloak release to Flux. The parent retains
# source/render/stored/live parity, Ready Pod, locked image and HTTP issuer route
# checks; database identity, realms, clients, SMTP and credentials never become chart objects.
require_relative 'flux-ownership'

module KeycloakFluxOwnership
  include FluxOwnership
  VALUES_FILE = './k8Deployment/kubernetes/helm/keycloak/values.yaml'.freeze

  private

  def flux_ownership_component
    'keycloak'
  end

  def validate_flux_ownership_record
    super
    spec = @flux_ownership_helmrelease.fetch('spec')
    chart = spec.dig('chart', 'spec')
    ensure_true(chart.is_a?(Hash) && chart['chart'] == './k8Deployment/kubernetes/helm/keycloak' &&
                chart['reconcileStrategy'] == 'Revision' &&
                chart['sourceRef'] == { 'kind' => 'GitRepository', 'name' => 'flux-system', 'namespace' => 'flux-system' } &&
                chart['valuesFiles'] == [VALUES_FILE] && chart['ignoreMissingValuesFiles'] == false &&
                spec.fetch('values', {}).empty? && spec.fetch('valuesFrom', []).empty?,
                'Keycloak Flux chart source or values differs from the reviewed configuration')
    ensure_true(spec['serviceAccountName'] == 'clouddsp-keycloak-helm' &&
                spec['driftDetection'] == { 'mode' => 'enabled' } && spec['dependsOn'] == [{ 'name' => 'clouddsp-postgresql', 'namespace' => 'flux-system' },
                                       { 'name' => 'clouddsp-mailpit', 'namespace' => 'flux-system' }],
                'Keycloak Flux delivery identity, drift policy or dependencies differ')
    # Safety settings are part of the reviewed lifecycle boundary too. Reject
    # force, rollback/uninstall remediation, hooks, or hidden values overrides.
    reviewed = YAML.load_file(self.class::ROOT.join('gitops/clusters/clouddsp-local/keycloak/helmrelease.yaml')).fetch('spec')
    # The HelmChart API defaults version to '*' even for a Git chart, where
    # Revision packaging uses Chart.yaml and the source SHA. Accept exactly
    # that API default; native revision validation still pins the base version.
    reviewed.fetch('chart').fetch('spec')['version'] ||= '*'
    ensure_true(spec == reviewed, 'Keycloak Flux spec differs from the reviewed lifecycle configuration')
  end
end
