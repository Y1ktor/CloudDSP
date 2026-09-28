require 'json'
require 'minitest/autorun'

require_relative '../../scripts/stateless-release'

class RabbitmqFreshInstallTest < Minitest::Test
  # Simulate only the Kubernetes and Helm boundaries. The real runner's
  # source/chart comparison and live verification remain separate checks;
  # these cases prove no write occurs around an occupied broker identity.
  class FakeRabbitmq < StatelessRelease
    attr_accessor :existing_release, :existing_object, :existing_pvc, :orphan_pod, :secret_valid
    attr_reader :events

    def initialize
      super(component: 'rabbitmq', namespace: 'clouddsp-data', release: 'clouddsp-rabbitmq',
            source_files: %w[rabbitmq-statefulset.yaml rabbitmq-service.yaml rabbitmq-headless-service.yaml rabbitmq-management-service.yaml rabbitmq-ingress-network-policy.yaml],
            resources: %w[statefulset/clouddsp-rabbitmq service/clouddsp-rabbitmq service/clouddsp-rabbitmq-headless service/clouddsp-rabbitmq-management networkpolicy/clouddsp-rabbitmq-ingress],
            pod_selector: 'app.kubernetes.io/name=rabbitmq', workload_kind: 'StatefulSet',
            pvc_name: 'rabbitmq-data-clouddsp-rabbitmq-0',
            before_adopt: %w[python3 rabbitmq-backup-and-restore-test.py],
            allow_fresh_install: true, before_install: %w[ruby rabbitmq-secret-stage.rb verify],
            fresh_install_timeout: '5m')
      @events = []
      @secret_valid = true
    end

    private

    def check_tools
      @events << :tools
    end

    def check_chart_and_source
      @events << :chart
      @source = {
        ['StatefulSet', 'clouddsp-data', 'clouddsp-rabbitmq'] => {
          'spec' => { 'volumeClaimTemplates' => [{ 'metadata' => { 'name' => 'rabbitmq-data' } }] }
        }
      }
    end

    def release_record
      @events << :release_lookup
      @existing_release
    end

    def kubectl(*arguments)
      resource = arguments.fetch(1)
      @events << [:object_lookup, resource]
      return JSON.generate('items' => (@orphan_pod ? [{ 'metadata' => { 'name' => 'orphan' } }] : [])) if resource == 'pods'
      return "#{resource}\n" if resource == @existing_object || (resource.start_with?('pvc/') && @existing_pvc)

      ''
    end

    def command(*arguments, stdin_data: nil)
      @events << [:command, arguments]
      raise 'credential verification failed' if arguments.first == 'ruby' && !@secret_valid

      "NAME: clouddsp-rabbitmq\nSTATUS: deployed\n"
    end

    def check_cluster(owner, allow_failed_release: false)
      @events << [:verify_release, owner]
    end
  end

  def test_install_checks_all_broker_and_storage_boundaries_before_secret_and_helm
    runner = FakeRabbitmq.new
    capture_io { runner.run('install') }

    assert_equal [:tools, :chart, :release_lookup], runner.events.first(3)
    assert_equal %w[statefulset/clouddsp-rabbitmq service/clouddsp-rabbitmq service/clouddsp-rabbitmq-headless service/clouddsp-rabbitmq-management networkpolicy/clouddsp-rabbitmq-ingress pvc/rabbitmq-data-clouddsp-rabbitmq-0 pods],
                 runner.events.select { |event| event.is_a?(Array) && event.first == :object_lookup }.map(&:last)
    commands = runner.events.select { |event| event.is_a?(Array) && event.first == :command }.map(&:last)
    assert_equal %w[ruby rabbitmq-secret-stage.rb verify], commands.first
    assert_equal %w[helm install clouddsp-rabbitmq], commands.last.first(3)
    assert_equal '5m', commands.last.last
    refute_includes commands.last, '--take-ownership'
    refute_includes commands.last, '--force-conflicts'
    refute commands.any? { |command| command.include?('rabbitmq-backup-and-restore-test.py') }
    assert_equal [:verify_release, 'Helm'], runner.events.last
  end

  def test_existing_release_blocks_install
    runner = FakeRabbitmq.new
    runner.existing_release = { 'status' => 'failed' }

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'Helm release already exists'
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
  end

  def test_existing_broker_resource_blocks_install
    runner = FakeRabbitmq.new
    runner.existing_object = 'networkpolicy/clouddsp-rabbitmq-ingress'

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'networkpolicy/clouddsp-rabbitmq-ingress already exists'
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
  end

  def test_existing_pvc_blocks_install_before_credential_or_helm_commands
    runner = FakeRabbitmq.new
    runner.existing_pvc = true

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'PVC rabbitmq-data-clouddsp-rabbitmq-0 already exists'
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
  end

  def test_orphan_pod_blocks_install
    runner = FakeRabbitmq.new
    runner.orphan_pod = true

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'Pod already exists'
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
  end

  def test_missing_credential_stops_before_helm
    runner = FakeRabbitmq.new
    runner.secret_valid = false

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'credential verification failed'
    assert_equal ['ruby'], runner.events.select { |event| event.is_a?(Array) && event.first == :command }.map { |event| event.last.first }
  end
end
