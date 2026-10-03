require 'base64'
require 'json'
require 'minitest/autorun'
require 'tmpdir'
require 'yaml'
require_relative '../../scripts/stages/database/job-api-database-bootstrap'

class JobApiDatabaseBootstrapTest < Minitest::Test
  VALUES = {
    'JOB_API_DB_NAME' => 'clouddsp_job_api',
    'JOB_API_DB_USERNAME' => 'clouddsp-job-api',
    'JOB_API_DB_PASSWORD' => 'test-only-password'
  }.freeze

  class FakeCluster
    attr_reader :calls, :temporary_secret

    def initialize(state: :ready, pending_job: false, runtime_password: VALUES.fetch('JOB_API_DB_PASSWORD'))
      @state = state
      @pending_job = pending_job
      @temporary_secret = false
      @runtime_password = runtime_password
      @calls = []
    end

    def call(*argv)
      @calls << argv
      if argv.include?('exec')
        sql = argv.last
        return row(database) if sql == JobApiDatabaseBootstrap::DATABASE_QUERY
        return row(role) if sql == JobApiDatabaseBootstrap::ROLE_QUERY
        return row(schema) if sql == JobApiDatabaseBootstrap::SCHEMA_QUERY
      end
      verb, name = argv[5], argv[6]
      if verb == 'get'
        return (@temporary_secret ? "secret/#{JobApiDatabaseBootstrap::BOOTSTRAP_SECRET}\n" : '') if name == "secret/#{JobApiDatabaseBootstrap::BOOTSTRAP_SECRET}"
        if name == "secret/#{JobApiDatabaseBootstrap::RUNTIME_SECRET}"
          return JSON.generate({ 'data' => VALUES.merge('JOB_API_DB_PASSWORD' => @runtime_password).transform_values { |value| Base64.strict_encode64(value) } }) if argv.include?('--output=json')
          return "secret/#{JobApiDatabaseBootstrap::RUNTIME_SECRET}\n"
        end
        if name == "job/#{JobApiDatabaseBootstrap::JOB}"
          return '' unless @pending_job
          return JSON.generate({ 'status' => { 'conditions' => [{ 'type' => 'Failed', 'status' => 'True' }] } })
        end
        return 'secret/clouddsp-postgresql-credentials' if name == 'secret/clouddsp-postgresql-credentials'
        return JSON.generate({ 'status' => { 'readyReplicas' => 1 } }) if name == 'statefulset/clouddsp-postgresql'
      elsif verb == 'create'
        return 'validated' if argv.include?('--dry-run=server')
        if argv.last.end_with?('job-api-database-bootstrap-credentials.secret.yaml')
          @temporary_secret = true
        elsif argv.last.end_with?('job-api-database-bootstrap-job.yaml')
          @state = :ready
        end
        return 'created'
      elsif verb == 'wait'
        return 'complete'
      elsif verb == 'delete'
        @temporary_secret = false
        return 'deleted'
      end
      raise "unexpected fake command: #{argv.inspect}"
    end

    private

    def row(object)
      object ? JSON.generate(object) : ''
    end

    def database
      return nil if @state == :absent
      {
        'datname' => 'clouddsp_job_api', 'owner' => 'clouddsp-job-api',
        'datallowconn' => true, 'encoding' => 'UTF8', 'no_public_privileges' => true
      }
    end

    def role
      return nil if %i[absent partial].include?(@state)
      {
        'rolname' => 'clouddsp-job-api', 'rolcanlogin' => true,
        'rolsuper' => @state == :drift, 'rolcreatedb' => false,
        'rolcreaterole' => false, 'rolreplication' => false,
        'rolinherit' => false, 'rolbypassrls' => false, 'no_memberships' => true
      }
    end

    def schema
      {
        'nspname' => 'public', 'owner' => 'clouddsp-job-api',
        'no_public_privileges' => true, 'role_usage' => true,
        'role_create' => true, 'role_connect' => true, 'role_temporary' => true
      }
    end
  end

  def test_complete_state_reconciles_without_creating_a_job_or_secret
    fake = FakeCluster.new
    result = nil
    capture_io { result = JobApiDatabaseBootstrap.new(runner: fake.method(:call)).run('reconcile') }
    assert_equal(:ready, result)
    refute(fake.calls.any? { |argv| argv.include?('create') || argv.include?('delete') })
  end

  def test_absent_database_plan_reports_dependency_without_writing
    Dir.mktmpdir do |directory|
      write_local_secrets(directory)
      fake = FakeCluster.new(state: :absent)
      result = nil

      capture_io do
        result = JobApiDatabaseBootstrap.new(runner: fake.method(:call), local_directory: directory).run('plan')
      end

      assert_equal(:absent, result)
      refute(fake.calls.any? { |argv| argv.include?('create') || argv.include?('delete') })
    end
  end

  def test_partial_database_and_role_state_blocks_mutation
    fake = FakeCluster.new(state: :partial)
    _out, err = capture_io do
      assert_raises(SystemExit) { JobApiDatabaseBootstrap.new(runner: fake.method(:call)).run('reconcile') }
    end
    assert_includes(err, 'partial Job API database/role state')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  def test_privileged_role_drift_blocks_mutation
    fake = FakeCluster.new(state: :drift)
    _out, err = capture_io do
      assert_raises(SystemExit) { JobApiDatabaseBootstrap.new(runner: fake.method(:call)).run('reconcile') }
    end
    assert_includes(err, 'role privileges drifted')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  def test_existing_job_with_absent_database_blocks_mutation
    Dir.mktmpdir do |directory|
      write_local_secrets(directory)
      fake = FakeCluster.new(state: :absent, pending_job: true)
      _out, err = capture_io do
        assert_raises(SystemExit) do
          JobApiDatabaseBootstrap.new(runner: fake.method(:call), local_directory: directory).run('reconcile')
        end
      end
      assert_includes(err, 'bootstrap Job exists without complete database state')
      refute(fake.calls.any? { |argv| argv.include?('create') })
    end
  end

  def test_failed_job_blocks_even_when_database_metadata_is_complete
    fake = FakeCluster.new(state: :ready, pending_job: true)
    _out, err = capture_io do
      assert_raises(SystemExit) { JobApiDatabaseBootstrap.new(runner: fake.method(:call)).run('verify') }
    end
    assert_includes(err, 'bootstrap Job remains active or failed')
  end

  def test_absent_database_creates_versioned_job_and_removes_temporary_secret
    Dir.mktmpdir do |directory|
      write_local_secrets(directory)
      fake = FakeCluster.new(state: :absent)
      result = nil
      out, err = capture_io do
        result = JobApiDatabaseBootstrap.new(runner: fake.method(:call), local_directory: directory).run('reconcile')
      end
      assert_equal(:ready, result)
      assert_empty(err)
      assert_includes(out, 'temporary Secret removed')
      refute(fake.temporary_secret)
      created = fake.calls.select { |argv| argv.include?('create') && !argv.include?('--dry-run=server') }
      assert_equal(2, created.length)
      assert(created.first.last.end_with?('job-api-database-bootstrap-credentials.secret.yaml'))
      assert(created.last.last.end_with?('job-api-database-bootstrap-job.yaml'))
    end
  end

  def test_runtime_secret_mismatch_blocks_before_creating_anything
    Dir.mktmpdir do |directory|
      write_local_secrets(directory)
      fake = FakeCluster.new(state: :absent, runtime_password: 'different-test-only-password')
      _out, err = capture_io do
        assert_raises(SystemExit) do
          JobApiDatabaseBootstrap.new(runner: fake.method(:call), local_directory: directory).run('reconcile')
        end
      end
      assert_includes(err, 'live Job API runtime Secret differs')
      refute(fake.calls.any? { |argv| argv.include?('create') })
    end
  end

  private

  def write_local_secrets(directory)
    [
      ['job-api-database-bootstrap-credentials.secret.yaml', JobApiDatabaseBootstrap::BOOTSTRAP_SECRET, 'clouddsp-data'],
      ['job-api-database-credentials.secret.yaml', JobApiDatabaseBootstrap::RUNTIME_SECRET, 'clouddsp-app']
    ].each do |filename, name, namespace|
      document = { 'kind' => 'Secret', 'metadata' => { 'name' => name, 'namespace' => namespace },
                   'type' => 'Opaque', 'stringData' => VALUES }
      File.write(File.join(directory, filename), YAML.dump(document))
    end
  end
end
