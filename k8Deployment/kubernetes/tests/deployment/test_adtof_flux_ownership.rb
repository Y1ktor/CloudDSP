# Exercise the actual ADTOF runner with external CLI responses replaced. The
# original worker policy, source/stored/live parity, HPA ownership, idle/warm,
# and versioned smoke boundaries still execute through production code.
require 'minitest/autorun'
require 'minitest/mock'
require 'fileutils'
require 'tmpdir'
require_relative '../../scripts/releases/adtof-release'

class AdtofFluxOwnershipTest < Minitest::Test
  REVISION = '0.1.0+abcdef123456.1'.freeze

  module ExternalResponses
    attr_accessor :record, :crd_present, :api_error, :native_chart, :native_namespace,
                  :native_status, :origin_labels, :manifest_change, :live_change,
                  :hpa_change, :desired, :pod_ready, :missing_release, :missing_objects,
                  :render_change, :existing_job
    attr_reader :commands

    def prepare
      @record = YAML.load_file(HelmRelease::ROOT.join('gitops/clusters/clouddsp-local/adtof/helmrelease.yaml'))
      @record['metadata']['generation'] = 1
      @record['status'] = {
        'observedGeneration' => 1, 'storageNamespace' => 'clouddsp-app',
        'lastAttemptedRevision' => REVISION,
        'conditions' => [{ 'type' => 'Ready', 'status' => 'True', 'observedGeneration' => 1 }]
      }
      @commands = []; @crd_present = @pod_ready = true
      @desired = 0; @native_status = 'deployed'
      @native_chart = "adtof-#{REVISION}"; @native_namespace = 'clouddsp-app'
      @origin_labels = FluxOwnership::BINDINGS.fetch('adtof').fetch(:origin_labels).dup
      @plain = @source_files.map { |name| YAML.load_file(HelmRelease::ROOT.join('services/adtof', name)) }
      @plain.each { |o| o.dig('metadata', 'labels')['app.kubernetes.io/managed-by'] = 'Helm' }
      self
    end

    def manual
      @record = nil; @native_chart = 'adtof-0.1.0'; @origin_labels = {}
      self
    end

    def use_chart(path)
      @chart = Pathname.new(path)
    end

    private

    def rendered
      policy = WorkerScalingPolicy.new(YAML.load_file(@chart.join('values.yaml')))
      @plain.map { |o| policy.configured_source(o) }
    end

    def stored
      rendered.each { |o| o.dig('metadata', 'labels').merge!(@origin_labels) }
    end

    def live
      stored.each do |o|
        o['metadata'].merge!('uid' => "original-#{o['kind']}", 'generation' => 1,
                             'annotations' => { 'meta.helm.sh/release-name' => 'clouddsp-adtof',
                                                'meta.helm.sh/release-namespace' => 'clouddsp-app' })
        if o['kind'] == 'Deployment'
          o['spec']['replicas'] = @desired
          o['status'] = { 'replicas' => @desired, 'readyReplicas' => @desired, 'updatedReplicas' => @desired }
        else
          o['metadata']['labels']['scaledobject.keda.sh/name'] = AdtofRelease::SCALER
          o['status'] = { 'conditions' => [{ 'type' => 'Ready', 'status' => 'True' }] }
        end
      end.tap { |data| @live_change&.call(data) }
    end

    def command(*args, stdin_data: nil)
      @commands << args
      return "k3d-clouddsp-local\n" if args.first(3) == %w[kubectl config get-contexts]
      if args.include?('customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io')
        raise 'Flux API lookup failed' if @api_error == :crd
        return @crd_present ? "crd/helmreleases.helm.toolkit.fluxcd.io\n" : ''
      end
      if args.include?('helmrelease.helm.toolkit.fluxcd.io/clouddsp-adtof')
        raise 'Flux API lookup failed' if @api_error == :helmrelease
        return '{invalid-json' if @api_error == :json
        return @record ? JSON.generate(@record) : ''
      end
      return '' if args.first(2) == %w[helm lint]
      if args.first(2) == %w[helm template]
        data = rendered; @render_change&.call(data)
        return data.map(&:to_yaml).join
      end
      return '' if args.include?('--dry-run=server')
      if args.first == 'helm' && args.include?('list')
        return '[]' if @missing_release
        return JSON.generate([{ 'name' => 'clouddsp-adtof', 'namespace' => @native_namespace,
                                'status' => @native_status, 'chart' => @native_chart }])
      end
      if args.first == 'helm' && args.include?('manifest')
        data = stored; @manifest_change&.call(data)
        return data.map(&:to_yaml).join
      end
      if args.include?("hpa/#{AdtofRelease::HPA}")
        hpa = { 'metadata' => { 'uid' => 'original-hpa', 'ownerReferences' => [
          { 'controller' => true, 'kind' => 'ScaledObject', 'name' => AdtofRelease::SCALER, 'uid' => 'original-ScaledObject' }
        ] }, 'spec' => { 'scaleTargetRef' => { 'apiVersion' => 'apps/v1', 'kind' => 'Deployment', 'name' => 'clouddsp-adtof' } } }
        @hpa_change&.call(hpa)
        return JSON.generate(hpa)
      end
      if args.include?('pods') && args.include?('get')
        pods = Array.new(@desired) { |i| { 'metadata' => { 'uid' => "warm-#{i}" },
          'status' => { 'conditions' => [{ 'type' => 'Ready', 'status' => @pod_ready ? 'True' : 'False' }] } } }
        return JSON.generate('items' => pods)
      end
      return @missing_objects ? '' : "present\n" if args.first == 'kubectl' && args.include?('--ignore-not-found')
      if args.include?('get') && args.include?('deployment/clouddsp-adtof')
        return JSON.generate('kind' => 'List', 'items' => live)
      end
      if args.include?('jobs') && args.include?('get')
        return JSON.generate('items' => @existing_job ? [{ 'metadata' => { 'name' => 'adtof-worker-smoke' } }] : [])
      end
      return "ADTOF worker smoke: passed\n" if args.first == 'kubectl' && args.include?('logs')
      return '' if args.first == 'kubectl' && (args.include?('create') || args.include?('wait') || args.include?('delete'))
      return '' if args.first == 'ruby'
      raise 'authorized native Helm write reached' if args.first == 'helm' && %w[install upgrade].include?(args[1])
      raise "unexpected executable response: #{args.inspect}"
    end
  end

  def runner
    CloudDSPAdtofRelease.build.extend(ExternalResponses).prepare
  end

  def run_release(worker, mode = 'verify-idle')
    status = nil
    output, error = capture_io do
      File.stub(:executable?, true) do
        begin
          worker.run(mode)
        rescue SystemExit => exception
          status = exception.status
        end
      end
    end
    [status, output, error]
  end

  def rejected(worker, message, mode = 'verify-idle')
    status, _, error = run_release(worker, mode)
    assert_equal 1, status
    assert_includes error, message
    refute worker.commands.any? { |args| args.first == 'helm' && %w[install upgrade].include?(args[1]) }
  end

  def test_ready_flux_handoff_preserves_native_idle_scaler_and_hpa_gates
    worker = runner
    status, output, error = run_release(worker)
    assert_nil status, error
    assert_includes output, 'zero idle replicas'
    assert worker.commands.any? { |args| args.include?("hpa/#{AdtofRelease::HPA}") }
    assert worker.commands.any? { |args| args.include?('pods') }
  end

  def test_install_and_adopt_are_reserved_even_in_unhealthy_flux_states
    %w[install adopt].each do |mode|
      [:ready, :failed, :suspended, :deleting].each do |state|
        worker = runner
        worker.record['status']['conditions'].first['status'] = 'False' if state == :failed
        worker.record['spec']['suspend'] = true if state == :suspended
        worker.record['metadata']['deletionTimestamp'] = '2026-10-05T00:00:00Z' if state == :deleting
        rejected(worker, 'owned by Flux HelmRelease flux-system/clouddsp-adtof', mode)
        refute worker.commands.any? { |args| args.first(2) == %w[helm lint] || args.first == 'ruby' }
      end
    end
  end

  def test_api_lookup_errors_fail_closed_before_chart_or_job_creation
    [:crd, :helmrelease, :json].each do |failure|
      %w[install adopt smoke verify].each do |mode|
        worker = runner; worker.api_error = failure
        status, _, error = run_release(worker, mode)
        assert_equal 1, status, error
        refute worker.commands.any? { |args| args.include?('create') || args.first(2) == %w[helm lint] }
      end
    end
  end

  def test_native_identity_revision_and_current_ready_generation_remain_exact
    changes = [
      ->(w) { w.record['spec']['storageNamespace'] = 'flux-system' },
      ->(w) { w.record['spec']['targetNamespace'] = 'clouddsp-data' },
      ->(w) { w.record['spec']['releaseName'] = 'another-worker' },
      ->(w) { w.record['status']['storageNamespace'] = 'flux-system' },
      ->(w) { w.record['status']['observedGeneration'] = 0 },
      ->(w) { w.record['status']['conditions'].first['observedGeneration'] = 0 },
      ->(w) { w.native_chart = 'adtof-0.1.0' },
      ->(w) { w.native_namespace = 'flux-system' },
      ->(w) { w.record['status']['lastAttemptedRevision'] = '0.2.0+abcdef123456.1' }
    ]
    changes.each do |change|
      worker = runner; change.call(worker)
      status, _, error = run_release(worker)
      assert_equal 1, status, error
    end
  end

  def test_only_the_exact_replica_ignore_and_shared_auth_dependency_are_allowed
    changes = [
      ->(r) { r['spec']['driftDetection']['ignore'] = [] },
      ->(r) { r['spec']['driftDetection']['ignore'].first['paths'] = [''] },
      ->(r) { r['spec']['driftDetection']['ignore'].first['target']['name'] = 'clouddsp-basic-pitch' },
      ->(r) { r['spec']['driftDetection']['mode'] = 'disabled' },
      ->(r) { r['spec']['dependsOn'] = [] }
    ]
    changes.each do |change|
      worker = runner; change.call(worker.record)
      status, _, error = run_release(worker)
      assert_equal 1, status, error
      refute worker.commands.any? { |args| args.first(2) == %w[helm lint] }
    end
  end

  def test_unreviewed_sources_and_inline_values_are_rejected
    changes = [
      ->(r) { r['spec']['chart']['spec']['valuesFiles'] << 'another.yaml' },
      ->(r) { r['spec']['chart']['spec']['sourceRef']['name'] = 'another-source' },
      ->(r) { r['spec']['values'] = { 'autoscaling' => { 'minReplicaCount' => 1 } } },
      ->(r) { r['spec']['valuesFrom'] = [{ 'kind' => 'Secret', 'name' => 'another-secret' }] }
    ]
    changes.each do |change|
      worker = runner; change.call(worker.record)
      rejected(worker, 'chart source or values differs')
    end
  end

  def test_flux_labels_do_not_relax_template_policy_or_authentication_parity
    [:render_change, :manifest_change, :live_change].each do |field|
      worker = runner
      worker.public_send("#{field}=", ->(data) { data.first['spec']['template']['spec']['containers'].first['image'] = 'wrong-image' })
      status, _, error = run_release(worker)
      assert_equal 1, status, error
    end
    worker = runner
    worker.live_change = ->(data) { data.last['spec']['triggers'].first['authenticationRef']['name'] = 'wrong-authentication' }
    rejected(worker, 'spec differs from source')
  end

  def test_scaler_readiness_and_hpa_target_owner_are_required
    worker = runner
    worker.live_change = ->(data) { data.last['status']['conditions'].first['status'] = 'False' }
    rejected(worker, 'ADTOF ScaledObject is not Ready')
    worker = runner
    worker.hpa_change = ->(hpa) { hpa['metadata']['ownerReferences'].first['uid'] = 'wrong-owner' }
    rejected(worker, 'HPA is not owned')
    worker = runner
    worker.hpa_change = ->(hpa) { hpa['spec']['scaleTargetRef']['name'] = 'another-worker' }
    rejected(worker, 'HPA target changed')
    worker = runner; worker.desired = 1
    rejected(worker, 'desired replicas changed')
  end

  def test_warm_policy_keeps_parent_readiness_range_and_strict_idle_boundary
    Dir.mktmpdir('adtof-flux-warm') do |directory|
      path = File.join(directory, 'adtof')
      FileUtils.cp_r(HelmRelease::ROOT.join('helm/adtof').to_s, path)
      values = YAML.load_file(File.join(path, 'values.yaml'))
      values['autoscaling']['minReplicaCount'] = 1
      File.write(File.join(path, 'values.yaml'), YAML.dump(values))
      worker = runner; worker.use_chart(path); worker.desired = 1
      status, output, error = run_release(worker, 'verify')
      assert_nil status, error
      assert_includes output, 'configured replica limits'
      rejected(worker, 'verify-idle requires autoscaling.minReplicaCount: 0')
      worker.pod_ready = false
      rejected(worker, 'is not Ready', 'verify')
    end
  end

  def test_smoke_uses_the_existing_versioned_job_without_a_helm_write
    worker = runner
    status, output, error = run_release(worker, 'smoke')
    assert_nil status, error
    assert_includes output, 'smoke passed; disposable test Job removed'
    creates = worker.commands.select { |args| args.first == 'kubectl' && args.include?('create') }
    assert_equal 1, creates.length
    assert_includes creates.first, HelmRelease::ROOT.join('tests/adtof-worker-smoke/adtof-worker-smoke-job.yaml').to_s
    refute worker.commands.any? { |args| args.first == 'helm' && %w[install upgrade].include?(args[1]) }
    worker = runner; worker.existing_job = true
    rejected(worker, 'smoke Job already exists', 'smoke')
    refute worker.commands.any? { |args| args.include?('create') || args.include?('delete') }
  end

  def test_pre_flux_clusters_keep_native_verify_and_all_five_fresh_gates
    [false, true].each do |crd_present|
      worker = runner.manual; worker.crd_present = crd_present
      status, _, error = run_release(worker)
      assert_nil status, error
      worker = runner.manual; worker.crd_present = crd_present
      worker.missing_release = worker.missing_objects = true
      status, _, error = run_release(worker, 'install')
      assert_equal 1, status
      assert_includes error, 'authorized native Helm write reached'
      assert_equal 5, worker.commands.count { |args| args.first == 'ruby' }
      assert worker.commands.any? { |args| args.include?('./k8Deployment/kubernetes/scripts/releases/scaling-auth-release.rb') }
    end
  end
end
