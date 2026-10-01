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

  class MirrorStage < CloudDSPImageRegistryStage
    attr_accessor :tag_digest, :expected_digest, :source_manifest, :copy_succeeds
    attr_reader :docker_calls

    def initialize
      super(output: StringIO.new, error: StringIO.new)
      @docker_calls = []
      @copy_succeeds = true
    end

    private

    def local_manifest_digest(_repository, reference)
      return @tag_digest unless reference.start_with?('sha256:')

      @tag_digest == @expected_digest ? @tag_digest : nil
    end

    def docker(*arguments)
      @docker_calls << arguments
      case arguments.first
      when 'manifest'
        @source_manifest.to_json
      when 'buildx'
        @tag_digest = @expected_digest if @copy_succeeds
        ''
      else
        raise "unexpected Docker command: #{arguments.first}"
      end
    end
  end

  def test_all_locked_local_images_have_distinct_public_tags
    entries = FakeStage.new.send(:load_entries)

    assert_equal 19, entries.length
    assert_equal entries.length, entries.map { |entry| entry.fetch(:hub_tag) }.uniq.length
    job_api = entries.find { |entry| entry.fetch(:key) == 'job-api' }
    assert_equal 'job-api-0.0.9-demucs-timeout-message', job_api.fetch(:hub_tag)
    assert_equal 'job-api', job_api.fetch(:local_repo)
    minio_mc = entries.find { |entry| entry.fetch(:key) == 'minio-mc' }
    assert_equal 'minio-mc-release-2025-08-13', minio_mc.fetch(:hub_tag)
    assert_equal 'minio-mc', minio_mc.fetch(:local_repo)
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

  def test_docker_failure_detail_keeps_reason_and_redacts_credentials_and_url_query
    stage = FakeStage.new

    detail = stage.send(:docker_error_detail, '',
                        "push failed: token=private-value https://registry.local/v2/repo?state=private-state\n")

    assert_equal 'push failed: token=[redacted] https://registry.local/v2/repo?[redacted]', detail
    refute_includes detail, 'private-value'
    refute_includes detail, 'private-state'
  end

  def test_docker_failure_detail_uses_stdout_when_stderr_is_empty
    stage = FakeStage.new

    assert_equal 'no space left on device', stage.send(:docker_error_detail, "progress\nno space left on device\n", '')
  end

  def test_mirror_repairs_only_a_tag_pointing_to_a_child_of_its_locked_index
    entry = MirrorStage.new.send(:load_entries).find { |image| image.fetch(:key) == 'minio' }
    child_digest = 'sha256:' + ('1' * 64)
    stage = MirrorStage.new
    stage.expected_digest = entry.fetch(:digest)
    stage.tag_digest = child_digest
    stage.source_manifest = {
      mediaType: CloudDSPImageRegistryStage::INDEX_MEDIA_TYPES.first,
      manifests: [{ digest: child_digest }]
    }

    stage.send(:mirror, entry)

    assert_equal ['manifest', 'inspect', "y1ktor/clouddsp@#{entry.fetch(:digest)}"], stage.docker_calls.first
    assert_equal ['buildx', 'imagetools', 'create', '--prefer-index=false', '--tag',
                  '127.0.0.1:5001/minio:release-2025-10-15', "y1ktor/clouddsp@#{entry.fetch(:digest)}"],
                 stage.docker_calls.last
    assert_equal entry.fetch(:digest), stage.tag_digest

    stage.tag_digest = 'sha256:' + ('2' * 64)
    stage.docker_calls.clear
    assert_raises(RuntimeError) { stage.send(:mirror, entry) }
    refute stage.docker_calls.any? { |call| call.first == 'buildx' }
  end

  def test_mirror_checks_digest_after_copying_a_single_manifest
    entry = MirrorStage.new.send(:load_entries).first
    stage = MirrorStage.new
    stage.expected_digest = entry.fetch(:digest)
    stage.copy_succeeds = false

    assert_raises(RuntimeError) { stage.send(:mirror, entry) }
    assert_equal 'buildx', stage.docker_calls.first.first
    refute stage.docker_calls.any? { |call| call.first == 'manifest' }
  end
end
