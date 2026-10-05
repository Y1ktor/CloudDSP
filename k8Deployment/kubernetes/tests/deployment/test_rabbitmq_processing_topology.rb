require 'json'
require 'minitest/autorun'
require 'yaml'
require_relative '../../scripts/stages/rabbitmq/rabbitmq-processing-topology'

class RabbitmqProcessingTopologyTest < Minitest::Test
  class FakeCluster
    attr_reader :calls, :broker

    def initialize(stages, applied_versions: [])
      @stages = stages
      @broker = { 'vhosts' => [], 'exchanges' => [], 'queues' => [], 'bindings' => [] }
      @configs = {}
      @jobs = {}
      @calls = []
      applied_versions.each do |version|
        stage = @stages.find { |item| item.version == version }
        @configs[stage.config_name] = stage.config
        import(stage)
      end
    end

    def call(*argv)
      @calls << argv
      verb, name = argv[5], argv[6]
      if verb == 'exec'
        kind = %w[list_vhosts list_exchanges list_queues list_bindings].find { |item| argv.include?(item) }
        return JSON.generate(@broker.fetch(kind.delete_prefix('list_'))) if kind
      elsif verb == 'get'
        return JSON.generate(@configs[name.delete_prefix('configmap/')]) if name.start_with?('configmap/') && @configs.key?(name.delete_prefix('configmap/'))
        return JSON.generate(@jobs[name.delete_prefix('job/')]) if name.start_with?('job/') && @jobs.key?(name.delete_prefix('job/'))
        return '' if name.start_with?('configmap/', 'job/')
        return 'secret/clouddsp-rabbitmq-credentials' if name == 'secret/clouddsp-rabbitmq-credentials'
        return JSON.generate({ 'status' => { 'readyReplicas' => 1 } }) if name == 'statefulset/clouddsp-rabbitmq'
      elsif verb == 'create'
        return 'validated' if argv.include?('--dry-run=server')

        path = argv.last
        stage = @stages.find { |item| [item.config_path.to_s, item.job_path.to_s].include?(path) }
        raise "unexpected create source: #{path}" unless stage

        if path == stage.config_path.to_s
          @configs[stage.config_name] = stage.config
        else
          @jobs[stage.job_name] = { 'status' => { 'conditions' => [{ 'type' => 'Complete', 'status' => 'True' }] } }
          import(stage)
        end
        return 'created'
      elsif verb == 'wait'
        return 'complete'
      end
      raise "unexpected fake command: #{argv.inspect}"
    end

    private

    def import(stage)
      stage.definition.fetch('vhosts', []).each { |item| @broker.fetch('vhosts') << item unless @broker.fetch('vhosts').include?(item) }
      %w[exchanges queues bindings].each do |kind|
        @broker.fetch(kind).concat(stage.definition.fetch(kind, []))
      end
    end
  end

  def setup
    @stages = RabbitmqProcessingTopology.new.load_stages
  end

  def test_complete_reconcile_does_not_reimport_or_create
    fake = FakeCluster.new(@stages, applied_versions: %w[v001 v002])
    out, err = capture_io { runner(fake).run('reconcile') }

    assert_empty(err)
    assert_includes(out, 'v002: verified; no work pending')
    refute(fake.calls.any? { |argv| argv.include?('create') || argv.include?('wait') })
  end

  def test_fresh_plan_reports_both_versions_without_writes
    fake = FakeCluster.new(@stages)
    out, err = capture_io { runner(fake).run('plan') }

    assert_empty(err)
    assert_includes(out, 'v001: versioned import pending')
    assert_includes(out, 'v002: versioned import pending')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  def test_fresh_reconcile_imports_v001_before_v002
    fake = FakeCluster.new(@stages)
    out, err = capture_io { runner(fake).run('reconcile') }

    assert_empty(err)
    assert_includes(out, 'v002: applied and verified')
    created = fake.calls.select { |argv| argv[5] == 'create' && !argv.include?('--dry-run=server') }.map(&:last)
    assert_equal([@stages[0].config_path.to_s, @stages[0].job_path.to_s,
                  @stages[1].config_path.to_s, @stages[1].job_path.to_s], created)
  end

  def test_partial_import_blocks_all_writes
    fake = FakeCluster.new(@stages, applied_versions: %w[v001 v002])
    fake.broker.fetch('bindings').pop

    _out, err = capture_io { assert_raises(SystemExit) { runner(fake).run('reconcile') } }

    assert_includes(err, 'v002 processing topology is partially present')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  def test_queue_argument_drift_blocks_all_writes
    fake = FakeCluster.new(@stages, applied_versions: %w[v001 v002])
    fake.broker.fetch('queues').first.fetch('arguments')['x-max-length'] = 999

    _out, err = capture_io { assert_raises(SystemExit) { runner(fake).run('reconcile') } }

    assert_includes(err, 'queue clouddsp.demucs.requests drifted')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  def test_extra_route_to_worker_queue_blocks_all_writes
    fake = FakeCluster.new(@stages, applied_versions: %w[v001 v002])
    fake.broker.fetch('bindings') << {
      'source' => 'clouddsp.processing-events', 'destination' => 'clouddsp.demucs.requests',
      'destination_type' => 'queue', 'routing_key' => 'unexpected.request', 'arguments' => {}
    }

    _out, err = capture_io { assert_raises(SystemExit) { runner(fake).run('reconcile') } }

    assert_includes(err, 'v001 queue has an unexpected explicit binding')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  def test_v002_without_v001_blocks_all_writes
    fake = FakeCluster.new(@stages, applied_versions: ['v002'])
    fake.broker.fetch('vhosts') << { 'name' => '/clouddsp' }

    _out, err = capture_io { assert_raises(SystemExit) { runner(fake).run('reconcile') } }

    assert_includes(err, 'v002 exists without its v001 prerequisite')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  def test_applied_broker_topology_without_immutable_configmap_blocks
    fake = FakeCluster.new(@stages, applied_versions: %w[v001 v002])
    fake.instance_variable_get(:@configs).delete(@stages[0].config_name)

    _out, err = capture_io { assert_raises(SystemExit) { runner(fake).run('verify') } }

    assert_includes(err, 'v001 broker topology has no immutable source ConfigMap')
  end

  def test_existing_job_without_broker_topology_blocks_reimport
    fake = FakeCluster.new(@stages)
    fake.instance_variable_get(:@jobs)[@stages[0].job_name] = {
      'status' => { 'conditions' => [{ 'type' => 'Failed', 'status' => 'True' }] }
    }

    _out, err = capture_io { assert_raises(SystemExit) { runner(fake).run('reconcile') } }

    assert_includes(err, 'v001 bootstrap Job exists without complete broker topology')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  private

  def runner(fake)
    RabbitmqProcessingTopology.new(runner: fake.method(:call))
  end
end
