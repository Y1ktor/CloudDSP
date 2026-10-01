require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/deploy-local-foundation'

class DeployLocalFoundationTest < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  class FakeCommands
    attr_accessor :cluster, :registry, :namespaces, :fail_namespace_create
    attr_reader :calls

    def initialize(cluster: false, registry: false, namespaces: false)
      @cluster = cluster
      @registry = registry
      @namespaces = namespaces
      @traefik_created = cluster
      @deployment_lookups = Hash.new(0)
      @calls = []
      @fail_namespace_create = false
    end

    def call(*argv)
      @calls << argv
      output = case argv
               when ['k3d', 'cluster', 'list', '--no-headers']
                 @cluster ? "clouddsp-local   1/1   2/2   true\n" : ''
               when ['k3d', 'registry', 'list', '--no-headers']
                 @registry ? "clouddsp-registry.localhost   registry   clouddsp-local   running\n" : ''
               else
                 dispatch(argv)
               end
      return ['', '', Status.new(1)] if output == :failure

      [output, '', Status.new(0)]
    end

    def dispatch(argv)
      if argv.first.end_with?('/cluster.sh') && argv.last == 'create'
        @cluster = true
        @registry = true
        ''
      elsif argv.first == 'curl'
        ''
      elsif argv.first == 'kubectl' && argv.include?('create')
        return :failure if @fail_namespace_create && !argv.include?('--dry-run=server')

        @namespaces = true unless argv.include?('--dry-run=server')
        ''
      elsif argv.first == 'kubectl' && argv.include?('--ignore-not-found') &&
            argv.any? { |arg| arg.start_with?('deployment/') }
        name = argv.find { |arg| arg.start_with?('deployment/') }.delete_prefix('deployment/')
        @deployment_lookups[name] += 1
        return '' if name == 'traefik' && @deployment_lookups[name] == 1 && !@traefik_created

        @traefik_created = true if name == 'traefik'
        "deployment.apps/#{name}\n"
      elsif argv.first == 'kubectl' && argv.include?('wait')
        ''
      elsif argv.first == 'kubectl' && argv.include?('nodes')
        JSON.generate('items' => CloudDSPFoundation::NODE_ROLES.map do |name, role|
          { 'metadata' => { 'name' => name, 'labels' => { 'clouddsp.io/role' => role } },
            'status' => { 'conditions' => [{ 'type' => 'Ready', 'status' => 'True' }] } }
        end)
      elsif argv.first == 'kubectl' && argv.include?('namespaces')
        labels = CloudDSPFoundation.new.send(:load_source)
        items = if @namespaces
                  labels.map do |name, values|
                    { 'metadata' => { 'name' => name, 'labels' => values },
                      'status' => { 'phase' => 'Active' } }
                  end
                else
                  []
                end
        JSON.generate('items' => items)
      elsif argv.first == 'kubectl' && argv.include?('rollout')
        argv.include?('deployment/traefik') && !@traefik_created ? :failure : ''
      else
        raise "unexpected fake command #{argv.first}"
      end
    end
  end

  class FakeFoundation < CloudDSPFoundation
    def check_prerequisites
      # The command runner below owns the modeled host. No real daemon or
      # host PATH is needed to exercise the stage's guarded write sequence.
    end
  end

  def foundation(commands)
    elapsed_seconds = 0.0
    FakeFoundation.new(command: commands.method(:call), output: StringIO.new, error: StringIO.new,
                       monotonic_clock: -> { elapsed_seconds },
                       sleeper: ->(seconds) { elapsed_seconds += seconds })
  end

  def test_source_configuration_is_fixed_to_the_reviewed_three_node_profile
    namespaces = foundation(FakeCommands.new).send(:load_source)

    assert_equal %w[clouddsp-app clouddsp-data clouddsp-system], namespaces.keys.sort
    assert_equal 'data', namespaces.fetch('clouddsp-data').fetch('clouddsp.io/scope')
  end

  def test_absent_cluster_plan_does_not_create_anything
    commands = FakeCommands.new

    assert_equal 0, foundation(commands).run('plan')
    refute commands.calls.any? { |argv| argv.first.end_with?('/cluster.sh') || argv.include?('create') }
  end

  def test_bootstrap_creates_cluster_then_namespaces_and_verifies_foundation
    commands = FakeCommands.new

    assert_equal 0, foundation(commands).run('bootstrap')
    create_cluster = commands.calls.index { |argv| argv.first.end_with?('/cluster.sh') }
    wait_nodes = commands.calls.index { |argv| argv.first == 'kubectl' && argv.include?('wait') }
    create_namespaces = commands.calls.index { |argv| argv.first == 'kubectl' && argv.include?('create') && !argv.include?('--dry-run=server') }
    assert_operator create_cluster, :<, wait_nodes
    assert_operator wait_nodes, :<, create_namespaces
    wait_traefik = commands.calls.index do |argv|
      argv.include?('--ignore-not-found') && argv.include?('deployment/traefik')
    end
    rollout_traefik = commands.calls.index { |argv| argv.include?('rollout') && argv.include?('deployment/traefik') }
    assert_operator create_namespaces, :<, wait_traefik
    assert_operator wait_traefik, :<, rollout_traefik
    assert commands.cluster
    assert commands.registry
    assert commands.namespaces
    assert_equal 0, foundation(commands).run('verify')
  end

  def test_system_deployment_creation_wait_polls_until_async_traefik_exists
    commands = FakeCommands.new

    assert_equal 0, foundation(commands).run('bootstrap')
    CloudDSPFoundation::SYSTEM_DEPLOYMENTS.each do |name|
      assert_includes commands.calls,
                      ['kubectl', '--context', 'k3d-clouddsp-local', '-n', 'kube-system',
                       'get', "deployment/#{name}", '--ignore-not-found', '--output=name',
                       "--request-timeout=#{CloudDSPFoundation::SYSTEM_DEPLOYMENT_REQUEST_TIMEOUT}"]
    end
    traefik_lookups = commands.calls.count do |argv|
      argv.include?('--ignore-not-found') && argv.include?('deployment/traefik')
    end
    assert_equal 2, traefik_lookups, 'the async Traefik Deployment appears after the first lookup'
  end

  def test_existing_cluster_stops_before_mutation
    existing = FakeCommands.new(cluster: true, registry: true, namespaces: true)

    assert_equal 1, foundation(existing).run('bootstrap')
    refute existing.calls.any? { |argv| argv.first.end_with?('/cluster.sh') || argv.include?('create') }
  end

  def test_retained_registry_is_reused_for_fresh_cluster
    commands = FakeCommands.new(registry: true)

    assert_equal 0, foundation(commands).run('plan')
    assert_equal 0, foundation(commands).run('bootstrap')
    assert commands.calls.any? { |argv| argv.first.end_with?('/cluster.sh') && argv.last == 'create' }
    assert commands.cluster
    assert commands.registry
  end

  def test_partial_namespace_failure_remains_for_explicit_inspection
    commands = FakeCommands.new
    commands.fail_namespace_create = true

    assert_equal 1, foundation(commands).run('bootstrap')
    assert commands.cluster
    refute commands.namespaces
    assert_equal 1, foundation(commands).run('bootstrap')
    assert_equal 1, commands.calls.count { |argv| argv.first.end_with?('/cluster.sh') }
  end
end
