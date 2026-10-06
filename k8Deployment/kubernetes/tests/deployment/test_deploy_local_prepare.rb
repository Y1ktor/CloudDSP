require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/orchestration/deploy-local-prepare'

class DeployLocalPrepareTest < Minitest::Test
  def setup
    @calls = []
    @output = StringIO.new
    @error = StringIO.new
  end

  def run_prepare(failing_step = nil)
    runner = lambda do |ruby, script, mode|
      assert_equal RbConfig.ruby, ruby
      call = [File.basename(script), mode]
      @calls << call
      call != failing_step
    end
    CloudDSPPrepare.new(run_command: runner, output: @output, error: @error).run
  end

  def test_fresh_foundation_is_followed_by_image_mirror_and_digest_verification
    assert_equal 0, run_prepare
    assert_equal [
      %w[deploy-local-foundation.rb plan],
      %w[image-registry-stage.rb plan],
      %w[image-registry-stage.rb verify-source],
      %w[deploy-local-foundation.rb bootstrap],
      %w[image-registry-stage.rb mirror],
      %w[image-registry-stage.rb verify]
    ], @calls
    assert_includes @output.string, 'Helm releases remain to install'
  end

  def test_missing_public_image_stops_before_cluster_creation
    assert_equal 1, run_prepare(%w[image-registry-stage.rb verify-source])
    refute_includes @calls, %w[deploy-local-foundation.rb bootstrap]
    assert_includes @error.string, 'public image source'
  end

  def test_failed_mirror_stops_before_claiming_digest_verification
    assert_equal 1, run_prepare(%w[image-registry-stage.rb mirror])
    refute_includes @calls, %w[image-registry-stage.rb verify]
    refute_includes @output.string, 'prepare complete'
  end
end
