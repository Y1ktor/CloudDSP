# Opt in only the existing MinIO release to Flux. The parent retains
# source/render/stored/live parity, Ready Pod, immutable image and bound claim
# checks; buckets, IAM, notifications and objects never become chart objects.
require_relative 'flux-ownership'

module MinIOFluxOwnership
  include FluxOwnership
  VALUES_FILE = './k8Deployment/kubernetes/helm/minio/values.yaml'.freeze

  private

  # Flux's YAML serializer emits the console argument as plain :9001.
  # Kubernetes treats it as the string ":9001", but Psych interprets that
  # scalar as a Ruby Symbol and loses the leading colon. Mark only this exact
  # untagged scalar as quoted before decoding. No manifest field is ignored:
  # altered arguments, tags and all other source/stored/live drift still fail.
  def documents(yaml)
    stream = Psych.parse_stream(yaml)
    quote_console = lambda do |node|
      if node.is_a?(Psych::Nodes::Scalar) && node.tag.nil? && node.plain && node.value == ':9001'
        node.plain = false
        node.quoted = true
      end
      Array(node.children).each { |child| quote_console.call(child) }
    end
    quote_console.call(stream)
    stream.children.map(&:to_ruby).compact
  end

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
