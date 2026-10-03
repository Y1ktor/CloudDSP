require 'json'
require 'minitest/autorun'

require_relative '../../scripts/lib/helm-release'

class MinioFreshInstallTest < Minitest::Test
  # Simulate only the external boundaries. The real runner still performs
  # strict chart/source/schema checks; these cases prove that occupied MinIO
  # identities and either missing Secret stop before Helm creates storage.
  class FakeMinio < HelmRelease
    attr_accessor :existing_release, :existing_object, :existing_pvc, :orphan_pod,
                  :root_secret_valid, :amqp_secret_valid
    attr_reader :events

    def initialize
      super(component: 'minio', namespace: 'clouddsp-data', release: 'clouddsp-minio',
            source_files: %w[minio-statefulset.yaml minio-service.yaml minio-headless-service.yaml minio-s3-ingress.yaml],
            resources: %w[statefulset/clouddsp-minio service/clouddsp-minio service/clouddsp-minio-headless ingress/clouddsp-minio-s3],
            pod_selector: 'app.kubernetes.io/name=minio', workload_kind: 'StatefulSet',
            pvc_name: 'minio-data-clouddsp-minio-0',
            before_adopt: %w[python3 minio-backup-and-restore-test.py],
            allow_fresh_install: true,
            before_install: [%w[ruby minio-root-secret-stage.rb verify],
                             %w[ruby minio-amqp-secret-stage.rb verify]],
            fresh_install_timeout: '5m',
            health_host: 'minio.localhost', health_path: '/minio/health/ready')
      @events = []
      @root_secret_valid = true
      @amqp_secret_valid = true
    end

    private

    def check_tools
      @events << :tools
    end

    def check_chart_and_source
      @events << :chart
      @source = {
        ['StatefulSet', 'clouddsp-data', 'clouddsp-minio'] => {
          'spec' => { 'volumeClaimTemplates' => [{ 'metadata' => { 'name' => 'minio-data' } }] }
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
      raise 'root credential verification failed' if arguments.include?('minio-root-secret-stage.rb') && !@root_secret_valid
      raise 'AMQP credential verification failed' if arguments.include?('minio-amqp-secret-stage.rb') && !@amqp_secret_valid

      "NAME: clouddsp-minio\nSTATUS: deployed\n"
    end

    def check_cluster(owner, allow_failed_release: false)
      @events << [:verify_release, owner]
    end

    def verify_http_route
      @events << :verify_route
    end
  end

  def test_install_checks_storage_boundary_then_both_secrets_before_helm
    runner = FakeMinio.new
    capture_io { runner.run('install') }

    assert_equal [:tools, :chart, :release_lookup], runner.events.first(3)
    assert_equal %w[statefulset/clouddsp-minio service/clouddsp-minio service/clouddsp-minio-headless ingress/clouddsp-minio-s3 pvc/minio-data-clouddsp-minio-0 pods],
                 runner.events.select { |event| event.is_a?(Array) && event.first == :object_lookup }.map(&:last)
    commands = runner.events.select { |event| event.is_a?(Array) && event.first == :command }.map(&:last)
    assert_equal %w[ruby minio-root-secret-stage.rb verify], commands[0]
    assert_equal %w[ruby minio-amqp-secret-stage.rb verify], commands[1]
    assert_equal %w[helm install clouddsp-minio], commands[2].first(3)
    assert_equal '5m', commands[2].last
    refute_includes commands[2], '--take-ownership'
    refute_includes commands[2], '--force-conflicts'
    refute commands.any? { |command| command.include?('minio-backup-and-restore-test.py') }
    assert_equal [[:verify_release, 'Helm'], :verify_route], runner.events.last(2)
  end

  def test_existing_release_blocks_before_prerequisites
    runner = FakeMinio.new
    runner.existing_release = { 'status' => 'failed' }

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'Helm release already exists'
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
  end

  def test_existing_resource_or_pvc_blocks_before_prerequisites
    ['ingress/clouddsp-minio-s3', 'pvc/minio-data-clouddsp-minio-0'].each do |resource|
      runner = FakeMinio.new
      resource.start_with?('pvc/') ? runner.existing_pvc = true : runner.existing_object = resource

      _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

      assert_includes error, 'already exists'
      refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
    end
  end

  def test_orphan_pod_blocks_before_prerequisites
    runner = FakeMinio.new
    runner.orphan_pod = true

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'Pod already exists'
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
  end

  def test_missing_root_secret_stops_before_amqp_check_and_helm
    runner = FakeMinio.new
    runner.root_secret_valid = false

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'root credential verification failed'
    assert_equal ['minio-root-secret-stage.rb'], command_scripts(runner)
  end

  def test_missing_amqp_secret_stops_before_helm
    runner = FakeMinio.new
    runner.amqp_secret_valid = false

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'AMQP credential verification failed'
    assert_equal %w[minio-root-secret-stage.rb minio-amqp-secret-stage.rb], command_scripts(runner)
  end

  private

  def command_scripts(runner)
    runner.events.select { |event| event.is_a?(Array) && event.first == :command }.map { |event| event.last[1] }
  end
end
