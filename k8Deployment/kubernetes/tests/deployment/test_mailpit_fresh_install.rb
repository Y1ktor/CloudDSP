require 'minitest/autorun'

require_relative '../../scripts/stateless-release'

class MailpitFreshInstallTest < Minitest::Test
  class FakeMailpit < StatelessRelease
    attr_accessor :existing_release, :existing_object
    attr_reader :events

    def initialize
      super(component: 'mailpit', namespace: 'clouddsp-data', release: 'clouddsp-mailpit',
            source_files: %w[mailpit-deployment.yaml mailpit-services.yaml mailpit-ingress.yaml],
            resources: %w[deployment/clouddsp-mailpit service/clouddsp-mailpit-smtp service/clouddsp-mailpit ingress/clouddsp-mailpit],
            pod_selector: 'app.kubernetes.io/name=mailpit', health_host: 'mailpit.localhost',
            health_path: '/readyz', allow_fresh_install: true)
      @events = []
    end

    private

    def check_tools
      @events << :tools
    end

    def check_chart_and_source
      @events << :chart
    end

    def release_record
      @events << :release_lookup
      @existing_release
    end

    def kubectl(*arguments)
      @events << [:object_lookup, arguments.fetch(1)]
      @existing_object == arguments.fetch(1) ? "#{@existing_object}\n" : ''
    end

    def command(*arguments, stdin_data: nil)
      @events << [:command, arguments]
      "NAME: clouddsp-mailpit\nSTATUS: deployed\n"
    end

    def check_cluster(owner, allow_failed_release: false)
      @events << [:verify_release, owner]
    end

    def verify_http_route
      @events << :route
    end
  end

  def test_install_requires_absence_then_installs_without_takeover_and_verifies
    runner = FakeMailpit.new
    capture_io { runner.run('install') }

    assert_equal [:tools, :chart, :release_lookup], runner.events.first(3)
    assert_equal 4, runner.events.count { |event| event.is_a?(Array) && event.first == :object_lookup }
    command = runner.events.find { |event| event.is_a?(Array) && event.first == :command }.last
    assert_equal %w[helm install clouddsp-mailpit], command.first(3)
    assert_includes command, '--kube-context'
    assert_includes command, '--wait'
    refute_includes command, '--take-ownership'
    refute_includes command, '--force-conflicts'
    assert_equal [[:verify_release, 'Helm'], :route], runner.events.last(2)
  end

  def test_existing_release_stops_before_install
    runner = FakeMailpit.new
    runner.existing_release = { 'status' => 'failed' }

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'Helm release already exists'
    refute_includes error, 'before retrying'
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
  end

  def test_single_existing_object_stops_before_install
    runner = FakeMailpit.new
    runner.existing_object = 'service/clouddsp-mailpit-smtp'

    _output, error = capture_io { assert_raises(SystemExit) { runner.run('install') } }

    assert_includes error, 'service/clouddsp-mailpit-smtp already exists'
    refute_includes error, 'before retrying'
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command }
  end
end
