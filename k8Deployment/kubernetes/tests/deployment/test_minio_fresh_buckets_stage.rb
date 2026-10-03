require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/stages/minio/minio-fresh-buckets-stage'

class MinioFreshBucketsStageTest < Minitest::Test
  class FakeStage < MinioFreshBucketsStage
    attr_accessor :buckets, :release_ready, :public_policy, :fail_create
    attr_reader :writes

    def initialize
      super(output: StringIO.new, error: StringIO.new)
      @buckets = []
      @release_ready = true
      @public_policy = nil
      @fail_create = nil
      @writes = []
    end

    def load_source
      {}
    end

    def verify_release
      raise 'MinIO Helm release verification failed' unless @release_ready
    end

    def root_credentials
      {}
    end

    def aws(_credentials, operation, *arguments, **_options)
      case operation
      when 'list-buckets'
        { 'Buckets' => @buckets.map { |name| { 'Name' => name } } }
      when 'create-bucket'
        name = arguments.fetch(1)
        raise 'S3 create-bucket failed' if name == @fail_create

        @writes << name
        @buckets << name
        {}
      when 'head-bucket'
        raise 'missing bucket' unless @buckets.include?(arguments.fetch(1))

        {}
      when 'get-bucket-policy'
        arguments.fetch(1) == SAMPLE_BUCKET && @public_policy ? { 'Policy' => JSON.generate(@public_policy) } : nil
      when 'get-bucket-notification-configuration'
        {}
      else
        raise 'unexpected S3 test command'
      end
    end
  end

  def test_plan_is_read_only_and_bootstrap_creates_only_two_private_buckets
    stage = FakeStage.new

    assert_equal 0, stage.run('plan')
    assert_empty stage.writes
    assert_equal 0, stage.run('bootstrap')
    assert_equal [FakeStage::UPLOAD_BUCKET, FakeStage::SAMPLE_BUCKET], stage.writes
    assert_equal 0, stage.run('verify')
  end

  def test_existing_or_partially_missing_buckets_block_creation
    [FakeStage::UPLOAD_BUCKET, FakeStage::SAMPLE_BUCKET].each do |name|
      stage = FakeStage.new
      stage.buckets = [name]

      assert_equal 1, stage.run('bootstrap')
      assert_empty stage.writes
    end
    stage = FakeStage.new
    stage.buckets = [FakeStage::UPLOAD_BUCKET, FakeStage::SAMPLE_BUCKET]
    assert_equal 1, stage.run('bootstrap')
    assert_empty stage.writes
  end

  def test_missing_release_blocks_before_any_bucket_write
    stage = FakeStage.new
    stage.release_ready = false

    assert_equal 1, stage.run('bootstrap')
    assert_empty stage.writes
  end

  def test_second_bucket_failure_leaves_partial_state_for_inspection
    stage = FakeStage.new
    stage.fail_create = FakeStage::SAMPLE_BUCKET

    assert_equal 1, stage.run('bootstrap')
    assert_equal [FakeStage::UPLOAD_BUCKET], stage.writes
    assert_equal 1, stage.run('bootstrap')
    assert_equal [FakeStage::UPLOAD_BUCKET], stage.writes
  end

  def test_verify_rejects_unreviewed_public_sample_policy
    stage = FakeStage.new
    stage.buckets = [FakeStage::UPLOAD_BUCKET, FakeStage::SAMPLE_BUCKET]
    stage.public_policy = { 'Version' => '2012-10-17', 'Statement' => [
      { 'Effect' => 'Allow', 'Principal' => '*', 'Action' => 's3:*',
        'Resource' => "arn:aws:s3:::#{FakeStage::SAMPLE_BUCKET}/*" }
    ] }

    assert_equal 1, stage.run('verify')
    assert_empty stage.writes
  end
end
