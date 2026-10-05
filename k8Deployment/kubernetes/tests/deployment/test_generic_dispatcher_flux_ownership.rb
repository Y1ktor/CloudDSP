# Exercise generic-dispatcher's actual runner without cluster mutations. Only CLI
# responses are replaced; strict source/render/stored/live parity, running
# digest, Pod readiness, and Flux ownership checks execute production code.
require 'minitest/autorun'
require_relative '../../scripts/releases/generic-dispatcher-release'

class GenericDispatcherFluxOwnershipTest < Minitest::Test
  REVISION = '0.1.0+abcdef123456.1'.freeze

  module ExternalResponses
    attr_accessor :record, :api_failure, :native_chart, :origin_labels,
                  :live_change, :manifest_change, :pod_digest, :pod_ready,
                  :release_present, :objects_present, :paused, :remaining_pods
    attr_reader :commands

    def prepare
      @commands = []
      @record = {
        'kind' => 'HelmRelease',
        'metadata' => { 'name' => 'clouddsp-generic-dispatcher', 'namespace' => 'flux-system', 'generation' => 1 },
        'spec' => { 'releaseName' => 'clouddsp-generic-dispatcher', 'targetNamespace' => 'clouddsp-app',
                    'storageNamespace' => 'clouddsp-app', 'chart' => { 'spec' => { 'valuesFiles' => [GenericDispatcherFluxOwnership::VALUES_FILE] } } },
        'status' => { 'observedGeneration' => 1, 'storageNamespace' => 'clouddsp-app',
                      'lastAttemptedRevision' => REVISION,
                      'conditions' => [{ 'type' => 'Ready', 'status' => 'True', 'observedGeneration' => 1 }] }
      }
      @release_present = @objects_present = @pod_ready = true
      @native_chart = "generic-dispatcher-#{REVISION}"
      @origin_labels = GenericDispatcherFluxOwnership::ORIGIN_LABELS.dup
      @plain = YAML.load_file(HelmRelease::ROOT.join('services', 'dispatcher', 'dispatcher-generic-deployment.yaml'))
      @plain.dig('metadata', 'labels')['app.kubernetes.io/managed-by'] = 'Helm'
      @pod_digest = @plain.dig('spec', 'template', 'spec', 'containers').first.fetch('image')
      self
    end

    def object
      Marshal.load(Marshal.dump(@plain)).tap do |o|
        o.dig('metadata', 'labels').merge!(@origin_labels)
        o['spec']['replicas'] = 0 if @paused
      end
    end

    private

    def command(*args, stdin_data: nil)
      @commands << args
      return "k3d-clouddsp-local\n" if args.first(3) == %w[kubectl config get-contexts]
      if args.include?('customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io')
        raise 'Flux API failed' if @api_failure == :crd
        return "customresourcedefinition.apiextensions.k8s.io/helmreleases.helm.toolkit.fluxcd.io\n"
      end
      if args.include?('helmrelease.helm.toolkit.fluxcd.io/clouddsp-generic-dispatcher')
        raise 'Flux API failed' if @api_failure == :helmrelease
        return @record ? JSON.generate(@record) : ''
      end
      return 'lint passed' if args.first(2) == %w[helm lint]
      if args.first(2) == %w[helm template]
        data = Marshal.load(Marshal.dump(@plain))
        data['spec']['replicas'] = 0 if args.include?('--values')
        return data.to_yaml
      end
      return '' if args.include?('--dry-run=server')
      if args.first == 'helm' && args.include?('list')
        return @release_present ? JSON.generate([{ 'name' => 'clouddsp-generic-dispatcher', 'namespace' => 'clouddsp-app',
                                                  'status' => 'deployed', 'chart' => @native_chart }]) : '[]'
      end
      if args.first == 'helm' && args.include?('manifest')
        data = object
        @manifest_change&.call(data)
        return data.to_yaml
      end
      if args.include?('pods') && args.include?('get')
        return JSON.generate('items' => @remaining_pods || []) if @paused
        return JSON.generate('items' => [{ 'metadata' => { 'uid' => 'original-publisher-pod' },
                                          'status' => { 'conditions' => [{ 'type' => 'Ready', 'status' => @pod_ready ? 'True' : 'False' }],
                                                        'containerStatuses' => [{ 'imageID' => @pod_digest }] } }])
      end
      return @objects_present ? "present\n" : '' if args.first == 'kubectl' && args.include?('--ignore-not-found')
      return '' if args.first == 'ruby'
      raise 'authorized manual Helm write reached' if args.first == 'helm' && %w[install upgrade].include?(args[1])
      raise "unexpected command: #{args.inspect}"
    end

    def monotonic_time
      @clock = (@clock || 0) + 61
    end

    def live_objects
      data = object
      data['metadata']['annotations'] = { 'meta.helm.sh/release-name' => 'clouddsp-generic-dispatcher',
                                          'meta.helm.sh/release-namespace' => 'clouddsp-app' }
      count = @paused ? 0 : 1
      data['status'] = { 'replicas' => count, 'readyReplicas' => count }
      @live_change&.call(data)
      indexed([data])
    end
  end

  def runner
    CloudDSPGenericDispatcherRelease.build.extend(ExternalResponses).prepare
  end

  def execute(publisher, mode = 'verify')
    status = nil
    output, error = capture_io do
      begin
        publisher.run(mode)
      rescue SystemExit => stopped
        status = stopped.status
      end
    end
    [status, output, error]
  end

  def rejected(publisher, expected, mode = 'verify')
    status, _output, error = execute(publisher, mode)
    assert_equal 1, status
    assert_includes error, expected
  end

  def test_ready_flux_release_retains_native_spec_and_runtime_checks
    publisher = runner
    status, output, error = execute(publisher)
    assert_nil status, error
    assert_includes output, 'Pod readiness, and running image digest verified'
    assert_includes publisher.singleton_class.ancestors, GenericDispatcherFluxOwnership
  end

  def test_presence_blocks_direct_writes_before_chart_and_all_dependencies
    %w[install adopt].each do |mode|
      [:ready, :failed, :suspended, :deleting].each do |state|
        publisher = runner
        publisher.record['status']['conditions'].first['status'] = 'False' if state == :failed
        publisher.record['spec']['suspend'] = true if state == :suspended
        publisher.record['metadata']['deletionTimestamp'] = '2026-10-05T00:00:00Z' if state == :deleting
        rejected(publisher, 'owned by Flux HelmRelease flux-system/clouddsp-generic-dispatcher', mode)
        assert publisher.commands.all? { |command| command.first == 'kubectl' }
      end
    end
  end

  def test_exact_helmrelease_identity_storage_and_current_generation_are_required
    publisher = runner
    publisher.record['metadata']['name'] = 'clouddsp-job-api'
    rejected(publisher, 'unexpected HelmRelease identity')
    %w[releaseName targetNamespace storageNamespace].each do |field|
      publisher = runner
      publisher.record['spec'][field] = 'wrong'
      rejected(publisher, "spec.#{field} must be")
    end
    publisher = runner
    publisher.record['status']['conditions'].first['observedGeneration'] = 0
    rejected(publisher, 'not Ready for its current generation')
    publisher = runner
    publisher.record['status']['storageNamespace'] = 'flux-system'
    rejected(publisher, 'status.storageNamespace must be clouddsp-app')
  end

  def test_reviewed_chart_base_and_exact_native_revision_are_required
    publisher = runner
    publisher.record['status']['lastAttemptedRevision'] = '0.1.1+abcdef123456.1'
    rejected(publisher, 'Flux chart revision differs from the reviewed chart version')
    publisher = runner
    publisher.native_chart = 'generic-dispatcher-0.1.0+123456abcdef.1'
    rejected(publisher, 'expected generic-dispatcher Helm chart/release is not deployed')
  end

  def test_origin_labels_secret_references_and_pod_template_are_still_strict
    publisher = runner
    publisher.origin_labels['helm.toolkit.fluxcd.io/name'] = 'clouddsp-job-api'
    rejected(publisher, 'installed generic-dispatcher release manifest differs')
    publisher = runner
    publisher.manifest_change = ->(object) { object.dig('spec', 'template', 'spec', 'containers').first['image'] = 'wrong-image' }
    rejected(publisher, 'installed generic-dispatcher release manifest differs')
    publisher = runner
    publisher.live_change = lambda do |object|
      env = object.dig('spec', 'template', 'spec', 'containers').first['env']
      env.find { |item| item['valueFrom'] }.dig('valueFrom', 'secretKeyRef')['name'] = 'wrong-secret'
    end
    rejected(publisher, 'spec differs from source')
    publisher = runner
    publisher.live_change = ->(object) { object.dig('spec', 'template', 'metadata', 'labels').merge!(GenericDispatcherFluxOwnership::ORIGIN_LABELS) }
    rejected(publisher, 'spec differs from source')
  end

  def test_running_digest_and_ready_pod_remain_required
    publisher = runner
    publisher.pod_digest = 'sha256:wrong'
    rejected(publisher, 'running Pod imageID does not match its locked digest')
    publisher = runner
    publisher.pod_ready = false
    rejected(publisher, 'Pod is not Ready')
  end

  def test_api_failures_never_authorize_direct_install_adopt_or_verification
    [:crd, :helmrelease].each do |failure|
      %w[verify install adopt].each do |mode|
        publisher = runner
        publisher.api_failure = failure
        rejected(publisher, 'Flux API failed', mode)
        assert publisher.commands.all? { |command| command.first == 'kubectl' }
      end
    end
  end

  def test_absent_helmrelease_preserves_manual_verification_and_three_fresh_gates
    publisher = runner
    publisher.record = nil
    publisher.native_chart = 'generic-dispatcher-0.1.0'
    publisher.origin_labels = {}
    status, _output, error = execute(publisher)
    assert_nil status, error
    publisher.release_present = publisher.objects_present = false
    rejected(publisher, 'authorized manual Helm write reached', 'install')
    dependencies = publisher.commands.select { |command| command.first == 'ruby' }
    assert_equal %w[application-identity-stage.rb application-identity-stage.rb job-api-release.rb],
                 dependencies.map { |command| File.basename(command[1]) }
    assert_equal %w[database dispatcher verify], dependencies[0].last(3)
    assert_equal %w[rabbitmq dispatcher verify], dependencies[1].last(3)
    assert dependencies.all? { |command| command.last == 'verify' }
  end
  def paused_runner
    runner.tap do |publisher|
      publisher.paused = true
      publisher.record.dig('spec', 'chart', 'spec', 'valuesFiles') << GenericDispatcherFluxOwnership::PAUSE_FILE
    end
  end

  def test_pause_is_a_separate_read_only_exact_values_and_zero_pod_check
    publisher = paused_runner
    status, output, error = execute(publisher, 'verify-smoke-pause')
    assert_nil status, error
    assert_includes output, 'no publisher Pods (including terminating Pods)'
    assert publisher.commands.none? { |cmd| cmd.first == 'helm' && %w[install upgrade].include?(cmd[1]) }
    rejected(paused_runner, 'values must match', 'verify')
    rejected(runner, 'values must match', 'verify-smoke-pause')
    publisher = runner
    publisher.record = nil
    rejected(publisher, 'requires the active Flux HelmRelease', 'verify-smoke-pause')
  end

  def test_inline_values_external_values_and_extra_files_are_rejected
    ['values', 'valuesFrom'].each do |field|
      publisher = paused_runner
      publisher.record['spec'][field] = field == 'values' ? { 'replicas' => 1 } : [{ 'kind' => 'Secret', 'name' => 'other' }]
      rejected(publisher, 'values must match', 'verify-smoke-pause')
    end
    publisher = paused_runner
    publisher.record.dig('spec', 'chart', 'spec', 'valuesFiles') << './other.yaml'
    rejected(publisher, 'values must match', 'verify-smoke-pause')
  end

  def test_terminating_publishers_block_smoke_even_after_scale_to_zero
    publisher = paused_runner
    publisher.remaining_pods = [{ 'metadata' => { 'deletionTimestamp' => '2026-10-05T03:00:00Z' } }]
    rejected(publisher, 'still has Pods, including terminating publishers', 'verify-smoke-pause')
  end

end
