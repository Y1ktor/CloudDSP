require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/deploy-local-verify'

class DeployLocalVerifyTest < Minitest::Test
  FakeStatus = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  def test_every_stage_uses_a_read_only_mode_and_explicit_kubernetes_context
    commands = CloudDSPLocalVerify::STAGES.map(&:command)
    assert_equal ['ruby', 'deploy-local-plan.rb'], commands.first
    assert_equal ['ruby', 'frontend-release.rb', 'verify'], commands.last
    commands.each do |command|
      if command.first == 'ruby'
        assert command.length == 2 || command.last == 'verify', command.inspect
        refute_match(/\A(?:adopt|reconcile|smoke|bootstrap)\z/, command.last)
      else
        assert_equal 'kubectl', command.first
        assert_equal 'k3d-clouddsp-local', command[command.index('--context') + 1]
        if command.include?('rollout')
          assert_equal 'status', command[command.index('rollout') + 1]
        else
          assert_equal 'crd', command[command.index('get') + 1]
        end
      end
    end
  end

  def test_runs_in_declared_order_and_reports_success
    calls = []
    runner = lambda do |*command|
      calls << command
      ['', '', FakeStatus.new(0)]
    end
    output = StringIO.new
    errors = StringIO.new
    result = CloudDSPLocalVerify.new(runner: runner, output: output, error: errors).run

    assert_equal 0, result
    assert_equal CloudDSPLocalVerify::STAGES.length, calls.length
    assert_match(/preflight/, output.string)
    assert_match(/all configured verify gates passed/, output.string)
    assert_empty errors.string
  end

  def test_failure_stops_before_later_stages_and_hides_child_output
    calls = []
    runner = lambda do |*command|
      calls << command
      calls.length == 4 ? ['sensitive child output', 'sensitive child error', FakeStatus.new(1)] : ['', '', FakeStatus.new(0)]
    end
    output = StringIO.new
    errors = StringIO.new
    result = CloudDSPLocalVerify.new(runner: runner, output: output, error: errors).run

    assert_equal 1, result
    assert_equal 4, calls.length
    assert_includes errors.string, 'RabbitMQ release'
    refute_includes(output.string + errors.string, 'sensitive child')
    refute_includes output.string, 'MinIO release'
  end

  def test_missing_command_reports_stage_without_running_later_stages
    runner = lambda { |*_command| raise Errno::ENOENT }
    errors = StringIO.new
    result = CloudDSPLocalVerify.new(runner: runner, output: StringIO.new, error: errors).run

    assert_equal 1, result
    assert_includes errors.string, 'preflight'
  end
end
