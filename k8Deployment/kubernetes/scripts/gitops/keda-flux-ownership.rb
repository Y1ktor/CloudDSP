# Guard upstream KEDA delivery independently of the application chart adapters.
# KEDA uses an exact HelmRepository chart version, not a Git-packaged SHA version.
# The Ruby bootstrap verifier and historical shell installer share this guard so
# an unhealthy Flux record can never authorize a competing native Helm write.
require_relative '../lib/paths'
require 'json'
require 'open3'
require 'yaml'

class KedaFluxOwnership
  CONTEXT = 'k3d-clouddsp-local'.freeze
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  DIRECTORY = ROOT.join('gitops', 'clusters', 'clouddsp-local', 'keda').freeze

  def initialize(command: Open3.method(:capture3))
    @command = command
  end

  def self.chart_values(values)
    # Upstream duplicates this key when additionalLabels supplies it. Keep the
    # historical native input intact; Flux applies its existing effective label
    # with reviewed postrenderer patches after valid YAML has been rendered.
    transformed = Marshal.load(Marshal.dump(values))
    raise 'KEDA local part-of label differs from reviewed profile' unless
      transformed.fetch('additionalLabels').delete('app.kubernetes.io/part-of') == 'clouddsp'

    transformed
  end

  def record
    # Explicit CRD absence supports fresh pre-Flux bootstrap. Any API, network,
    # or permission failure propagates instead of being interpreted as absence.
    crd = command!('Flux HelmRelease API lookup', 'get',
                   'customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io',
                   '--ignore-not-found', '--output', 'name')
    return nil if crd.strip.empty?

    response = command!('KEDA Flux ownership lookup', '--namespace', 'flux-system',
                        'get', 'helmrelease.helm.toolkit.fluxcd.io/keda',
                        '--ignore-not-found', '--output', 'json')
    return nil if response.strip.empty?

    object = JSON.parse(response)
    require_identity!(object, 'HelmRelease', 'keda')
    object
  end

  def guard_native!
    # Reserve the native release as soon as the HelmRelease exists, before its
    # first reconciliation and during failed, suspended, or deleting states.
    raise 'KEDA is owned by Flux HelmRelease flux-system/keda; publish its GitOps configuration instead of a native Helm write' if record
  end

  def verify!(object, values)
    expected = YAML.load_file(DIRECTORY.join('helmrelease.yaml')).fetch('spec')
    require_true(expected.values_at('releaseName', 'targetNamespace', 'storageNamespace', 'serviceAccountName') ==
                 %w[keda keda keda clouddsp-keda-helm] &&
                 expected.dig('chart', 'spec', 'chart') == 'keda' &&
                 expected.dig('chart', 'spec', 'version') == '2.20.2' &&
                 expected.dig('chart', 'spec', 'sourceRef') ==
                   { 'kind' => 'HelmRepository', 'name' => 'kedacore', 'namespace' => 'flux-system' },
                 'KEDA Flux source must retain the pinned upstream release identity')
    require_true(expected.fetch('values') == self.class.chart_values(values),
                 'KEDA Flux public values differ from the exact reviewed local transformation')
    require_true(object.dig('spec', 'suspend') != true && !object.dig('metadata', 'deletionTimestamp'),
                 'KEDA Flux HelmRelease is suspended or deleting')
    # The API adds no defaults to this reviewed spec. Permit only an explicit
    # suspend:false; every delivery option, source, value, retention patch, and
    # narrow TLS drift exception must match the versioned handoff definition.
    actual = object.fetch('spec').reject { |key, value| key == 'suspend' && value == false }
    require_true(actual == expected, 'KEDA Flux HelmRelease configuration differs from reviewed source')
    require_ready!(object)
    version = expected.dig('chart', 'spec', 'version')
    require_true(object.dig('status', 'storageNamespace') == 'keda' &&
                 object.dig('status', 'lastAttemptedRevision') == version,
                 'KEDA Flux storage namespace or chart revision differs from lock')

    repository = read!('helmrepository.source.toolkit.fluxcd.io/kedacore')
    require_identity!(repository, 'HelmRepository', 'kedacore')
    repository_spec = repository.fetch('spec').reject { |key, value| key == 'type' && value == 'default' }
    repository_spec.delete('provider') if repository_spec['provider'] == 'generic'
    require_true(repository_spec == YAML.load_file(DIRECTORY.join('helmrepository.yaml')).fetch('spec'),
                 'KEDA Flux upstream repository differs from reviewed source')
    require_ready!(repository)

    chart_id = object.dig('status', 'helmChart')
    require_true(chart_id == 'flux-system/flux-system-keda', 'KEDA Flux HelmChart identity differs')
    chart = read!('helmchart.source.toolkit.fluxcd.io/flux-system-keda')
    require_identity!(chart, 'HelmChart', 'flux-system-keda')
    require_ready!(chart)
    chart_spec = chart.fetch('spec')
    require_true(chart_spec['chart'] == 'keda' && chart_spec['version'] == version &&
                 chart_spec['sourceRef'] == { 'kind' => 'HelmRepository', 'name' => 'kedacore' } &&
                 chart_spec['reconcileStrategy'] == 'ChartVersion' &&
                 chart_spec.fetch('valuesFiles', []).empty? &&
                 chart.dig('status', 'artifact', 'revision') == version,
                 'KEDA Flux HelmChart source, values, or artifact differs from lock')
    true
  end

  private

  def require_true(condition, message)
    raise message unless condition
  end

  def require_identity!(object, kind, name)
    require_true(object['kind'] == kind && object.dig('metadata', 'name') == name &&
                 object.dig('metadata', 'namespace') == 'flux-system',
                 "KEDA Flux #{kind} identity differs")
  end

  def require_ready!(object)
    generation = object.dig('metadata', 'generation')
    ready = object.dig('status', 'conditions').to_a.find { |condition| condition['type'] == 'Ready' }
    require_true(generation.is_a?(Integer) && generation.positive? &&
                 object.dig('status', 'observedGeneration') == generation &&
                 ready && ready['status'] == 'True' && ready['observedGeneration'] == generation,
                 "KEDA Flux #{object.fetch('kind')} is not Ready for its current generation")
  end

  def read!(resource)
    JSON.parse(command!("KEDA Flux #{resource.split('/').first} lookup", '--namespace', 'flux-system',
                        'get', resource, '--output', 'json'))
  end

  def command!(label, *arguments)
    output, _error, status = @command.call('kubectl', '--context', CONTEXT, *arguments)
    require_true(status.success?, "#{label} failed")
    output
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: keda-flux-ownership.rb guard-native' unless ARGV == ['guard-native']
  begin
    KedaFluxOwnership.new.guard_native!
  rescue StandardError => exception
    warn "KEDA native delivery stopped: #{exception.message}"
    exit 1
  end
end
