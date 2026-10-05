require 'base64'
require 'json'
require 'minitest/autorun'
require 'stringio'
require 'tmpdir'
require 'yaml'

require_relative '../../scripts/stages/credentials/upload-intake-rabbitmq-secret-stage'

class UploadIntakeRabbitmqSecretStageTest < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  # Model the API server's stringData conversion and record whether any
  # Kubernetes write occurred. The companion bootstrap source remains a
  # local file here; this stage must never create its temporary Secret.
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
    Dir.mktmpdir('clouddsp-upload-intake-rabbitmq-secret-test-') do |directory|
      runtime = YAML.safe_load(CloudDSPUploadIntakeRabbitmqSecretStage::EXAMPLE_PATH.read)
      bootstrap = YAML.safe_load(CloudDSPUploadIntakeRabbitmqSecretStage::BOOTSTRAP_EXAMPLE_PATH.read)
      runtime.fetch('stringData')['RABBITMQ_UPLOAD_INTAKE_PASSWORD'] = 'local-only-test-password'
      bootstrap.fetch('stringData')['RABBITMQ_UPLOAD_INTAKE_PASSWORD'] = 'local-only-test-password'
      runtime_path = File.join(directory, 'runtime.secret.yaml')
      bootstrap_path = File.join(directory, 'bootstrap.secret.yaml')
      File.write(runtime_path, YAML.dump(runtime))
      File.write(bootstrap_path, YAML.dump(bootstrap))
      fake = FakeKubernetes.new(runtime)
      output = StringIO.new
      error = StringIO.new
      stage = CloudDSPUploadIntakeRabbitmqSecretStage.new(
        command: fake.method(:call), local_path: runtime_path,
        bootstrap_local_path: bootstrap_path, output: output, error: error
      )
      yield stage, fake, runtime, bootstrap, runtime_path, bootstrap_path, output, error
    end
  end

  def test_plan_requires_absence_without_creating_either_secret
    with_stage do |stage, fake, _runtime, _bootstrap, _runtime_path, _bootstrap_path, output, _error|
      assert_equal 0, stage.run('plan')
      assert_equal ['get'], fake.calls.map { |call| call[5] }
      assert_includes output.string, 'creation pending'
    end
  end

  def test_bootstrap_server_validates_creates_runtime_secret_and_verifies
    with_stage do |stage, fake, _runtime, _bootstrap, runtime_path, bootstrap_path, output, _error|
      assert_equal 0, stage.run('bootstrap')
      assert_equal %w[get create create get], fake.calls.map { |call| call[5] }
      assert_includes fake.calls[1], '--dry-run=server'
      refute_includes fake.calls[2], '--dry-run=server'
      assert_includes fake.calls[2], runtime_path
      refute_includes fake.calls[2], bootstrap_path
      assert_equal 0, stage.run('verify')
      assert_includes output.string, 'created and verified'
    end
  end

  def test_existing_runtime_secret_blocks_bootstrap
    with_stage do |stage, fake, _runtime, _bootstrap, _runtime_path, _bootstrap_path, _output, error|
      fake.secret = fake.live_source
      assert_equal 1, stage.run('bootstrap')
      assert_equal ['get'], fake.calls.map { |call| call[5] }
      assert_includes error.string, 'already exists'
    end
  end

  def test_placeholder_or_companion_mismatch_blocks_before_lookup
    with_stage do |stage, fake, runtime, bootstrap, runtime_path, bootstrap_path, _output, error|
      runtime.fetch('stringData')['RABBITMQ_UPLOAD_INTAKE_PASSWORD'] =
        'REPLACE_WITH_A_UNIQUE_LOCAL_UPLOAD_INTAKE_PASSWORD'
      File.write(runtime_path, YAML.dump(runtime))
      assert_equal 1, stage.run('bootstrap')
      assert_empty fake.calls
      assert_includes error.string, 'placeholder password'

      runtime.fetch('stringData')['RABBITMQ_UPLOAD_INTAKE_PASSWORD'] = 'local-only-test-password'
      bootstrap.fetch('stringData')['RABBITMQ_UPLOAD_INTAKE_PASSWORD'] = 'different-password'
      File.write(runtime_path, YAML.dump(runtime))
      File.write(bootstrap_path, YAML.dump(bootstrap))
      assert_equal 1, stage.run('bootstrap')
      assert_empty fake.calls
      assert_includes error.string, 'runtime/bootstrap credentials differ'
      refute_includes error.string, 'different-password'
    end
  end

  def test_verify_rejects_drift_without_printing_credentials
    with_stage do |stage, fake, _runtime, _bootstrap, _runtime_path, _bootstrap_path, output, error|
      fake.secret = fake.live_source
      fake.secret.fetch('data')['RABBITMQ_UPLOAD_INTAKE_PASSWORD'] = Base64.strict_encode64('different-password')
      assert_equal 1, stage.run('verify')
      assert_includes error.string, 'differs from ignored local source'
      refute_includes output.string + error.string, 'different-password'
    end
  end
end
