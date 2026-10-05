require 'minitest/autorun'
require_relative '../../scripts/releases/keycloak-release'

class KeycloakFreshInstallTest < Minitest::Test
  # Keep the actual thin runner configuration while replacing only external
  # commands. This proves the two reviewed prerequisites run before Helm and
  # that the inherited fresh-install boundary refuses existing ownership.
  class FakeKeycloak
    attr_accessor :existing_release, :existing_object, :failing_prerequisite
    attr_reader :events, :runner

    def initialize
      @runner = CloudDSPKeycloakRelease.build
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
          raise 'Keycloak prerequisite verification failed'
        end
        "NAME: clouddsp-keycloak\nSTATUS: deployed\n"
      end
      @runner.define_singleton_method(:check_cluster) do |owner, allow_failed_release: false|
        fake.events << [:verify_release, owner]
      end
      @runner.define_singleton_method(:verify_http_route) { fake.events << :route }
    end
  end

  def test_install_checks_absent_objects_then_database_and_admin_before_helm
    fake = FakeKeycloak.new
    capture_io { fake.runner.run('install') }

    assert_equal [:tools, :chart, :release_lookup], fake.events.first(3)
    objects = fake.events.select { |event| event.is_a?(Array) && event.first == :object_lookup }.map(&:last)
    assert_equal %w[deployment/clouddsp-keycloak service/clouddsp-keycloak ingress/clouddsp-keycloak], objects
    commands = fake.events.select { |event| event.is_a?(Array) && event.first == :command }.map(&:last)
    assert_equal %w[keycloak-database-stage.rb keycloak-admin-secret-stage.rb],
                 commands.take(2).map { |command| File.basename(command.fetch(1)) }
    assert commands.take(2).all? { |command| command.first == 'ruby' && command.last == 'verify' }
    assert_equal %w[helm install clouddsp-keycloak], commands.last.first(3)
    assert_equal '7m', commands.last.last
    refute_includes commands.last, '--take-ownership'
    refute_includes commands.last, '--force-conflicts'
    assert_equal [[:verify_release, 'Helm'], :route], fake.events.last(2)
  end

  def test_failed_admin_verification_prevents_helm_install
    fake = FakeKeycloak.new
    fake.failing_prerequisite = 'keycloak-admin-secret-stage.rb'

    _output, error = capture_io { assert_raises(SystemExit) { fake.runner.run('install') } }

    assert_includes error, 'Keycloak prerequisite verification failed'
    commands = fake.events.select { |event| event.is_a?(Array) && event.first == :command }.map(&:last)
    assert_equal 2, commands.length
    refute commands.any? { |command| command.first == 'helm' }
  end

  def test_existing_release_or_object_blocks_before_prerequisites
    [[:existing_release, { 'status' => 'failed' }],
     [:existing_object, 'service/clouddsp-keycloak']].each do |field, value|
      fake = FakeKeycloak.new
      fake.public_send("#{field}=", value)
      _output, error = capture_io { assert_raises(SystemExit) { fake.runner.run('install') } }
      assert_includes error, field == :existing_release ? 'Helm release already exists' : 'service/clouddsp-keycloak already exists'
      refute fake.events.any? { |event| event.is_a?(Array) && event.first == :command }
    end
  end
end
