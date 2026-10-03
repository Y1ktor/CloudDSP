require 'base64'
require 'json'
require 'minitest/autorun'
require 'stringio'
require 'tmpdir'
require 'yaml'
require_relative '../../scripts/stages/keycloak/keycloak-database-stage'

class KeycloakDatabaseStageTest < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  # The fake returns only the read-only PostgreSQL catalog rows the stage
  # requests. Its create path models Kubernetes stringData conversion and a
  # completed one-shot Job; no real database or cluster is changed in tests.
  class FakeCluster
    attr_accessor :state, :secret, :job, :fail_dry_run, :fail_auth
    attr_reader :calls, :auth_stdin

    def initialize(source, state: :absent)
      @source = source
      @state = state
      @secret = nil
      @job = nil
      @fail_dry_run = false
      @calls = []
    end

    def call(*argv, **options)
      @calls << argv
      action = argv[5]
      case action
      when 'exec'
        query = argv.last
        if query == CloudDSPKeycloakDatabaseStage::AUTH_IN_POD
          @auth_stdin = options.fetch(:stdin_data)
          return result(@fail_auth ? '' : '1', @fail_auth ? 1 : 0)
        end
        return result(row(database)) if query == CloudDSPKeycloakDatabaseStage::DATABASE_QUERY
        return result(row(role)) if query == CloudDSPKeycloakDatabaseStage::ROLE_QUERY
        return result(row(schema)) if query == CloudDSPKeycloakDatabaseStage::SCHEMA_QUERY
      when 'get'
        name = argv[6]
        resource = case name
                   when "secret/#{CloudDSPKeycloakDatabaseStage::SECRET}" then @secret
                   when "job/#{CloudDSPKeycloakDatabaseStage::JOB}" then @job
                   when 'secret/clouddsp-postgresql-credentials' then { 'kind' => 'Secret' }
                   when 'statefulset/clouddsp-postgresql' then { 'status' => { 'readyReplicas' => 1 } }
                   end
        return result(resource ? JSON.generate(resource) : '')
      when 'create'
        return result('', 1) if @fail_dry_run && argv.include?('--dry-run=server')
        unless argv.include?('--dry-run=server')
          if argv.last.end_with?('keycloak-database-credentials.secret.yaml')
            @secret = live_secret
          elsif argv.last.end_with?('keycloak-database-bootstrap-job.yaml')
            @job = completed_job
            @state = :ready
          end
        end
        return result('')
      when 'wait'
        return result('')
      end
      raise "unexpected fake command #{action}"
    end

    def live_secret
      { 'kind' => 'Secret', 'metadata' => @source.fetch('metadata'), 'type' => 'Opaque',
        'data' => @source.fetch('stringData').transform_values { |value| Base64.strict_encode64(value) } }
    end

    def completed_job
      { 'status' => { 'conditions' => [{ 'type' => 'Complete', 'status' => 'True' }] } }
    end

    private

    def result(output, code = 0)
      [output, '', Status.new(code)]
    end

    def row(value)
      value ? JSON.generate(value) : ''
    end

    def database
      return nil if @state == :absent
      { 'datname' => 'clouddsp_keycloak', 'owner' => 'clouddsp-keycloak',
        'datallowconn' => true, 'encoding' => 'UTF8', 'no_public_privileges' => true }
    end

    def role
      return nil if %i[absent partial].include?(@state)
      { 'rolname' => 'clouddsp-keycloak', 'rolcanlogin' => true,
        'rolsuper' => @state == :drift, 'rolcreatedb' => false,
        'rolcreaterole' => false, 'rolreplication' => false,
        'rolinherit' => false, 'rolbypassrls' => false, 'no_memberships' => true }
    end

    def schema
      { 'nspname' => 'public', 'owner' => 'clouddsp-keycloak',
        'no_public_privileges' => true, 'role_usage' => true,
        'role_create' => true, 'role_connect' => true, 'role_temporary' => true }
    end
  end

  def with_stage(state: :absent)
    Dir.mktmpdir('clouddsp-keycloak-database-test-') do |directory|
      source = YAML.safe_load(CloudDSPKeycloakDatabaseStage::EXAMPLE_PATH.read)
      source.fetch('stringData')['KEYCLOAK_DB_PASSWORD'] = 'test-only-local-key'
      path = File.join(directory, 'keycloak-database-credentials.secret.yaml')
      File.write(path, YAML.dump(source))
      fake = FakeCluster.new(source, state: state)
      output = StringIO.new
      error = StringIO.new
      stage = CloudDSPKeycloakDatabaseStage.new(command: fake.method(:call), local_path: path,
                                                 output: output, error: error)
      yield stage, fake, source, path, output, error
    end
  end

  def test_plan_on_absent_cluster_reads_state_without_writes
    with_stage do |stage, fake, _source, _path, output, _error|
      assert_equal 0, stage.run('plan')
      assert_includes output.string, 'versioned Job pending'
      refute fake.calls.any? { |call| call[5] == 'create' }
    end
  end

  def test_fresh_bootstrap_creates_secret_then_versioned_job_after_server_dry_runs
    with_stage do |stage, fake, _source, path, output, _error|
      assert_equal 0, stage.run('bootstrap')
      creates = fake.calls.select { |call| call[5] == 'create' }
      assert_equal 4, creates.length
      assert creates.take(2).all? { |call| call.include?('--dry-run=server') }
      assert_equal path, creates[2].last
      assert creates[3].last.end_with?('keycloak-database-bootstrap-job.yaml')
      assert_equal 0, stage.run('verify')
      assert_includes output.string, 'created and verified'
      assert_equal "test-only-local-key\n", fake.auth_stdin
      refute fake.calls.any? { |call| call.any? { |part| part.include?('test-only-local-key') } }
    end
  end

  def test_ready_state_verifies_without_writes
    with_stage(state: :ready) do |stage, fake, _source, _path, _output, _error|
      fake.secret = fake.live_secret
      assert_equal 0, stage.run('verify')
      assert_equal 0, stage.run('bootstrap')
      refute fake.calls.any? { |call| call[5] == 'create' }
    end
  end

  def test_partial_database_or_privileged_role_stops_before_writes
    %i[partial drift].each do |state|
      with_stage(state: state) do |stage, fake, _source, _path, _output, error|
        assert_equal 1, stage.run('bootstrap')
        assert_includes error.string, state == :partial ? 'partial Keycloak' : 'role privileges drifted'
        refute fake.calls.any? { |call| call[5] == 'create' }
      end
    end
  end

  def test_orphan_secret_or_active_job_blocks_fresh_bootstrap
    with_stage do |stage, fake, _source, _path, _output, error|
      fake.secret = fake.live_secret
      assert_equal 1, stage.run('bootstrap')
      assert_includes error.string, 'partial Keycloak bootstrap resources'
      refute fake.calls.any? { |call| call[5] == 'create' }
    end
    with_stage(state: :ready) do |stage, fake, _source, _path, _output, error|
      fake.secret = fake.live_secret
      fake.job = { 'status' => { 'conditions' => [{ 'type' => 'Failed', 'status' => 'True' }] } }
      assert_equal 1, stage.run('verify')
      assert_includes error.string, 'remains active or failed'
    end
  end

  def test_placeholder_or_changed_identity_blocks_before_cluster_contact
    with_stage do |stage, fake, source, path, _output, error|
      source.fetch('stringData')['KEYCLOAK_DB_PASSWORD'] =
        'REPLACE_WITH_A_UNIQUE_LOCAL_KEYCLOAK_DATABASE_PASSWORD'
      File.write(path, YAML.dump(source))
      assert_equal 1, stage.run('bootstrap')
      assert_empty fake.calls
      assert_includes error.string, 'placeholder password'

      source.fetch('stringData')['KEYCLOAK_DB_PASSWORD'] = 'test-only-local-key'
      source.fetch('metadata')['namespace'] = 'clouddsp-app'
      File.write(path, YAML.dump(source))
      assert_equal 1, stage.run('bootstrap')
      assert_empty fake.calls
      assert_includes error.string, 'differs from committed example'
    end
  end

  def test_server_dry_run_failure_leaves_resources_absent
    with_stage do |stage, fake, _source, _path, _output, error|
      fake.fail_dry_run = true
      assert_equal 1, stage.run('bootstrap')
      assert_nil fake.secret
      assert_nil fake.job
      assert_includes error.string, 'Kubernetes operation failed'
    end
  end

  def test_live_secret_drift_fails_without_printing_password
    with_stage(state: :ready) do |stage, fake, _source, _path, output, error|
      fake.secret = fake.live_secret
      fake.secret.fetch('data')['KEYCLOAK_DB_PASSWORD'] = Base64.strict_encode64('different-test-password')
      assert_equal 1, stage.run('verify')
      assert_includes error.string, 'differs from ignored local source'
      refute_includes output.string + error.string, 'different-test-password'
    end
  end

  def test_rejected_database_login_fails_verify_without_leaking_password
    with_stage(state: :ready) do |stage, fake, _source, _path, output, error|
      fake.secret = fake.live_secret
      fake.fail_auth = true
      assert_equal 1, stage.run('verify')
      assert_includes error.string, 'credential authentication failed'
      refute_includes output.string + error.string, 'test-only-local-key'
      refute fake.calls.any? { |call| call[5] == 'create' }
    end
  end
end
