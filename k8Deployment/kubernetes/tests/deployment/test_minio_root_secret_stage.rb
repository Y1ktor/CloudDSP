require 'base64'
require 'json'
require 'minitest/autorun'
require 'stringio'
require 'tmpdir'
require 'yaml'

require_relative '../../scripts/minio-root-secret-stage'

class MinioRootSecretStageTest < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  # The fake returns only the API shapes needed by the stage. Its call trace
  # proves a fresh create has a server dry run and an occupied Secret has no
  # write, without reading or changing the user's live administrator account.
  class FakeKubernetes
    attr_accessor :secret
    attr_reader :calls

    def initialize(source)
      @source = source
      @secret = nil
      @calls = []
    end

    def call(*arguments)
      @calls << arguments
      case arguments[5]
      when 'get'
        [@secret ? JSON.generate(@secret) : '', '', Status.new(0)]
      when 'create'
        @secret = live_source unless arguments.include?('--dry-run=server')
        ['', '', Status.new(0)]
      else
        raise "unexpected fake command #{arguments[5]}"
      end
    end

    def live_source
      { 'kind' => 'Secret', 'metadata' => @source.fetch('metadata'),
        'type' => 'Opaque',
        'data' => @source.fetch('stringData').transform_values { |value| Base64.strict_encode64(value) } }
    end
  end

  def with_stage
    Dir.mktmpdir('clouddsp-minio-root-secret-test-') do |directory|
      source = YAML.safe_load(CloudDSPMinioRootSecretStage::EXAMPLE_PATH.read)
      source.fetch('stringData')['MINIO_ROOT_USER'] = 'local_test_admin'
      source.fetch('stringData')['MINIO_ROOT_PASSWORD'] = 'local-only-test-password'
      path = File.join(directory, 'minio-root.secret.yaml')
      File.write(path, YAML.dump(source))
      fake = FakeKubernetes.new(source)
      output = StringIO.new
      error = StringIO.new
      stage = CloudDSPMinioRootSecretStage.new(command: fake.method(:call), local_path: path,
                                               output: output, error: error)
      yield stage, fake, source, path, output, error
    end
  end

  def test_plan_requires_absence_and_does_not_write
    with_stage do |stage, fake, _source, _path, output, _error|
      assert_equal 0, stage.run('plan')
      assert_equal ['get'], fake.calls.map { |call| call[5] }
      assert_includes output.string, 'creation pending'
    end
  end

  def test_bootstrap_server_validates_creates_and_verifies_secret
    with_stage do |stage, fake, _source, _path, output, _error|
      assert_equal 0, stage.run('bootstrap')
      assert_equal %w[get create create get], fake.calls.map { |call| call[5] }
      assert_includes fake.calls[1], '--dry-run=server'
      refute_includes fake.calls[2], '--dry-run=server'
      assert_equal 0, stage.run('verify')
      assert_includes output.string, 'created and verified'
    end
  end

  def test_existing_secret_blocks_bootstrap
    with_stage do |stage, fake, _source, _path, _output, error|
      fake.secret = fake.live_source
      assert_equal 1, stage.run('bootstrap')
      assert_equal ['get'], fake.calls.map { |call| call[5] }
      assert_includes error.string, 'already exists'
    end
  end

  def test_placeholders_or_invalid_values_block_before_lookup
    with_stage do |stage, fake, source, path, _output, error|
      source.fetch('stringData')['MINIO_ROOT_PASSWORD'] = 'REPLACE_WITH_A_UNIQUE_LOCAL_ROOT_PASSWORD'
      File.write(path, YAML.dump(source))
      assert_equal 1, stage.run('bootstrap')
      assert_empty fake.calls
      assert_includes error.string, 'placeholder credential'

      source.fetch('stringData')['MINIO_ROOT_PASSWORD'] = "bad\nvalue"
      File.write(path, YAML.dump(source))
      assert_equal 1, stage.run('bootstrap')
      assert_empty fake.calls
      assert_includes error.string, 'empty or invalid value'
    end
  end

  def test_verify_rejects_drift_without_printing_credentials
    with_stage do |stage, fake, _source, _path, output, error|
      fake.secret = fake.live_source
      fake.secret.fetch('data')['MINIO_ROOT_PASSWORD'] = Base64.strict_encode64('different-password')
      assert_equal 1, stage.run('verify')
      assert_includes error.string, 'differs from ignored local source'
      refute_includes output.string + error.string, 'different-password'
    end
  end
end
