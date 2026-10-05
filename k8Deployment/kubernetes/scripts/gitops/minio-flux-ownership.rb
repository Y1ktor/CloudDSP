# Opt in only the existing MinIO release to Flux. The parent retains
# source/render/stored/live parity, Ready Pod, immutable image and bound claim
# checks; buckets, IAM, notifications and objects never become chart objects.
require_relative 'flux-ownership'

module MinIOFluxOwnership
  include FluxOwnership
  VALUES_FILE = './k8Deployment/kubernetes/helm/minio/values.yaml'.freeze

  private

  def flux_ownership_component
    'minio'
  end

  def validate_flux_ownership_record
    super
    spec = @flux_ownership_helmrelease.fetch('spec')
    chart = spec.dig('chart', 'spec')
    ensure_true(chart.is_a?(Hash) && chart['chart'] == './k8Deployment/kubernetes/helm/minio' &&
                chart['reconcileStrategy'] == 'Revision' &&
                chart['sourceRef'] == { 'kind' => 'GitRepository', 'name' => 'flux-system', 'namespace' => 'flux-system' } &&
                chart['valuesFiles'] == [VALUES_FILE] && chart['ignoreMissingValuesFiles'] == false &&
                spec.fetch('values', {}).empty? && spec.fetch('valuesFrom', []).empty?,
                'MinIO Flux chart source or values differs from the reviewed configuration')
    ensure_true(spec['serviceAccountName'] == 'clouddsp-minio-helm' &&
                spec['driftDetection'] == { 'mode' => 'enabled' } && spec['dependsOn'] == [{ 'name' => 'clouddsp-rabbitmq', 'namespace' => 'flux-system' }],
                'MinIO Flux delivery identity, drift policy or dependencies differ')
    # Safety settings are part of the reviewed lifecycle boundary too. Reject
    # force, rollback/uninstall remediation, hooks, or hidden values overrides.
    reviewed = YAML.load_file(self.class::ROOT.join('gitops/clusters/clouddsp-local/minio/helmrelease.yaml')).fetch('spec')
    # The HelmChart API defaults version to '*' even for a Git chart, where
    # Revision packaging uses Chart.yaml and the source SHA. Accept exactly
    # that API default; native revision validation still pins the base version.
    reviewed.fetch('chart').fetch('spec')['version'] ||= '*'
    ensure_true(spec == reviewed, 'MinIO Flux spec differs from the reviewed lifecycle configuration')
  end
end
