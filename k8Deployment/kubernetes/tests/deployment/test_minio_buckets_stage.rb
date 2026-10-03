require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/stages/minio/minio-buckets-stage'

class MinioBucketsStageTest < Minitest::Test
  class FakeStage < MinioBucketsStage
    attr_accessor :buckets, :policy, :objects
    attr_reader :writes

    def initialize
      super(output: StringIO.new, error: StringIO.new)
      @buckets = [UPLOAD_BUCKET, SAMPLE_BUCKET]
      @policy = sample_policy
      @objects = { 'drums/kick.m4a' => 5 }
      @writes = []
    end

    def load_source
      {}
    end

    def root_credentials
      {}
    end

    def sample_objects_from_lock
      { 'drums/kick.m4a' => 5 }
    end

    def aws(_credentials, operation, *arguments, **_options)
      case [operation, arguments.first(2)]
      when ['list-buckets', []]
        { 'Buckets' => @buckets.map { |name| { 'Name' => name } } }
      when ['get-bucket-policy', ['--bucket', UPLOAD_BUCKET]]
        nil
      when ['get-bucket-policy', ['--bucket', SAMPLE_BUCKET]]
        @policy && { 'Policy' => JSON.generate(@policy) }
      when ['get-bucket-notification-configuration', ['--bucket', SAMPLE_BUCKET]]
        {}
      when ['list-objects-v2', ['--bucket', SAMPLE_BUCKET]]
        { 'Contents' => @objects.map { |key, size| { 'Key' => key, 'Size' => size } } }
      when ['put-bucket-policy', ['--bucket', SAMPLE_BUCKET]]
        @writes << arguments
        @policy = JSON.parse(arguments.fetch(3))
        {}
      else
        raise 'unexpected S3 test command'
      end
    end
  end

  def test_committed_catalog_has_reviewed_bucket_and_461_sample_keys
    catalog = MinioBucketsStage.new.send(:sample_objects_from_lock)

    assert_equal 461, catalog.length
    assert catalog.key?('drums/kick.m4a')
  end

  def test_matching_buckets_and_policy_are_no_op
    stage = FakeStage.new

    assert_equal 0, stage.run('reconcile')
    assert_empty stage.writes
  end

  def test_only_absent_sample_policy_can_be_restored
    stage = FakeStage.new
    stage.policy = nil

    assert_equal 1, stage.run('verify')
    assert_empty stage.writes
    assert_equal 0, stage.run('reconcile')
    assert_equal 1, stage.writes.length
    assert_equal stage.send(:sample_policy), stage.policy
  end

  def test_missing_bucket_or_unexpected_sample_object_blocks_policy_write
    missing_bucket = FakeStage.new
    missing_bucket.buckets = [MinioBucketsStage::UPLOAD_BUCKET]
    missing_bucket.policy = nil
    extra_object = FakeStage.new
    extra_object.policy = nil
    extra_object.objects['private/unexpected.wav'] = 5

    assert_equal 1, missing_bucket.run('reconcile')
    assert_equal 1, extra_object.run('reconcile')
    assert_empty missing_bucket.writes
    assert_empty extra_object.writes
  end

  def test_changed_public_policy_blocks_write
    stage = FakeStage.new
    stage.policy = { 'Version' => '2012-10-17', 'Statement' => [{ 'Effect' => 'Allow',
                      'Principal' => '*', 'Action' => 's3:*',
                      'Resource' => 'arn:aws:s3:::clouddsp-midi-samples/*' }] }

    assert_equal 1, stage.run('reconcile')
    assert_empty stage.writes
  end
end
