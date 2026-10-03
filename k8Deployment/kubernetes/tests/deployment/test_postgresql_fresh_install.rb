require 'json'
require 'minitest/autorun'

require_relative '../../scripts/lib/helm-release'

class PostgresqlFreshInstallTest < Minitest::Test
  class FakePostgresql < HelmRelease
    attr_accessor :existing_release, :existing_object, :existing_pvc, :orphan_pod, :secret_valid
    attr_reader :events

    def initialize
      super(component: 'postgresql', namespace: 'clouddsp-data', release: 'clouddsp-postgresql',
            source_files: %w[postgresql-statefulset.yaml postgresql-service.yaml postgresql-headless-service.yaml],
            resources: %w[statefulset/clouddsp-postgresql service/clouddsp-postgresql service/clouddsp-postgresql-headless],
            pod_selector: 'app.kubernetes.io/name=postgresql', workload_kind: 'StatefulSet',
            pvc_name: 'postgres-data-clouddsp-postgresql-0', allow_fresh_install: true,
            before_install: %w[ruby postgresql-secret-stage.rb verify], fresh_install_timeout: '5m')
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
        ['StatefulSet', 'clouddsp-data', 'clouddsp-postgresql'] => {
          'spec' => { 'volumeClaimTemplates' => [{ 'metadata' => { 'name' => 'postgres-data' } }] }
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

      "NAME: clouddsp-postgresql\nSTATUS: deployed\n"
    end

    def check_cluster(owner, allow_failed_release: false)
      @events << [:verify_release, owner]
    end
  end

  def test_install_checks_all_storage_boundaries_and_secret_before_helm
    runner = FakePostgresql.new
    capture_io { runner.run('install') }

    assert_equal [:tools, :chart, :release_lookup], runner.events.first(3)
    assert_equal %w[statefulset/clouddsp-postgresql service/clouddsp-postgresql service/clouddsp-postgresql-headless pvc/postgres-data-clouddsp-postgresql-0 pods],
                 runner.events.select { |event| event.is_a?(Array) && event.first == :object_lookup }.map(&:last)
    commands = runner.events.select { |event| event.is_a?(Array) && event.first == :command }.map(&:last)
    assert_equal %w[ruby postgresql-secret-stage.rb verify], commands.first
    assert_equal %w[helm install clouddsp-postgresql], commands.last.first(3)
    assert_equal '5m', commands.last.last
    refute_includes commands.last, '--take-ownership'
    refute_includes commands.last, '--force-conflicts'
    assert_equal [:verify_release, 'Helm'], runner.events.last
  end

  def test_existing_pvc_blocks_install_before_credential_or_helm_commands
    runner = FakePostgresql.new
    runner.existing_pvc = true

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'PVC postgres-data-clouddsp-postgresql-0 already exists'
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
  end

  def test_orphan_pod_blocks_install
    runner = FakePostgresql.new
    runner.orphan_pod = true

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'Pod already exists'
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
  end

  def test_missing_credential_stops_before_helm
    runner = FakePostgresql.new
    runner.secret_valid = false

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'credential verification failed'
    assert_equal ['ruby'], runner.events.select { |event| event.is_a?(Array) && event.first == :command }.map { |event| event.last.first }
  end

  def test_existing_release_blocks_install_without_running_backup_gate
    runner = FakePostgresql.new
    runner.existing_release = { 'status' => 'failed' }

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'Helm release already exists'
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
  end
end
