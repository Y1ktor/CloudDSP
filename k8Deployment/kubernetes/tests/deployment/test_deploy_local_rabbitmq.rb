require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/orchestration/deploy-local-rabbitmq'

class DeployLocalRabbitmqTest < Minitest::Test
  def setup
    @calls = []
    @output = StringIO.new
    @error = StringIO.new
  end

  def run_bootstrap(failing_step = nil)
    runner = lambda do |ruby, script, *arguments|
      assert_equal RbConfig.ruby, ruby
      call = [File.basename(script), *arguments]
      @calls << call
      call != failing_step
    end
    CloudDSPBootstrapRabbitmq.new(run_command: runner, output: @output, error: @error).run
  end

  def test_preparation_credentials_install_and_verification_are_ordered
    assert_equal 0, run_bootstrap
    assert_equal [
      ['deploy-local-prepare.rb'],
      %w[rabbitmq-secret-stage.rb bootstrap],
      %w[rabbitmq-release.rb install],
      %w[rabbitmq-release.rb verify]
    ], @calls
    assert_includes @output.string, 'remaining services are pending'
  end

  def test_failed_preparation_stops_before_credential_creation
    assert_equal 1, run_bootstrap(['deploy-local-prepare.rb'])
    assert_equal [['deploy-local-prepare.rb']], @calls
    assert_includes @error.string, 'fresh foundation and locked images'
  end

  def test_failed_secret_creation_stops_before_broker_install
    assert_equal 1, run_bootstrap(%w[rabbitmq-secret-stage.rb bootstrap])
    refute_includes @calls, %w[rabbitmq-release.rb install]
    assert_includes @error.string, 'RabbitMQ credential Secret'
  end

  def test_failed_helm_install_does_not_claim_broker_readiness
    assert_equal 1, run_bootstrap(%w[rabbitmq-release.rb install])
    refute_includes @calls, %w[rabbitmq-release.rb verify]
    refute_includes @output.string, 'bootstrap-rabbitmq complete'
  end

  def test_failed_release_verification_does_not_claim_completion
    assert_equal 1, run_bootstrap(%w[rabbitmq-release.rb verify])
    assert_includes @error.string, 'RabbitMQ Helm and PVC verification'
    refute_includes @output.string, 'bootstrap-rabbitmq complete'
  end
end
