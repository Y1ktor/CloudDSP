require 'base64'
require 'json'
require 'minitest/autorun'
require 'stringio'
require 'tmpdir'
require 'yaml'
require_relative '../../scripts/keycloak-admin-secret-stage'

class KeycloakAdminSecretStageTest < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  # Kubernetes converts stringData to encoded data after create. The fake
  # models that one conversion so verify exercises the real source/live
  # comparison without contacting the retained Keycloak installation.
  class FakeKubernetes
    attr_accessor :secret, :fail_dry_run
    attr_reader :calls

    def initialize(source)
      @source = source
      @secret = nil
      @fail_dry_run = false
      @calls = []
    end

    def call(*arguments)
      @calls << arguments
      case arguments[5]
      when 'get'
        [@secret ? JSON.generate(@secret) : '', '', Status.new(0)]
      when 'create'
        return ['', 'sensitive fake server output', Status.new(1)] if @fail_dry_run && arguments.include?('--dry-run=server')
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
    Dir.mktmpdir('clouddsp-keycloak-admin-secret-test-') do |directory|
      source = YAML.safe_load(CloudDSPKeycloakAdminSecretStage::EXAMPLE_PATH.read)
      source.fetch('stringData')['KC_BOOTSTRAP_ADMIN_USERNAME'] = 'test-admin'
      source.fetch('stringData')['KC_BOOTSTRAP_ADMIN_PASSWORD'] = 'test-only-local-password'
      path = File.join(directory, 'keycloak-bootstrap-admin.secret.yaml')
      File.write(path, YAML.dump(source))
      fake = FakeKubernetes.new(source)
      output = StringIO.new
      error = StringIO.new
      stage = CloudDSPKeycloakAdminSecretStage.new(command: fake.method(:call),
                                                   local_path: path, output: output, error: error)
      yield stage, fake, source, path, output, error
    end
  end

  def test_plan_validates_source_and_absence_without_writes
    with_stage do |stage, fake, _source, _path, output, _error|
      assert_equal 0, stage.run('plan')
      assert_equal ['get'], fake.calls.map { |call| call[5] }
      assert_includes output.string, 'creation pending'
    end
  end

  def test_bootstrap_validates_creates_and_verifies_exact_secret
    with_stage do |stage, fake, _source, path, output, _error|
      assert_equal 0, stage.run('bootstrap')
      assert_equal %w[get create create get], fake.calls.map { |call| call[5] }
      assert_includes fake.calls[1], '--dry-run=server'
      assert_equal path, fake.calls[2].last
      assert_equal 0, stage.run('verify')
      assert_includes output.string, 'created and verified'
    end
  end

  def test_existing_secret_blocks_fresh_creation_even_when_matching
    with_stage do |stage, fake, _source, _path, _output, error|
      fake.secret = fake.live_source
      assert_equal 1, stage.run('bootstrap')
      assert_equal ['get'], fake.calls.map { |call| call[5] }
      assert_includes error.string, 'already exists'
    end
  end

  def test_placeholder_username_or_password_blocks_before_cluster_lookup
    with_stage do |stage, fake, source, path, _output, error|
      example = YAML.safe_load(CloudDSPKeycloakAdminSecretStage::EXAMPLE_PATH.read)
      %w[KC_BOOTSTRAP_ADMIN_USERNAME KC_BOOTSTRAP_ADMIN_PASSWORD].each do |key|
        source.fetch('stringData')[key] = example.fetch('stringData').fetch(key)
        File.write(path, YAML.dump(source))
        assert_equal 1, stage.run('bootstrap')
        assert_empty fake.calls
        assert_includes error.string, 'placeholder value'
        source.fetch('stringData')[key] = key.end_with?('USERNAME') ? 'test-admin' : 'test-only-local-password'
      end
    end
  end

  def test_metadata_drift_and_server_rejection_stop_before_write
    with_stage do |stage, fake, source, path, output, error|
      source.fetch('metadata')['namespace'] = 'clouddsp-app'
      File.write(path, YAML.dump(source))
      assert_equal 1, stage.run('bootstrap')
      assert_empty fake.calls

      source.fetch('metadata')['namespace'] = 'clouddsp-data'
      File.write(path, YAML.dump(source))
      fake.fail_dry_run = true
      assert_equal 1, stage.run('bootstrap')
      assert_nil fake.secret
      refute_includes output.string + error.string, 'sensitive fake server output'
    end
  end

  def test_verify_rejects_live_secret_drift_without_printing_credentials
    with_stage do |stage, fake, _source, _path, output, error|
      fake.secret = fake.live_source
      fake.secret.fetch('data')['KC_BOOTSTRAP_ADMIN_PASSWORD'] = Base64.strict_encode64('different-test-password')
      assert_equal 1, stage.run('verify')
      assert_includes error.string, 'differs from ignored local source'
      refute_includes output.string + error.string, 'different-test-password'
    end
  end
end
