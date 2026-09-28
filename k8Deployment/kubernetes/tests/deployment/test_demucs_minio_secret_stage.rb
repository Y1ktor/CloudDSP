require 'base64'
require 'json'
require 'minitest/autorun'
require 'stringio'
require 'tmpdir'
require 'yaml'

require_relative '../../scripts/demucs-minio-secret-stage'

class DemucsMinioSecretStageTest < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  # Simulate Kubernetes stringData conversion. The companion provisioning
  # source is checked locally but must not be created by this runtime stage.
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
      { 'kind' => 'Secret', 'metadata' => @source.fetch('metadata'), 'type' => 'Opaque',
        'data' => @source.fetch('stringData').transform_values { |value| Base64.strict_encode64(value) } }
    end
  end

  def with_stage
    Dir.mktmpdir('clouddsp-demucs-minio-secret-test-') do |directory|
      runtime = YAML.safe_load(CloudDSPDemucsMinioSecretStage::EXAMPLE_PATH.read)
      bootstrap = YAML.safe_load(CloudDSPDemucsMinioSecretStage::BOOTSTRAP_EXAMPLE_PATH.read)
      runtime.fetch('stringData')['DEMUCS_S3_SECRET_KEY'] = 'local-only-test-key'
      bootstrap.fetch('stringData')['DEMUCS_S3_SECRET_KEY'] = 'local-only-test-key'
      runtime_path = File.join(directory, 'runtime.secret.yaml')
      bootstrap_path = File.join(directory, 'bootstrap.secret.yaml')
      File.write(runtime_path, YAML.dump(runtime))
      File.write(bootstrap_path, YAML.dump(bootstrap))
      fake = FakeKubernetes.new(runtime)
      output = StringIO.new
      error = StringIO.new
      stage = CloudDSPDemucsMinioSecretStage.new(
        command: fake.method(:call), local_path: runtime_path,
        bootstrap_local_path: bootstrap_path, output: output, error: error
      )
      yield stage, fake, runtime, bootstrap, runtime_path, bootstrap_path, output, error
    end
  end

  def test_plan_checks_local_pair_without_creating_a_secret
    with_stage do |stage, fake, _runtime, _bootstrap, _runtime_path, _bootstrap_path, output, _error|
      assert_equal 0, stage.run('plan')
      assert_equal ['get'], fake.calls.map { |call| call[5] }
      assert_includes output.string, 'creation pending'
    end
  end

  def test_bootstrap_creates_only_runtime_secret_after_server_validation
    with_stage do |stage, fake, _runtime, _bootstrap, runtime_path, bootstrap_path, output, _error|
      assert_equal 0, stage.run('bootstrap')
      assert_equal %w[get create create get], fake.calls.map { |call| call[5] }
      assert_includes fake.calls[1], '--dry-run=server'
      assert_includes fake.calls[2], runtime_path
      refute_includes fake.calls[2], bootstrap_path
      assert_equal 0, stage.run('verify')
      assert_includes output.string, 'created and verified'
    end
  end

  def test_existing_runtime_secret_blocks_fresh_bootstrap
    with_stage do |stage, fake, _runtime, _bootstrap, _runtime_path, _bootstrap_path, _output, error|
      fake.secret = fake.live_source
      assert_equal 1, stage.run('bootstrap')
      assert_equal ['get'], fake.calls.map { |call| call[5] }
      assert_includes error.string, 'already exists'
    end
  end

  def test_placeholder_or_mismatched_companion_blocks_before_cluster_lookup
    with_stage do |stage, fake, runtime, bootstrap, runtime_path, bootstrap_path, output, error|
      runtime.fetch('stringData')['DEMUCS_S3_SECRET_KEY'] =
        'REPLACE_WITH_A_UNIQUE_LOCAL_DEMUCS_MINIO_SECRET_KEY'
      File.write(runtime_path, YAML.dump(runtime))
      assert_equal 1, stage.run('bootstrap')
      assert_empty fake.calls
      assert_includes error.string, 'placeholder key'

      runtime.fetch('stringData')['DEMUCS_S3_SECRET_KEY'] = 'local-only-test-key'
      bootstrap.fetch('stringData')['DEMUCS_S3_SECRET_KEY'] = 'different-local-key'
      File.write(runtime_path, YAML.dump(runtime))
      File.write(bootstrap_path, YAML.dump(bootstrap))
      assert_equal 1, stage.run('bootstrap')
      assert_empty fake.calls
      refute_includes output.string + error.string, 'different-local-key'
      assert_includes error.string, 'runtime/bootstrap credentials differ'
    end
  end

  def test_bootstrap_companion_must_use_reviewed_data_namespace_contract
    with_stage do |stage, fake, _runtime, bootstrap, _runtime_path, bootstrap_path, _output, error|
      bootstrap.fetch('metadata')['namespace'] = 'clouddsp-app'
      File.write(bootstrap_path, YAML.dump(bootstrap))

      assert_equal 1, stage.run('bootstrap')
      assert_empty fake.calls
      assert_includes error.string, 'bootstrap Secret source contract differs from example'
    end
  end

  def test_verify_rejects_drift_without_printing_keys
    with_stage do |stage, fake, _runtime, _bootstrap, _runtime_path, _bootstrap_path, output, error|
      fake.secret = fake.live_source
      fake.secret.fetch('data')['DEMUCS_S3_SECRET_KEY'] = Base64.strict_encode64('different-local-key')
      assert_equal 1, stage.run('verify')
      assert_includes error.string, 'differs from ignored local source'
      refute_includes output.string + error.string, 'different-local-key'
    end
  end
end
