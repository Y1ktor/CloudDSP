require 'minitest/autorun'
require 'stringio'
require 'tmpdir'
require 'uri'
require 'yaml'

require_relative '../../scripts/credential-init'
require_relative '../../scripts/postgresql-secret-stage'
require_relative '../../scripts/rabbitmq-secret-stage'
require_relative '../../scripts/minio-root-secret-stage'
require_relative '../../scripts/minio-amqp-secret-stage'
require_relative '../../scripts/keycloak-admin-secret-stage'
require_relative '../../scripts/keycloak-database-stage'
require_relative '../../scripts/job-api-database-secret-stage'
require_relative '../../scripts/job-api-minio-secret-stage'
require_relative '../../scripts/worker-database-stage'

class CredentialInitTest < Minitest::Test
  def setup
    @temporary = Dir.mktmpdir('clouddsp-secret-init-test-')
    @local = File.join(@temporary, 'ignored-local')
    @output = StringIO.new
    @error = StringIO.new
    @catalog = YAML.safe_load(CloudDSPCredentialCatalog::DEFAULT_PATH.read)
  end

  def teardown
    FileUtils.remove_entry(@temporary) if File.exist?(@temporary)
  end

  def run_init(input_path = nil)
    CloudDSPCredentialInit.new(local_directory: @local, input_path: input_path,
                               output: @output, error: @error).run
  end

  def source(name)
    YAML.safe_load(File.read(File.join(@local, name)))
  end

  def test_generates_every_source_with_shared_values_and_owner_only_permissions
    assert_equal 0, run_init
    assert_equal 0o700, File.stat(@local).mode & 0o777
    assert_equal 33, Dir.glob(File.join(@local, '*.secret.yaml')).length
    assert Dir.glob(File.join(@local, '*.secret.yaml')).all? { |path| (File.stat(path).mode & 0o777) == 0o600 }

    @catalog.fetch('credentials').each_value do |group|
      values = group.fetch('sources').map { |entry| source(entry.fetch('file')).fetch('stringData') }
      assert_equal 1, values.uniq.length
      refute values.first.values.any? { |value| value.include?('REPLACE_') }
    end
    db = source('job-api-database-credentials.secret.yaml').fetch('stringData')
    worker = source('demucs-database-credentials.secret.yaml').fetch('stringData')
    refute_equal db.fetch('JOB_API_DB_PASSWORD'), worker.fetch('DEMUCS_DB_PASSWORD')

    events = source('minio-source-intake-rabbitmq-credentials.secret.yaml').fetch('stringData')
    uri = URI.parse(events.fetch('MINIO_NOTIFY_AMQP_URL_INTAKE'))
    assert_equal events.fetch('RABBITMQ_MINIO_EVENTS_USERNAME'), URI::DEFAULT_PARSER.unescape(uri.user)
    assert_equal events.fetch('RABBITMQ_MINIO_EVENTS_PASSWORD'), URI::DEFAULT_PARSER.unescape(uri.password)
    assert_equal '/%2Fclouddsp', uri.path
    assert_includes @output.string, '33 owner-only files'
    assert_empty @error.string
  end

  def test_second_run_validates_without_replacing_credentials
    assert_equal 0, run_init
    original = File.read(File.join(@local, 'keycloak-bootstrap-admin.secret.yaml'))
    @output.truncate(0)
    @output.rewind

    assert_equal 0, run_init
    assert_equal original, File.read(File.join(@local, 'keycloak-bootstrap-admin.secret.yaml'))
    assert_includes @output.string, 'already initialized'
    assert_empty @error.string
  end

  def test_partial_set_is_rejected_without_writing_other_sources
    FileUtils.mkdir_p(@local, mode: 0o700)
    File.write(File.join(@local, 'postgresql-credentials.secret.yaml'), 'existing')

    assert_equal 1, run_init
    assert_equal 1, Dir.glob(File.join(@local, '*.secret.yaml')).length
    assert_includes @error.string, 'partial credential set'
  end

  def test_owner_only_input_overrides_selected_values
    input = File.join(@temporary, 'private-input.yaml')
    File.open(input, File::WRONLY | File::CREAT | File::EXCL, 0o600) do |file|
      file.write(YAML.dump('version' => 1, 'credentials' => {
        'keycloak-admin' => {
          'KC_BOOTSTRAP_ADMIN_USERNAME' => 'local-owner',
          'KC_BOOTSTRAP_ADMIN_PASSWORD' => 'test-password-with-more-than-16-characters'
        }
      }))
    end

    assert_equal 0, run_init(input)
    values = source('keycloak-bootstrap-admin.secret.yaml').fetch('stringData')
    assert_equal 'local-owner', values.fetch('KC_BOOTSTRAP_ADMIN_USERNAME')
    assert_equal 'test-password-with-more-than-16-characters', values.fetch('KC_BOOTSTRAP_ADMIN_PASSWORD')
    refute_includes @output.string, values.fetch('KC_BOOTSTRAP_ADMIN_PASSWORD')
  end

  def test_rejects_insecure_input_before_creating_sources
    input = File.join(@temporary, 'insecure-input.yaml')
    File.write(input, YAML.dump('version' => 1, 'credentials' => {}))
    File.chmod(0o644, input)

    assert_equal 1, run_init(input)
    refute Dir.exist?(@local)
    assert_includes @error.string, 'owner-only permissions'
  end

  def test_rejects_override_of_fixed_service_identity
    input = File.join(@temporary, 'private-input.yaml')
    File.open(input, File::WRONLY | File::CREAT | File::EXCL, 0o600) do |file|
      file.write(YAML.dump('version' => 1, 'credentials' => {
        'job-api-database' => { 'JOB_API_DB_USERNAME' => 'other-user' }
      }))
    end

    assert_equal 1, run_init(input)
    refute Dir.exist?(@local)
    assert_includes @error.string, 'cannot be overridden'
  end

  def test_custom_broker_password_is_encoded_for_the_existing_minio_stage
    input = File.join(@temporary, 'private-input.yaml')
    File.open(input, File::WRONLY | File::CREAT | File::EXCL, 0o600) do |file|
      file.write(YAML.dump('version' => 1, 'credentials' => {
        'minio-events-rabbitmq' => {
          'RABBITMQ_MINIO_EVENTS_PASSWORD' => 'test:amqp@credential/with?reserved#characters'
        }
      }))
    end

    assert_equal 0, run_init(input)
    path = File.join(@local, 'minio-source-intake-rabbitmq-credentials.secret.yaml')
    values = CloudDSPMinioAmqpSecretStage.new(local_path: path).send(:load_local_source)
    assert_equal 'test:amqp@credential/with?reserved#characters',
                 values.fetch('RABBITMQ_MINIO_EVENTS_PASSWORD')
    assert_includes values.fetch('MINIO_NOTIFY_AMQP_URL_INTAKE'), '%3A'
  end

  def test_generated_files_pass_existing_service_secret_parsers
    assert_equal 0, run_init
    local = ->(name) { File.join(@local, "#{name}.secret.yaml") }

    assert CloudDSPPostgresqlSecretStage.new(local_path: local.call('postgresql-credentials')).send(:load_local_source)
    assert CloudDSPRabbitmqSecretStage.new(local_path: local.call('rabbitmq-credentials')).send(:load_local_source)
    assert CloudDSPMinioRootSecretStage.new(local_path: local.call('minio-root-credentials')).send(:load_local_source)
    assert CloudDSPKeycloakAdminSecretStage.new(local_path: local.call('keycloak-bootstrap-admin')).send(:load_local_source)
    assert CloudDSPKeycloakDatabaseStage.new(local_path: local.call('keycloak-database-credentials')).send(:load_credentials)
    assert CloudDSPJobApiDatabaseSecretStage.new(
      local_path: local.call('job-api-database-credentials'),
      bootstrap_local_path: local.call('job-api-database-bootstrap-credentials')
    ).send(:load_local_sources)
    assert CloudDSPJobApiMinioSecretStage.new(
      local_path: local.call('job-api-minio-credentials'),
      bootstrap_local_path: local.call('job-api-minio-bootstrap-credentials')
    ).send(:load_local_sources)
    assert CloudDSPWorkerDatabaseStage.new('basic-pitch', local: @local).send(:local_credentials)
    assert CloudDSPWorkerDatabaseStage.new('adtof', local: @local).send(:local_credentials)
  end
end
