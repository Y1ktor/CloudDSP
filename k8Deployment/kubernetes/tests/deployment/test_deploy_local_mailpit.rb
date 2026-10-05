require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/orchestration/deploy-local-mailpit'

class DeployLocalMailpitTest < Minitest::Test
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
    CloudDSPBootstrapMailpit.new(run_command: runner, output: @output, error: @error).run
  end

  def test_prepare_install_and_verify_run_in_dependency_order
    assert_equal 0, run_bootstrap
    assert_equal [
      ['deploy-local-prepare.rb'],
      %w[mailpit-release.rb install],
      %w[mailpit-release.rb verify]
    ], @calls
    assert_includes @output.string, 'remaining application stages are pending'
  end

  def test_failed_preparation_does_not_install_mailpit
    assert_equal 1, run_bootstrap(['deploy-local-prepare.rb'])
    assert_equal [['deploy-local-prepare.rb']], @calls
    assert_includes @error.string, 'fresh foundation and locked images'
  end

  def test_failed_install_does_not_claim_release_verification
    assert_equal 1, run_bootstrap(%w[mailpit-release.rb install])
    refute_includes @calls, %w[mailpit-release.rb verify]
    refute_includes @output.string, 'bootstrap-mailpit complete'
  end
end
