require 'minitest/autorun'

require_relative '../../scripts/releases/scaling-auth-release'

class ScalingAuthInstallTest < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  def test_fresh_install_verifies_both_external_scaler_identities
    commands = []
    runner = lambda do |*arguments, stdin_data: nil, chdir: nil|
      assert_nil stdin_data
      assert_equal File.expand_path('../../../..', __dir__), chdir.to_s
      commands << arguments
      ['', '', Status.new(0)]
    end
    stage = ScalingAuthRelease.new(command: runner)
    stage.instance_variable_set(:@source, {
      'clouddsp-rabbitmq-scaler-authentication' => {},
      'clouddsp-demucs-postgresql-scaler-authentication' => {}
    })
    stage.define_singleton_method(:release_record) { nil }
    stage.define_singleton_method(:kubectl) { |_argument, *_rest| '' }

    stage.send(:check_fresh_install_boundary)

    assert_equal [
      %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb rabbitmq keda-scaler verify],
      %w[ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb database keda-demucs verify]
    ], commands
  end
end
