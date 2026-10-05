# Exercise the real standalone authentication runner and Flux adapter. Replace
# only executable responses; source/render/stored/live parity, shared KEDA
# dependencies, Secret-name checks, and direct-write guards still run normally.
require 'minitest/autorun'
require 'minitest/mock'
require_relative '../../scripts/releases/scaling-auth-release'

class ScalingAuthFluxOwnershipTest < Minitest::Test
  REVISION = '0.1.0+abcdef123456.1'.freeze

  module ExternalResponses
    attr_accessor :record, :crd_present, :api_error, :manifest_change, :live_change,
                  :native_chart, :native_namespace, :native_status, :origin_labels,
                  :missing_secret, :render_change, :missing_release,
                  :missing_objects, :missing_scalers
    attr_reader :commands

    def prepare
      @record = YAML.load_file(ScalingAuthRelease::ROOT.join('gitops/clusters/clouddsp-local/scaling-auth/helmrelease.yaml'))
      @record['metadata']['generation'] = 1
      @record['status'] = {
        'observedGeneration' => 1, 'storageNamespace' => 'clouddsp-app',
        'lastAttemptedRevision' => REVISION,
        'conditions' => [{ 'type' => 'Ready', 'status' => 'True', 'observedGeneration' => 1 }]
      }
      @commands = []
      @crd_present = true
      @native_chart = "scaling-auth-#{REVISION}"
      @native_namespace = 'clouddsp-app'
      @native_status = 'deployed'
      @origin_labels = FluxOwnership::BINDINGS.fetch('scaling-auth').fetch(:origin_labels).dup
      @plain = ScalingAuthRelease::SOURCE_FILES.map do |file|
        YAML.load_file(ScalingAuthRelease::ROOT.join('helm/keda', file))
      end
      @plain.each { |object| object.dig('metadata', 'labels')['app.kubernetes.io/managed-by'] = 'Helm' }
      self
    end

    def manual
      @record = nil
      @native_chart = 'scaling-auth-0.1.0'
      @origin_labels = {}
      self
    end

    private

    def stored
      Marshal.load(Marshal.dump(@plain)).each do |object|
        object.dig('metadata', 'labels').merge!(@origin_labels)
      end
    end

    def live(kind)
      if kind == 'triggerauthentication'
        data = stored.each do |object|
          name = object.dig('metadata', 'name')
          object['metadata'].merge!('uid' => "original-#{name}", 'generation' => 1,
                                   'finalizers' => ['finalizer.keda.sh'],
                                   'annotations' => { 'meta.helm.sh/release-name' => 'clouddsp-scaling-auth',
                                                      'meta.helm.sh/release-namespace' => 'clouddsp-app' })
          users = ScalingAuthRelease::SCALERS.select { |_, c| c['authentication'].include?(name) }.keys
          object['status'] = { 'scaledobjects' => users.join(',') }
        end
      else
        data = ScalingAuthRelease::SCALERS.map do |name, config|
          worker = config.fetch('worker')
          case kind
          when 'scaledobject'
            { 'kind' => 'ScaledObject', 'metadata' => { 'name' => name, 'uid' => "scaler-#{name}" },
              'spec' => { 'scaleTargetRef' => { 'name' => worker },
                          'triggers' => config['authentication'].map { |ref| { 'authenticationRef' => { 'name' => ref } } } },
              'status' => { 'conditions' => [{ 'type' => 'Ready', 'status' => 'True' }] } }
          when 'hpa'
            { 'metadata' => { 'name' => "keda-hpa-#{name}", 'uid' => "hpa-#{name}",
                              'ownerReferences' => [{ 'kind' => 'ScaledObject', 'uid' => "scaler-#{name}" }] },
              'spec' => { 'scaleTargetRef' => { 'name' => worker } } }
          when 'deployment'
            { 'metadata' => { 'name' => worker, 'uid' => "worker-#{name}" } }
          else
            raise "unexpected collection #{kind}"
          end
        end
      end
      @live_change&.call(kind, data)
      data
    end

    def command(*args, stdin_data: nil)
      @commands << args
      return "k3d-clouddsp-local\n" if args.first(3) == %w[kubectl config get-contexts]
      if args.include?('customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io')
        raise 'Flux API lookup failed' if @api_error == :crd
        return @crd_present ? "crd/helmreleases.helm.toolkit.fluxcd.io\n" : ''
      end
      if args.include?('helmrelease.helm.toolkit.fluxcd.io/clouddsp-scaling-auth')
        raise 'Flux API lookup failed' if @api_error == :helmrelease
        return '{invalid-json' if @api_error == :json
        return @record ? JSON.generate(@record) : ''
      end
      return '' if args.first(2) == %w[helm lint]
      if args.first(2) == %w[helm template]
        data = Marshal.load(Marshal.dump(@plain))
        @render_change&.call(data)
        return data.map(&:to_yaml).join
      end
      return '' if args.include?('--dry-run=server')
      if args.first == 'helm' && args.include?('list')
        return JSON.generate([{ 'name' => 'keda', 'status' => 'deployed', 'chart' => 'keda-2.20.2' }]) if args.include?('keda')
        return '[]' if @missing_release
        return JSON.generate([{ 'name' => 'clouddsp-scaling-auth', 'namespace' => @native_namespace,
                                'status' => @native_status, 'chart' => @native_chart }])
      end
      if args.first == 'helm' && args.include?('manifest')
        data = stored
        @manifest_change&.call(data)
        return data.map(&:to_yaml).join
      end
      if args.first == 'kubectl' && args.any? { |arg| arg.start_with?('secret/') }
        raise 'expected observer Secret absent' if @missing_secret
        raise 'Secret payload read attempted' unless args.include?('--output=name')
        return args.select { |arg| arg.start_with?('secret/') }.join("\n")
      end
      if args.first == 'kubectl' && args.include?('--ignore-not-found')
        return '' if @missing_objects && args.any? { |arg| arg.start_with?('triggerauthentication/') }
        return '' if @missing_scalers && args.any? { |arg| arg.start_with?('scaledobject/') }
        return "present\n"
      end
      if args.first == 'kubectl' && args.include?('get')
        resource = args.find { |arg| arg.match?(/\A(triggerauthentication|scaledobject|hpa|deployment)\//) }
        return JSON.generate('kind' => 'List', 'items' => live(resource.split('/').first)) if resource
      end
      return '' if args.first == 'ruby'
      raise 'authorized native Helm write reached' if args.first == 'helm' && %w[install upgrade].include?(args[1])
      raise "unexpected executable response: #{args.inspect}"
    end
  end

  def runner
    CloudDSPScalingAuthRelease.build.extend(ExternalResponses).prepare
  end

  def run_release(stage, mode = 'verify')
    status = nil
    output, error = capture_io do
      File.stub(:executable?, true) do
        begin
          stage.run(mode)
        rescue SystemExit => exception
          status = exception.status
        end
      end
    end
    [status, output, error]
  end

  def rejected(stage, message, mode = 'verify')
    status, _, error = run_release(stage, mode)
    assert_equal 1, status
    assert_includes error, message
    refute stage.commands.any? { |args| args.first == 'helm' && %w[install upgrade].include?(args[1]) }
  end

  def test_ready_handoff_preserves_all_original_dependency_and_secret_name_checks
    stage = runner
    status, output, error = run_release(stage)
    assert_nil status, error
    assert_includes output, 'three Ready scalers, HPAs, and worker targets verified'
    %w[triggerauthentication scaledobject hpa deployment secret].each do |kind|
      assert stage.commands.any? { |args| args.any? { |arg| arg.start_with?(kind+'/') } }
    end
    refute stage.commands.any? { |args| args.first == 'helm' && %w[install upgrade].include?(args[1]) }
  end

  def test_existence_reserves_install_and_adopt_even_when_flux_is_unhealthy
    %w[install adopt].each do |mode|
      [:ready, :failed, :suspended, :deleting].each do |state|
        stage = runner
        stage.record['status']['conditions'].first['status'] = 'False' if state == :failed
        stage.record['spec']['suspend'] = true if state == :suspended
        stage.record['metadata']['deletionTimestamp'] = '2026-10-05T00:00:00Z' if state == :deleting
        rejected(stage, 'owned by Flux HelmRelease flux-system/clouddsp-scaling-auth', mode)
        refute stage.commands.any? { |args| args.first(2) == %w[helm lint] || args.first == 'ruby' }
      end
    end
  end

  def test_lookup_errors_never_authorize_native_writes
    [:crd, :helmrelease, :json].each do |failure|
      %w[install adopt reconcile verify].each do |mode|
        stage = runner
        stage.api_error = failure
        status, _, error = run_release(stage, mode)
        assert_equal 1, status, error
        refute stage.commands.any? { |args| args.first == 'helm' && %w[lint install upgrade].include?(args[1]) }
      end
    end
  end

  def test_exact_release_storage_and_ready_generation_are_required
    changes = [
      ->(r) { r['spec']['releaseName'] = 'another-release' },
      ->(r) { r['spec']['targetNamespace'] = 'clouddsp-data' },
      ->(r) { r['spec']['storageNamespace'] = 'flux-system' },
      ->(r) { r['status']['storageNamespace'] = 'flux-system' },
      ->(r) { r['status']['observedGeneration'] = 0 },
      ->(r) { r['status']['conditions'].first['observedGeneration'] = 0 },
      ->(r) { r['spec']['suspend'] = true },
      ->(r) { r['metadata']['deletionTimestamp'] = '2026-10-05T00:00:00Z' }
    ]
    changes.each do |change|
      stage = runner
      change.call(stage.record)
      status, _, error = run_release(stage, 'reconcile')
      assert_equal 1, status, error
      refute stage.commands.any? { |args| args.first == 'helm' && args[1] == 'upgrade' }
    end
  end

  def test_source_and_values_overrides_are_rejected_before_rendering
    changes = [
      ->(r) { r['spec']['chart']['spec']['chart'] = './another-chart' },
      ->(r) { r['spec']['chart']['spec']['sourceRef']['name'] = 'another-source' },
      ->(r) { r['spec']['chart']['spec']['valuesFiles'] << 'another-values.yaml' },
      ->(r) { r['spec']['values'] = { 'password' => 'test-only-placeholder' } },
      ->(r) { r['spec']['valuesFrom'] = [{ 'kind' => 'Secret', 'name' => 'another-secret' }] }
    ]
    changes.each do |change|
      stage = runner
      change.call(stage.record)
      rejected(stage, 'chart source or values differs')
      refute stage.commands.any? { |args| args.first(2) == %w[helm lint] }
    end
  end

  def test_native_revision_namespace_and_stored_manifest_stay_exact
    %w[0.2.0+abcdef123456.1 0.1.0 0.1.0+abcdef123456.0].each do |revision|
      stage = runner
      stage.record['status']['lastAttemptedRevision'] = revision
      rejected(stage, 'Flux chart revision differs')
    end
    stage = runner; stage.native_chart = 'scaling-auth-0.1.0'; rejected(stage, 'Helm release is not deployed')
    stage = runner; stage.native_namespace = 'flux-system'; rejected(stage, 'native Helm release namespace')
    stage = runner; stage.native_status = 'failed'; rejected(stage, 'Helm release is not deployed')
    stage = runner
    stage.manifest_change = ->(data) { data.first['spec']['secretTargetRef'].first['name'] = 'wrong-secret' }
    rejected(stage, 'installed release manifest differs')
  end

  def test_platform_dependency_cannot_be_removed_or_redirected
    [[], [{ 'name' => 'another-platform', 'namespace' => 'flux-system' }]].each do |dependencies|
      stage = runner
      stage.record['spec']['dependsOn'] = dependencies
      rejected(stage, 'dependencies must be the reviewed KEDA and RabbitMQ HelmReleases')
    end
  end

  def test_flux_ready_does_not_relax_source_render_parity
    stage = runner
    stage.render_change = ->(data) { data.first['spec']['secretTargetRef'].first['name'] = 'wrong-secret' }
    rejected(stage, 'differs from source')
  end

  def test_authentication_ownership_finalizers_and_references_stay_exact
    changes = [
      ->(a) { a['metadata']['labels']['unexpected'] = 'label' },
      ->(a) { a['metadata']['annotations']['meta.helm.sh/release-name'] = 'another-release' },
      ->(a) { a['metadata']['ownerReferences'] = [{ 'kind' => 'Deployment' }] },
      ->(a) { a['metadata']['finalizers'] = [] },
      ->(a) { a['spec']['secretTargetRef'].first['key'] = 'wrong-key' },
      ->(a) { a['status']['scaledobjects'] = '' }
    ]
    changes.each do |change|
      stage = runner
      stage.live_change = ->(kind, data) { change.call(data.first) if kind == 'triggerauthentication' }
      status, _, error = run_release(stage)
      assert_equal 1, status, error
    end
    stage = runner; stage.missing_secret = true; rejected(stage, 'observer Secret absent')
  end

  def test_worker_scaler_readiness_authentication_and_hpa_ownership_remain_checked
    changes = {
      'scaledobject' => [
        ->(s) { s['status']['conditions'].first['status'] = 'False' },
        ->(s) { s['metadata']['annotations'] = { 'autoscaling.keda.sh/paused' => 'true' } },
        ->(s) { s['spec']['triggers'].first['authenticationRef']['name'] = 'wrong-authentication' }
      ],
      'hpa' => [->(s) { s['metadata']['ownerReferences'].first['uid'] = 'wrong-scaler' }],
      'deployment' => [->(s) { s['metadata'].delete('uid') }]
    }
    changes.each do |kind, mutations|
      mutations.each do |change|
        stage = runner
        stage.live_change = ->(k, data) { change.call(data.first) if k == kind }
        status, _, error = run_release(stage)
        assert_equal 1, status, error
      end
    end
  end

  def test_flux_maintenance_verifies_twice_without_upgrading_shared_authentication
    stage = runner
    status, output, error = run_release(stage, 'reconcile')
    assert_nil status, error
    assert_includes output, 'without a direct Helm upgrade'
    assert_equal 2, stage.commands.count { |args| args.first == 'helm' && args.include?('manifest') }
    refute stage.commands.any? { |args| args.first == 'helm' && args[1] == 'upgrade' }
  end

  def test_absence_keeps_native_verification_reconciliation_and_fresh_install_gates
    [false, true].each do |crd_present|
      stage = runner.manual; stage.crd_present = crd_present
      status, _, error = run_release(stage)
      assert_nil status, error
      stage = runner.manual; stage.crd_present = crd_present
      status, _, error = run_release(stage, 'reconcile')
      assert_equal 1, status
      assert_includes error, 'authorized native Helm write reached'
      stage = runner.manual; stage.crd_present = crd_present
      stage.missing_release = stage.missing_objects = stage.missing_scalers = true
      status, _, error = run_release(stage, 'install')
      assert_equal 1, status
      assert_includes error, 'authorized native Helm write reached'
      assert_equal %w[rabbitmq database], stage.commands.select { |args| args.first == 'ruby' }.map { |args| args[2] }
    end
  end
end
