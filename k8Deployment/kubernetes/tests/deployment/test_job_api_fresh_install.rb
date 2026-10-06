require 'minitest/autorun'
require_relative '../../scripts/releases/job-api-release'

class JobApiFreshInstallTest < Minitest::Test
  # Exercise the actual Job API release configuration while replacing only
  # external commands. The shared helper still performs its named-object and
  # release absence checks and invokes the five dependency gates in order.
  class FakeJobApi
    attr_accessor :existing_release, :existing_object, :failing_prerequisite
    attr_reader :events, :runner

    def initialize
      @runner = CloudDSPJobApiRelease.build
      @events = []
      fake = self
      @runner.define_singleton_method(:check_tools) { fake.events << :tools }
      @runner.define_singleton_method(:check_chart_and_source) { fake.events << :chart }
      @runner.define_singleton_method(:release_record) do
        fake.events << :release_lookup
        fake.existing_release
      end
      @runner.define_singleton_method(:kubectl) do |*arguments|
        resource = arguments.fetch(1)
        fake.events << [:object_lookup, resource]
        fake.existing_object == resource ? "#{resource}\n" : ''
      end
      @runner.define_singleton_method(:command) do |*arguments, stdin_data: nil|
        fake.events << [:command, arguments]
        if arguments.first == 'ruby' && File.basename(arguments.fetch(1)) == fake.failing_prerequisite
          raise 'Job API prerequisite verification failed'
        end
        "NAME: clouddsp-job-api\nSTATUS: deployed\n"
      end
      @runner.define_singleton_method(:check_cluster) do |owner, allow_failed_release: false|
        fake.events << [:verify_release, owner]
      end
      @runner.define_singleton_method(:verify_http_route) { fake.events << :routes }
    end
  end

  def test_install_checks_absent_objects_and_all_dependencies_before_helm
    fake = FakeJobApi.new
    capture_io { fake.runner.run('install') }

    assert_equal [:tools, :chart, :release_lookup], fake.events.first(3)
    objects = fake.events.select { |event| event.is_a?(Array) && event.first == :object_lookup }.map(&:last)
    assert_equal %w[deployment/clouddsp-job-api service/clouddsp-job-api ingress/clouddsp-job-api], objects
    commands = fake.events.select { |event| event.is_a?(Array) && event.first == :command }.map(&:last)
    assert_equal %w[job-api-database-secret-stage.rb job-api-postgresql-stage.rb job-api-minio-secret-stage.rb minio-job-api-iam-stage.rb keycloak-config-verify.rb],
                 commands.take(5).map { |command| File.basename(command.fetch(1)) }
    assert commands.take(5).all? { |command| command.first == 'ruby' && command.last == 'verify' }
    assert_equal %w[helm install clouddsp-job-api], commands.last.first(3)
    refute_includes commands.last, '--take-ownership'
    refute_includes commands.last, '--force-conflicts'
    assert_equal [[:verify_release, 'Helm'], :routes], fake.events.last(2)
  end

  def test_failed_dependency_prevents_helm_install
    fake = FakeJobApi.new
    fake.failing_prerequisite = 'minio-job-api-iam-stage.rb'

    _output, error = capture_io { assert_raises(SystemExit) { fake.runner.run('install') } }

    assert_includes error, 'Job API prerequisite verification failed'
    commands = fake.events.select { |event| event.is_a?(Array) && event.first == :command }.map(&:last)
    assert_equal 4, commands.length
    refute commands.any? { |command| command.first == 'helm' }
  end

  def test_existing_release_or_object_blocks_all_dependency_checks
    [[:existing_release, { 'status' => 'failed' }],
     [:existing_object, 'ingress/clouddsp-job-api']].each do |field, value|
      fake = FakeJobApi.new
      fake.public_send("#{field}=", value)
      _output, error = capture_io { assert_raises(SystemExit) { fake.runner.run('install') } }
      assert_includes error, field == :existing_release ? 'Helm release already exists' : 'ingress/clouddsp-job-api already exists'
      refute fake.events.any? { |event| event.is_a?(Array) && event.first == :command }
    end
  end
end
