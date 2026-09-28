require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/deploy-local-minio'

class DeployLocalMinioTest < Minitest::Test
  EXPECTED_CALLS = [
    ['deploy-local-rabbitmq.rb'],
    %w[minio-root-secret-stage.rb bootstrap],
    %w[minio-amqp-secret-stage.rb bootstrap],
    %w[upload-intake-rabbitmq-secret-stage.rb bootstrap],
    %w[rabbitmq-source-intake-bootstrap.rb reconcile],
    %w[rabbitmq-source-intake-bootstrap.rb verify],
    %w[minio-release.rb install],
    %w[minio-release.rb verify],
    %w[job-api-minio-secret-stage.rb bootstrap],
    %w[upload-intake-minio-secret-stage.rb bootstrap],
    %w[demucs-minio-secret-stage.rb bootstrap],
    %w[basic-pitch-minio-secret-stage.rb bootstrap],
    %w[adtof-minio-secret-stage.rb bootstrap],
    %w[minio-fresh-buckets-stage.rb bootstrap],
    %w[minio-fresh-buckets-stage.rb verify],
    %w[minio-fresh-samples-stage.rb bootstrap],
    %w[minio-fresh-samples-stage.rb verify],
    %w[minio-job-api-iam-stage.rb bootstrap],
    %w[minio-job-api-iam-stage.rb verify],
    %w[minio-upload-intake-iam-stage.rb bootstrap],
    %w[minio-upload-intake-iam-stage.rb verify],
    %w[minio-demucs-iam-stage.rb bootstrap],
    %w[minio-demucs-iam-stage.rb verify],
    %w[minio-basic-pitch-iam-stage.rb bootstrap],
    %w[minio-basic-pitch-iam-stage.rb verify],
    %w[minio-adtof-iam-stage.rb bootstrap],
    %w[minio-adtof-iam-stage.rb verify]
  ].freeze

  def setup
    @calls = []
    @output = StringIO.new
    @error = StringIO.new
  end

  def run_bootstrap(failing_call = nil)
    runner = lambda do |ruby, script, *arguments|
      assert_equal RbConfig.ruby, ruby
      call = [File.basename(script), *arguments]
      @calls << call
      call != failing_call
    end
    CloudDSPBootstrapMinio.new(run_command: runner, output: @output, error: @error).run
  end

  def test_broker_secrets_source_intake_and_minio_install_are_ordered
    assert_equal 0, run_bootstrap
    assert_equal EXPECTED_CALLS, @calls
    assert_operator @calls.index(%w[rabbitmq-source-intake-bootstrap.rb verify]), :<,
                    @calls.index(%w[minio-release.rb install])
    assert_operator @calls.index(%w[minio-release.rb verify]), :<,
                    @calls.index(%w[minio-fresh-buckets-stage.rb bootstrap])
    assert_operator @calls.index(%w[job-api-minio-secret-stage.rb bootstrap]), :<,
                    @calls.index(%w[minio-fresh-buckets-stage.rb bootstrap])
    assert_operator @calls.index(%w[upload-intake-minio-secret-stage.rb bootstrap]), :<,
                    @calls.index(%w[minio-fresh-buckets-stage.rb bootstrap])
    assert_operator @calls.index(%w[demucs-minio-secret-stage.rb bootstrap]), :<,
                    @calls.index(%w[minio-fresh-buckets-stage.rb bootstrap])
    assert_operator @calls.index(%w[basic-pitch-minio-secret-stage.rb bootstrap]), :<,
                    @calls.index(%w[minio-fresh-buckets-stage.rb bootstrap])
    assert_operator @calls.index(%w[adtof-minio-secret-stage.rb bootstrap]), :<,
                    @calls.index(%w[minio-fresh-buckets-stage.rb bootstrap])
    assert_operator @calls.index(%w[minio-fresh-buckets-stage.rb verify]), :<,
                    @calls.index(%w[minio-fresh-samples-stage.rb bootstrap])
    assert_operator @calls.index(%w[minio-fresh-samples-stage.rb verify]), :<,
                    @calls.index(%w[minio-job-api-iam-stage.rb bootstrap])
    assert_operator @calls.index(%w[minio-job-api-iam-stage.rb verify]), :<,
                    @calls.index(%w[minio-upload-intake-iam-stage.rb bootstrap])
    assert_operator @calls.index(%w[minio-upload-intake-iam-stage.rb verify]), :<,
                    @calls.index(%w[minio-demucs-iam-stage.rb bootstrap])
    assert_operator @calls.index(%w[minio-demucs-iam-stage.rb verify]), :<,
                    @calls.index(%w[minio-basic-pitch-iam-stage.rb bootstrap])
    assert_operator @calls.index(%w[minio-basic-pitch-iam-stage.rb verify]), :<,
                    @calls.index(%w[minio-adtof-iam-stage.rb bootstrap])
    assert_includes @output.string, 'application orchestration remains pending'
    assert_empty @error.string
  end

  def test_every_failed_stage_stops_before_the_next_write_or_success_claim
    EXPECTED_CALLS.each_with_index do |failing_call, index|
      @calls.clear
      @output.truncate(0)
      @output.rewind
      @error.truncate(0)
      @error.rewind

      assert_equal 1, run_bootstrap(failing_call), "stage #{index + 1} should stop"
      assert_equal EXPECTED_CALLS.take(index + 1), @calls
      assert_includes @error.string, 'inspect that stage before retrying'
      refute_includes @output.string, 'bootstrap-minio complete'
    end
  end
end
