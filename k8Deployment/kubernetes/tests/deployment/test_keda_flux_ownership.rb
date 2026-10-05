# Test the real KEDA stage and historical installer with controlled executable
# responses. Ownership failures must stop delivery before any native Helm write;
# Ready Flux alone must not bypass the original native version/values checks.
require 'minitest/autorun'
require 'stringio'
require 'tmpdir'
require 'fileutils'
require 'shellwords'
require_relative '../../scripts/releases/keda-release-stage'

class KedaFluxOwnershipTest < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  def setup
    @calls = []
    @output = StringIO.new
    @error = StringIO.new
    @crd = true
    @failure = nil
    @native_chart = 'keda-2.20.2'
    @native_values = KedaFluxOwnership.chart_values(YAML.load_file(KedaReleaseStage::ROOT.join('helm/keda/values.yaml')))
    @record = ready(YAML.load_file(KedaFluxOwnership::DIRECTORY.join('helmrelease.yaml')))
    @record['status'].merge!('storageNamespace' => 'keda', 'lastAttemptedRevision' => '2.20.2',
                             'helmChart' => 'flux-system/flux-system-keda')
    @repository = ready(YAML.load_file(KedaFluxOwnership::DIRECTORY.join('helmrepository.yaml')))
    @chart = ready('kind' => 'HelmChart', 'metadata' => { 'name' => 'flux-system-keda', 'namespace' => 'flux-system' },
                   'spec' => { 'chart' => 'keda', 'version' => '2.20.2', 'reconcileStrategy' => 'ChartVersion',
                               'sourceRef' => { 'kind' => 'HelmRepository', 'name' => 'kedacore' } })
    @chart['status']['artifact'] = { 'revision' => '2.20.2' }
  end

  def ready(object)
    object['metadata']['generation'] = 1
    object['status'] = { 'observedGeneration' => 1,
                         'conditions' => [{ 'type' => 'Ready', 'status' => 'True', 'observedGeneration' => 1 }] }
    object
  end

  def command(*args)
    @calls << args
    return ['', '', Status.new(1)] if @failure && args.include?(@failure)
    return ['{bad-json', '', Status.new(0)] if @failure == :json && args.include?('helmrelease.helm.toolkit.fluxcd.io/keda')
    response = case
               when args.include?('customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io')
                 @crd ? "crd/helmreleases.helm.toolkit.fluxcd.io\n" : ''
               when args.include?('helmrelease.helm.toolkit.fluxcd.io/keda') then @record ? JSON.generate(@record) : ''
               when args.include?('helmrepository.source.toolkit.fluxcd.io/kedacore') then JSON.generate(@repository)
               when args.include?('helmchart.source.toolkit.fluxcd.io/flux-system-keda') then JSON.generate(@chart)
               when args.first == 'helm' && args.include?('list')
                 JSON.generate([{ 'name' => 'keda', 'namespace' => 'keda', 'chart' => @native_chart, 'status' => 'deployed' }])
               when args.first == 'helm' && args.include?('values') then YAML.dump(@native_values)
               when args.include?('namespace') then "namespace/keda\n"
               when args.include?('crds')
                 JSON.generate('items' => KedaReleaseStage::CRDS.map { |n| { 'metadata' => { 'name' => n } } })
               when args.include?('apiservice') then "apiservice.apiregistration.k8s.io/v1beta1.external.metrics.k8s.io\n"
               when args.include?('rollout') then ''
               else flunk "Unexpected command #{args.inspect}"
               end
    [response, '', Status.new(0)]
  end

  def stage(mode = 'verify')
    KedaReleaseStage.new(command: method(:command), output: @output, error: @error).run(mode)
  end

  def no_write
    refute @calls.any? { |args| args.first == 'bash' || (args.first == 'helm' && args.any? { |a| %w[install upgrade rollback uninstall].include?(a) }) }
  end

  def rejected
    assert_equal 1, stage, @error.string
    no_write
  end

  def test_ready_flux_still_verifies_native_chart_values_controllers_and_crds
    assert_equal 0, stage, @error.string
    assert_equal 3, @calls.count { |args| args.include?('rollout') }
    assert @calls.any? { |args| args.first == 'helm' && args.include?('values') }
    assert @calls.any? { |args| args.include?('crds') }
    assert @calls.any? { |args| args.include?('helmchart.source.toolkit.fluxcd.io/flux-system-keda') }
    no_write
  end

  def test_presence_reserves_both_native_modes_even_when_unhealthy
    %w[plan install].each do |mode|
      [:ready, :failed, :suspended, :deleting].each do |state|
        setup
        @record['status']['conditions'].first['status'] = 'False' if state == :failed
        @record['spec']['suspend'] = true if state == :suspended
        @record['metadata']['deletionTimestamp'] = '2026-10-05T00:00:00Z' if state == :deleting
        assert_equal 1, stage(mode)
        assert_includes @error.string, 'owned by Flux HelmRelease flux-system/keda'
        no_write
        refute @calls.any? { |args| args.first == 'helm' }
      end
    end
  end

  def test_api_errors_and_invalid_json_fail_closed_in_all_modes
    ['customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io', 'helmrelease.helm.toolkit.fluxcd.io/keda', :json].each do |failure|
      %w[plan install verify].each do |mode|
        setup
        @failure = failure
        assert_equal 1, stage(mode)
        no_write
      end
    end
  end

  def test_exact_delivery_spec_blocks_namespace_source_values_and_policy_changes
    mutations = [
      ->(s) { s['releaseName'] = 'another-release' },
      ->(s) { s['targetNamespace'] = 'clouddsp-app' },
      ->(s) { s['storageNamespace'] = 'flux-system' },
      ->(s) { s['serviceAccountName'] = 'default' },
      ->(s) { s['chart']['spec']['version'] = '*' },
      ->(s) { s['chart']['spec']['sourceRef']['name'] = 'another-repository' },
      ->(s) { s['values']['watchNamespace'] = '' },
      ->(s) { s['valuesFrom'] = [{ 'kind' => 'Secret', 'name' => 'override' }] },
      ->(s) { s['driftDetection']['ignore'].first['paths'] = [''] },
      ->(s) { s['postRenderers'] = [] },
      ->(s) { s['upgrade']['force'] = true },
      ->(s) { s['upgrade']['remediation']['remediateLastFailure'] = true }
    ]
    mutations.each do |mutate|
      setup
      mutate.call(@record['spec'])
      rejected
      assert_includes @error.string, 'configuration differs'
    end
  end

  def test_stale_ready_suspended_deleting_or_wrong_revision_never_pass
    changes = [
      ->(r) { r['status']['observedGeneration'] = 0 },
      ->(r) { r['status']['conditions'].first['observedGeneration'] = 0 },
      ->(r) { r['status']['conditions'].first['status'] = 'False' },
      ->(r) { r['spec']['suspend'] = true },
      ->(r) { r['metadata']['deletionTimestamp'] = '2026-10-05T00:00:00Z' },
      ->(r) { r['status']['storageNamespace'] = 'flux-system' },
      ->(r) { r['status']['lastAttemptedRevision'] = '2.21.0' },
      ->(r) { r['status']['helmChart'] = 'flux-system/wrong-chart' }
    ]
    changes.each do |change|
      setup
      change.call(@record)
      rejected
    end
  end

  def test_upstream_repository_and_artifact_must_be_current_and_exact
    [
      -> { @repository['spec']['url'] = 'https://example.invalid/charts' },
      -> { @repository['spec']['secretRef'] = { 'name' => 'unexpected' } },
      -> { @repository['status']['conditions'].first['observedGeneration'] = 0 },
      -> { @chart['status']['artifact']['revision'] = '2.21.0' },
      -> { @chart['spec']['sourceRef']['name'] = 'another-repository' },
      -> { @chart['spec']['valuesFiles'] = ['another-values.yaml'] },
      -> { @chart['status']['conditions'].first['status'] = 'False' }
    ].each do |change|
      setup
      change.call
      rejected
    end
  end

  def test_native_version_and_value_drift_remain_failures_after_flux_ready
    @native_chart = 'keda-2.21.0'
    rejected
    assert_includes @error.string, 'differs from lock'
    setup
    @native_values['watchNamespace'] = 'wrong'
    rejected
    assert_includes @error.string, 'values differ'
  end

  def test_explicit_unsuspended_api_defaults_are_accepted
    @record['spec']['suspend'] = false
    @repository['spec']['type'] = 'default'
    @repository['spec']['provider'] = 'generic'
    assert_equal 0, stage, @error.string
    no_write
  end

  def test_native_read_only_verification_supports_crd_or_record_absence
    [false, true].each do |present|
      setup
      @crd = present
      @record = nil
      @native_values = YAML.load_file(KedaReleaseStage::ROOT.join('helm/keda/values.yaml'))
      assert_equal 0, stage, @error.string
      no_write
    end
  end

  def test_historical_shell_installer_stops_before_helm_for_flux_and_api_errors
    Dir.mktmpdir('keda-installer-guard') do |directory|
      marker = File.join(directory, 'helm-called')
      File.write(File.join(directory, 'helm'), "#!/bin/sh\ntouch '#{marker}'\nexit 0\n")
      FileUtils.chmod(0o755, File.join(directory, 'helm'))
      [JSON.generate(@record), :failure].each do |reply|
        response_file = File.join(directory, 'record.json')
        File.write(response_file, reply.to_s)
        response_command = reply == :failure ? 'exit 1' : "cat #{Shellwords.escape(response_file)}"
        kubectl = <<~SH
          #!/bin/sh
          case "$*" in
            'config get-contexts --output=name') echo k3d-clouddsp-local ;;
            *customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io*) echo crd/helmreleases.helm.toolkit.fluxcd.io ;;
            *helmrelease.helm.toolkit.fluxcd.io/keda*) #{response_command} ;;
            *) exit 2 ;;
          esac
        SH
        File.write(File.join(directory, 'kubectl'), kubectl)
        FileUtils.chmod(0o755, File.join(directory, 'kubectl'))
        _, error, status = Open3.capture3({ 'PATH' => "#{directory}:#{ENV.fetch('PATH')}" },
                                         'bash', CloudDSPPaths.script('install-keda.sh').to_s)
        refute status.success?, error
        assert_includes error, 'KEDA native delivery stopped'
        refute File.exist?(marker), 'Historical installer reached Helm despite ownership failure'
      end
    end
  end
end
