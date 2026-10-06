# Verify configurable worker policies and runtime readiness without a cluster.
# Real Helm rendering exercises source parity; Kubernetes responses are fake
# so tests can cover warm scaling, drift, and adoption without running models.
require 'minitest/autorun'
require 'fileutils'
require 'tmpdir'
require_relative '../../scripts/lib/helm-release'

class WorkerScalingPolicyTest < Minitest::Test
  class FakeWorkerRelease < HelmRelease
    attr_accessor :desired, :ready, :observed, :updated, :pods, :manifest_override, :live_host_override
    attr_accessor :converge_on_sleep
    attr_reader :dry_run_manifest, :sleep_count

    def initialize(chart)
      super(component: 'basic-pitch', namespace: 'clouddsp-app', release: 'clouddsp-basic-pitch',
            source_files: %w[basic-pitch-deployment.yaml basic-pitch-scaledobject.yaml],
            resources: %w[deployment/clouddsp-basic-pitch scaledobject/clouddsp-basic-pitch-rabbitmq-scaler],
            pod_selector: 'app.kubernetes.io/name=basic-pitch', expected_replicas: 0,
            worker_scaling: true, allow_fresh_install: true)
      @chart = Pathname.new(chart)
      @desired = @ready = @observed = @updated = 2
      @pods = [pod('worker-a'), pod('worker-b')]
      @clock = @sleep_count = 0
    end

    def pod(name, ready: true, terminating: false)
      { 'metadata' => { 'name' => name, 'uid' => name, 'deletionTimestamp' => terminating ? '2026-10-04T00:00:00Z' : nil },
        'status' => { 'conditions' => [{ 'type' => 'Ready', 'status' => ready ? 'True' : 'False' }] } }
    end

    private

    def check_tools
      # The tests render Helm locally, but do not require a live kube-context.
    end

    def monotonic_time
      @clock
    end

    def sleep(seconds)
      @clock += seconds
      @sleep_count += 1
      @desired = @ready = @observed = @updated = 2 if @converge_on_sleep
    end

    def command(*arguments, stdin_data: nil)
      if arguments.first == 'kubectl'
        raise 'only server dry-run is allowed' unless arguments.include?('--dry-run=server')

        @dry_run_manifest = stdin_data
        return ''
      end
      super
    end

    def release_record
      { 'name' => @release, 'status' => 'deployed', 'chart' => @chart_identity }
    end

    def helm(*arguments)
      raise 'only installed manifest reads are allowed' unless arguments.first(2) == %w[get manifest]

      (@manifest_override || @rendered.values).map(&:to_yaml).join
    end

    def live_objects
      objects = Marshal.load(Marshal.dump(@rendered))
      objects.each do |id, object|
        object['metadata']['uid'] = id.last
        object['metadata']['annotations'] = { 'meta.helm.sh/release-name' => @release,
                                             'meta.helm.sh/release-namespace' => @namespace }
        if id.first == 'Deployment'
          object['spec']['replicas'] = @desired
          object['status'] = { 'replicas' => @observed, 'readyReplicas' => @ready, 'updatedReplicas' => @updated }
        else
          object['metadata']['labels']['scaledobject.keda.sh/name'] = id.last
          object['spec']['triggers'].first['metadata']['host'] = @live_host_override if @live_host_override
        end
      end
      objects
    end

    def kubectl(*arguments)
      if arguments == ['get', 'pods', '--selector', @pod_selector, '--output', 'json']
        return JSON.generate('items' => @pods)
      end
      raise "unexpected Kubernetes read: #{arguments.inspect}"
    end
  end

  def setup
    @directory = Dir.mktmpdir('clouddsp-worker-policy-')
    FileUtils.cp_r(Dir.glob(HelmRelease::ROOT.join('helm', 'basic-pitch', '*').to_s), @directory)
    @values_path = File.join(@directory, 'values.yaml')
    @values = YAML.load_file(@values_path)
    @values.fetch('autoscaling')['minReplicaCount'] = 1
    File.write(@values_path, @values.to_yaml)
    @runner = FakeWorkerRelease.new(@directory)
    @runner.send(:check_chart_and_source)
  end

  def teardown
    FileUtils.remove_entry(@directory)
  end

  def test_values_are_accepted_without_changing_the_fixed_adoption_snapshot
    source = YAML.load_file(HelmRelease::ROOT.join('services', 'basic-pitch', 'basic-pitch-scaledobject.yaml'))
    assert_equal 0, source.dig('spec', 'minReplicaCount')
    rendered = YAML.load_stream(@runner.dry_run_manifest).compact.find { |object| object['kind'] == 'ScaledObject' }
    assert_equal 1, rendered.dig('spec', 'minReplicaCount')
    assert_equal source.dig('spec', 'triggers'), rendered.dig('spec', 'triggers')
  end

  def test_warm_workers_can_scale_above_the_minimum_and_keep_multiple_ready_pods
    result = @runner.send(:check_cluster, 'Helm')
    assert_equal %w[worker-a worker-b], result.fetch(:pod_uids)
  end

  def test_desired_count_must_be_within_configured_limits
    [0, 4].each do |count|
      @runner.desired = count
      error = assert_raises(RuntimeError) { @runner.send(:check_cluster, 'Helm') }
      assert_includes error.message, 'configured range 1..3'
    end
  end

  def test_every_desired_replica_must_be_observed_ready_and_updated
    %i[observed ready updated].each do |field|
      @runner.public_send("#{field}=", 1)
      error = assert_raises(RuntimeError) { @runner.send(:check_cluster, 'Helm') }
      assert_includes error.message, '2 Ready updated replicas'
      @runner.public_send("#{field}=", 2)
    end
  end

  def test_a_non_ready_or_missing_warm_pod_fails_verification
    @runner.pods = [@runner.pod('worker-a'), @runner.pod('worker-b', ready: false)]
    error = assert_raises(RuntimeError) { @runner.send(:check_cluster, 'Helm') }
    assert_includes error.message, 'worker-b is not Ready'
    @runner.pods = [@runner.pod('worker-a')]
    error = assert_raises(RuntimeError) { @runner.send(:check_cluster, 'Helm') }
    assert_includes error.message, 'expected 2 non-terminating'
  end

  def test_draining_pods_do_not_count_as_warm_capacity
    @runner.pods << @runner.pod('worker-old', ready: false, terminating: true)
    assert_equal %w[worker-a worker-b], @runner.send(:check_cluster, 'Helm').fetch(:pod_uids)
  end

  def test_zero_minimum_retains_idle_verification
    @values.fetch('autoscaling')['minReplicaCount'] = 0
    File.write(@values_path, @values.to_yaml)
    @runner.send(:check_chart_and_source)
    @runner.desired = @runner.ready = @runner.observed = @runner.updated = 0
    @runner.pods = [@runner.pod('worker-old', terminating: true)]
    assert_nil @runner.send(:check_cluster, 'Helm').fetch(:pod_uid)
    @runner.desired = 1
    error = assert_raises(RuntimeError) { @runner.send(:check_cluster, 'Helm') }
    assert_includes error.message, 'desired replicas changed'
  end

  def test_install_supports_a_warm_minimum_above_one
    @values.fetch('autoscaling')['minReplicaCount'] = 2
    File.write(@values_path, @values.to_yaml)
    @runner.send(:check_chart_and_source)
    @runner.define_singleton_method(:release_record) { nil }
    @runner.define_singleton_method(:kubectl) { |*_arguments| '' }
    assert_nil @runner.send(:check_fresh_install_boundary)
  end

  def test_installed_policy_must_match_versioned_values
    @runner.manifest_override = Marshal.load(Marshal.dump(@runner.instance_variable_get(:@rendered).values))
    @runner.manifest_override.find { |object| object['kind'] == 'ScaledObject' }['spec']['minReplicaCount'] = 0
    error = assert_raises(RuntimeError) { @runner.send(:check_cluster, 'Helm') }
    assert_includes error.message, 'installed basic-pitch release manifest differs'
  end

  def test_live_endpoint_drift_remains_an_error
    @runner.live_host_override = 'http://unexpected-host:15672'
    error = assert_raises(RuntimeError) { @runner.send(:check_cluster, 'Helm') }
    assert_includes error.message, '/triggers/0/metadata/host'
  end

  def test_policy_adapter_does_not_mutate_source_or_allow_endpoint_changes
    source = YAML.load_file(HelmRelease::ROOT.join('services', 'basic-pitch', 'basic-pitch-scaledobject.yaml'))
    configured = WorkerScalingPolicy.new(@values).configured_source(source)
    assert_equal 0, source.dig('spec', 'minReplicaCount')
    assert_equal 1, configured.dig('spec', 'minReplicaCount')
    configured['spec']['triggers'].first['metadata']['host'] = 'http://unexpected-host'
    refute_nil @runner.send(:first_difference, WorkerScalingPolicy.new(@values).configured_source(source), configured)
  end

  def test_adoption_must_preserve_all_warm_pod_uids
    before = @runner.send(:check_cluster, 'Helm')
    assert_nil @runner.send(:verify_stable_identity, before, before)
    @runner.pods = [@runner.pod('worker-a'), @runner.pod('replacement')]
    after = @runner.send(:check_cluster, 'Helm')
    error = assert_raises(RuntimeError) { @runner.send(:verify_stable_identity, before, after) }
    assert_includes error.message, 'warm worker Pods changed'
  end

  def test_fresh_install_waits_for_keda_to_apply_a_larger_minimum
    @values.fetch('autoscaling')['minReplicaCount'] = 2
    File.write(@values_path, @values.to_yaml)
    @runner.send(:check_chart_and_source)
    @runner.desired = @runner.ready = @runner.observed = @runner.updated = 1
    @runner.converge_on_sleep = true
    result = @runner.send(:wait_for_worker_install)
    assert_equal %w[worker-a worker-b], result.fetch(:pod_uids)
    assert_equal 1, @runner.sleep_count
  end

  def test_fresh_install_wait_is_bounded_and_reports_the_unready_policy
    @runner.ready = 1
    error = assert_raises(RuntimeError) { @runner.send(:wait_for_worker_install) }
    assert_includes error.message, 'did not become Ready within 180s'
    assert_includes error.message, '2 Ready updated replicas'
    assert_equal HelmRelease::WORKER_INITIALIZATION_TIMEOUT_SECONDS, @runner.sleep_count
  end

  def test_idle_only_maintenance_rejects_a_warm_policy_before_live_reads
    _output, error = capture_io { assert_raises(SystemExit) { @runner.run('verify-idle') } }
    assert_includes error, 'verify-idle requires autoscaling.minReplicaCount: 0'
  end

  def test_custom_thresholds_and_rate_limits_pass_source_parity
    scaling = @values.fetch('autoscaling')
    scaling.merge!('pollingInterval' => 7, 'cooldownPeriod' => 90, 'maxReplicaCount' => 4)
    scaling['rabbitmq'] = { 'queueLength' => 2, 'activationValue' => 1, 'timeout' => 3000 }
    scaling['postgresql'] = { 'targetQueryValue' => 2, 'activationTargetQueryValue' => 1 }
    scaling['behavior']['scaleUp'] = { 'stabilizationWindowSeconds' => 30, 'value' => 2, 'periodSeconds' => 20 }
    scaling['behavior']['scaleDown'] = { 'stabilizationWindowSeconds' => 400, 'value' => 2, 'periodSeconds' => 90 }
    File.write(@values_path, @values.to_yaml)
    @runner.send(:check_chart_and_source)
    scaler = YAML.load_stream(@runner.dry_run_manifest).compact.find { |object| object['kind'] == 'ScaledObject' }
    assert_equal '2', scaler.dig('spec', 'triggers', 0, 'metadata', 'value')
    assert_equal '1', scaler.dig('spec', 'triggers', 1, 'metadata', 'activationTargetQueryValue')
    assert_equal 400, scaler.dig('spec', 'advanced', 'horizontalPodAutoscalerConfig', 'behavior', 'scaleDown', 'stabilizationWindowSeconds')
    assert_equal %w[worker-a worker-b], @runner.send(:check_cluster, 'Helm').fetch(:pod_uids)
  end
end
