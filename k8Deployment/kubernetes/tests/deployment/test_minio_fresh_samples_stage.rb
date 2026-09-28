require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/minio-fresh-samples-stage'

class MinioFreshSamplesStageTest < Minitest::Test
  class Status
    def initialize(success)
      @success = success
    end

    def success?
      @success
    end
  end

  class FakeStage < MinioFreshSamplesStage
    attr_accessor :buckets, :objects, :sample_policy, :release_ready, :mirror_ready
    attr_reader :calls

    def initialize
      @calls = []
      @buckets = [UPLOAD_BUCKET, SAMPLE_BUCKET]
      @objects = []
      @sample_policy = nil
      @release_ready = true
      @mirror_ready = true
      super(command: method(:fake_command), output: StringIO.new, error: StringIO.new)
    end

    def load_source
      {}
    end

    def root_credentials
      {}
    end

    def aws(_credentials, operation, *arguments, **_options)
      @calls << [operation, *arguments]
      case operation
      when 'list-buckets'
        { 'Buckets' => @buckets.map { |name| { 'Name' => name } } }
      when 'get-bucket-policy'
        arguments.fetch(1) == SAMPLE_BUCKET ? @sample_policy : nil
      when 'get-bucket-notification-configuration'
        {}
      when 'list-objects-v2'
        { 'Contents' => @objects.map { |key| { 'Key' => key } } }
      else
        raise 'unexpected S3 test command'
      end
    end

    def fake_command(*arguments)
      @calls << arguments
      success = if arguments.first == 'python3'
                  @mirror_ready
                elsif arguments[1] == RELEASE_STAGE.to_s
                  @release_ready
                else
                  true
                end
      ['', '', Status.new(success)]
    end
  end

  def test_fresh_mirror_runs_after_empty_private_boundary_and_before_verification
    stage = FakeStage.new

    assert_equal 0, stage.run('plan')
    refute stage.calls.any? { |call| call.first == 'python3' }
    stage.calls.clear
    assert_equal 0, stage.run('bootstrap')
    mirror_index = stage.calls.index { |call| call.first == 'python3' }
    assert_equal ['python3', FakeStage::MIRROR.to_s, '--fresh-bootstrap'], stage.calls.fetch(mirror_index)
    assert_operator stage.calls.index { |call| call.first == 'list-objects-v2' }, :<, mirror_index
    assert_equal [RbConfig.ruby, FakeStage::BUCKET_STAGE.to_s, 'verify'], stage.calls.last
  end

  def test_existing_samples_or_policy_block_mirror
    { objects: ['drums/kick.m4a'], sample_policy: { 'Policy' => '{}' } }.each do |field, value|
      stage = FakeStage.new
      stage.public_send("#{field}=", value)
      assert_equal 1, stage.run('bootstrap')
      refute stage.calls.any? { |call| call.first == 'python3' }
    end
  end

  def test_missing_bucket_or_release_blocks_mirror
    missing_bucket = FakeStage.new
    missing_bucket.buckets = [FakeStage::UPLOAD_BUCKET]
    missing_release = FakeStage.new
    missing_release.release_ready = false
    [missing_bucket, missing_release].each do |stage|
      assert_equal 1, stage.run('bootstrap')
      refute stage.calls.any? { |call| call.first == 'python3' }
    end
  end

  def test_mirror_failure_stops_before_success_verification
    stage = FakeStage.new
    stage.mirror_ready = false

    assert_equal 1, stage.run('bootstrap')
    refute stage.calls.any? { |call| call.include?(FakeStage::BUCKET_STAGE.to_s) }
  end

  def test_verify_is_read_only
    stage = FakeStage.new

    assert_equal 0, stage.run('verify')
    refute stage.calls.any? { |call| call.first == 'python3' || call.first == 'list-objects-v2' }
  end
end
