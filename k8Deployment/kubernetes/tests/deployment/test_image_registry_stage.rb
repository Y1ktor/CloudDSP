require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/image-registry-stage'

class ImageRegistryStageTest < Minitest::Test
  class FakeStage < CloudDSPImageRegistryStage
    attr_accessor :local_digest, :hub_digest
    attr_reader :actions

    def initialize
      super(output: StringIO.new, error: StringIO.new)
      @actions = []
    end

    private

    def local_manifest_digest(_repository, _reference)
      @local_digest
    end

    def hub_manifest_digest(_tag)
      @hub_digest
    end

    def publish(entry)
      @actions << [:publish, entry.fetch(:key)]
    end

    def mirror(entry)
      @actions << [:mirror, entry.fetch(:key)]
    end
  end

  def test_all_locked_local_images_have_distinct_public_tags
    entries = FakeStage.new.send(:load_entries)

    assert_equal 18, entries.length
    assert_equal entries.length, entries.map { |entry| entry.fetch(:hub_tag) }.uniq.length
    job_api = entries.find { |entry| entry.fetch(:key) == 'job-api' }
    assert_equal 'job-api-0.0.9-demucs-timeout-message', job_api.fetch(:hub_tag)
    assert_equal 'job-api', job_api.fetch(:local_repo)
  end

  def test_existing_hub_tag_with_wrong_digest_blocks_publish
    stage = FakeStage.new
    entry = stage.send(:load_entries).first
    stage.local_digest = entry.fetch(:digest)
    stage.hub_digest = 'sha256:' + ('0' * 64)

    assert_raises(RuntimeError) { stage.send(:process, 'publish', entry) }
    assert_empty stage.actions
  end

  def test_missing_local_manifest_can_be_mirrored_only_from_matching_hub_digest
    stage = FakeStage.new
    entry = stage.send(:load_entries).first
    stage.local_digest = nil
    stage.hub_digest = entry.fetch(:digest)

    stage.send(:process, 'mirror', entry)

    assert_equal [[:mirror, entry.fetch(:key)]], stage.actions
  end

  def test_public_source_verification_does_not_require_local_registry
    stage = FakeStage.new
    entry = stage.send(:load_entries).first
    stage.hub_digest = entry.fetch(:digest)
    stage.local_digest = nil

    stage.send(:process, 'verify-source', entry)
    assert_empty stage.actions

    stage.hub_digest = nil
    assert_raises(RuntimeError) { stage.send(:process, 'verify-source', entry) }
  end
end
