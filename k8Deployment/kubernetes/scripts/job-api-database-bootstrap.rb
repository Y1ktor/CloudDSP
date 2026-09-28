#!/usr/bin/env ruby
# Bootstrap the Job API's dedicated database and schema-owner role exactly
# once. Kubernetes Job status is transient; PostgreSQL ownership and grants
# are the durable completion evidence. This stage does not rotate credentials.
require 'base64'
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class JobApiDatabaseBootstrap
  CONTEXT = 'k3d-clouddsp-local'.freeze
  DATABASE = 'clouddsp_job_api'.freeze
  ROLE = 'clouddsp-job-api'.freeze
  JOB = 'job-api-database-bootstrap'.freeze
  BOOTSTRAP_SECRET = 'clouddsp-job-api-database-bootstrap-credentials'.freeze
  RUNTIME_SECRET = 'clouddsp-job-api-database-credentials'.freeze
  ROOT = Pathname.new(File.expand_path('..', __dir__)).freeze
  JOB_PATH = ROOT.join('services', 'api', 'job-api-database-bootstrap-job.yaml').freeze
  LOCAL_DIRECTORY = ROOT.parent.join('.local').freeze
  CREDENTIAL_KEYS = %w[JOB_API_DB_NAME JOB_API_DB_USERNAME JOB_API_DB_PASSWORD].freeze
  # Both SQL queries are fixed, read-only projections of cluster metadata.
  # No application table, password verifier, or Secret value is selected.
  DATABASE_QUERY = <<~SQL.freeze
    SELECT row_to_json(record) FROM (
      SELECT datname, pg_get_userbyid(datdba) AS owner, datallowconn,
             pg_encoding_to_char(encoding) AS encoding,
             NOT EXISTS (
               SELECT 1 FROM aclexplode(coalesce(datacl, acldefault('d', datdba)))
               WHERE grantee = 0
             ) AS no_public_privileges
      FROM pg_database WHERE datname = 'clouddsp_job_api'
    ) AS record;
  SQL
  ROLE_QUERY = <<~SQL.freeze
    SELECT row_to_json(record) FROM (
      SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
             rolreplication, rolinherit, rolbypassrls,
             NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = pg_roles.oid) AS no_memberships
      FROM pg_roles WHERE rolname = 'clouddsp-job-api'
    ) AS record;
  SQL
  SCHEMA_QUERY = <<~SQL.freeze
    SELECT row_to_json(record) FROM (
      SELECT nspname, pg_get_userbyid(nspowner) AS owner,
             NOT EXISTS (
               SELECT 1 FROM aclexplode(coalesce(nspacl, acldefault('n', nspowner)))
               WHERE grantee = 0
             ) AS no_public_privileges,
             has_schema_privilege('clouddsp-job-api', 'public', 'USAGE') AS role_usage,
             has_schema_privilege('clouddsp-job-api', 'public', 'CREATE') AS role_create,
             has_database_privilege('clouddsp-job-api', 'clouddsp_job_api', 'CONNECT') AS role_connect,
             has_database_privilege('clouddsp-job-api', 'clouddsp_job_api', 'TEMPORARY') AS role_temporary
      FROM pg_namespace WHERE nspname = 'public'
    ) AS record;
  SQL
  # The password is expanded only inside the existing PostgreSQL Pod from its
  # Secret-backed environment. SQL and the database name arrive as separate
  # argv values, so neither is parsed by the host shell.
  PSQL_IN_POD = 'export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=10; database="$1"; if [ -z "$database" ]; then database="$POSTGRES_DB"; fi; exec psql --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --host=127.0.0.1 --username="$POSTGRES_USER" --dbname="$database" --tuples-only --no-align --command "$2"'.freeze

  def initialize(runner: nil, local_directory: LOCAL_DIRECTORY)
    @runner = runner || method(:capture)
    @local_directory = Pathname.new(local_directory)
  end

  def run(mode)
    ensure_true(%w[plan verify reconcile].include?(mode), 'use plan, verify, or reconcile')
    job = load_job
    state = read_state
    existing_job = live_job
    ensure_true(!resource_exists?('clouddsp-data', 'secret', BOOTSTRAP_SECRET),
                'temporary Job API database bootstrap Secret remains; inspect and remove it')
    ensure_true(resource_exists?('clouddsp-app', 'secret', RUNTIME_SECRET),
                'Job API runtime database Secret is absent')
    if state == :ready
      ensure_true(existing_job.nil? || existing_job.fetch('status', {}).fetch('conditions', []).any? { |condition|
                    condition['type'] == 'Complete' && condition['status'] == 'True'
                  }, 'bootstrap Job remains active or failed despite complete database metadata')
      puts 'Job API database bootstrap: database, role, schema, and grants verified; no work pending'
    else
      ensure_true(existing_job.nil?,
                  'bootstrap Job exists without complete database state; inspect it before retrying')
      ensure_true(local_bootstrap_path.file? && local_runtime_path.file?,
                  'ignored local Job API bootstrap and runtime Secret files are required')
      puts 'Job API database bootstrap: database and role absent; one versioned Job pending'
      if mode == 'verify'
        raise 'Job API database bootstrap is incomplete'
      elsif mode == 'reconcile'
        ensure_prerequisites
        credentials = load_local_credentials
        ensure_live_runtime_secret_matches(credentials)
        ensure_true(read_state == :absent, 'database state changed during preflight')
        create_bootstrap(job)
        ensure_true(read_state == :ready, 'bootstrap Job completed without the expected database state')
        kubectl('clouddsp-data', 'delete', "secret/#{BOOTSTRAP_SECRET}")
        state = :ready
        puts 'Job API database bootstrap applied; temporary Secret removed'
      end
    end
    puts "Job API database bootstrap #{mode} passed"
    state
  rescue StandardError => error
    warn "Job API database bootstrap #{mode} stopped: #{error.message}"
    exit 1
  end

  # Complete means the exact database and role exist with the reviewed
  # privileges. A one-sided or drifted state is never repaired implicitly;
  # that could rename ownership or rotate a live application's password.
  def assess(database, role, schema)
    return :absent if database.nil? && role.nil?
    ensure_true(database && role, 'partial Job API database/role state requires inspection')
    ensure_true(database == {
                  'datname' => DATABASE, 'owner' => ROLE, 'datallowconn' => true,
                  'encoding' => 'UTF8', 'no_public_privileges' => true
                }, 'Job API database owner, encoding, connection, or public grants drifted')
    ensure_true(role == {
                  'rolname' => ROLE, 'rolcanlogin' => true, 'rolsuper' => false,
                  'rolcreatedb' => false, 'rolcreaterole' => false,
                  'rolreplication' => false, 'rolinherit' => false, 'rolbypassrls' => false,
                  'no_memberships' => true
                }, 'Job API role privileges drifted')
    ensure_true(schema == {
                  'nspname' => 'public', 'owner' => ROLE, 'no_public_privileges' => true,
                  'role_usage' => true, 'role_create' => true,
                  'role_connect' => true, 'role_temporary' => true
                }, 'Job API public schema owner or grants drifted')
    :ready
  end

  private

  def ensure_true(condition, message)
    raise message unless condition
  end

  def capture(*argv)
    stdout, _stderr, status = Open3.capture3(*argv)
    unless status.success?
      action = argv.include?('exec') ? 'PostgreSQL metadata query' : "kubectl #{argv[5]} #{argv[6]}"
      raise "#{action} failed (exit #{status.exitstatus}); inspect the named resource and Pod logs"
    end
    stdout
  end

  def command(*argv)
    @runner.call(*argv)
  end

  def kubectl(namespace, *args)
    command('kubectl', '--context', CONTEXT, '--namespace', namespace, *args)
  end

  def psql(sql, database = '')
    kubectl('clouddsp-data', 'exec', 'pod/clouddsp-postgresql-0', '--', 'sh', '-ec',
            PSQL_IN_POD, 'bootstrap-audit', database, sql).strip
  end

  def row(sql, database = '')
    output = psql(sql, database)
    output.empty? ? nil : JSON.parse(output)
  end

  def read_state
    database = row(DATABASE_QUERY)
    role = row(ROLE_QUERY)
    schema = database && role ? row(SCHEMA_QUERY, DATABASE) : nil
    assess(database, role, schema)
  end

  def resource_exists?(namespace, kind, name)
    !kubectl(namespace, 'get', "#{kind}/#{name}", '--ignore-not-found', '--output=name').strip.empty?
  end

  def live_job
    output = kubectl('clouddsp-data', 'get', "job/#{JOB}", '--ignore-not-found', '--output=json').strip
    output.empty? ? nil : JSON.parse(output)
  end

  def load_job
    job = YAML.load_file(JOB_PATH)
    containers = job.dig('spec', 'template', 'spec', 'containers')
    secret_names = containers&.first&.fetch('env')&.map do |entry|
      entry.dig('valueFrom', 'secretKeyRef', 'name')
    end&.compact&.uniq&.sort
    ensure_true(job['kind'] == 'Job' && job.dig('metadata', 'namespace') == 'clouddsp-data' &&
                job.dig('metadata', 'name') == JOB && job.dig('spec', 'backoffLimit') == 0 &&
                job.dig('spec', 'activeDeadlineSeconds').to_i.positive? &&
                job.dig('spec', 'template', 'spec', 'automountServiceAccountToken') == false &&
                containers.is_a?(Array) && containers.length == 1 &&
                secret_names == [BOOTSTRAP_SECRET, 'clouddsp-postgresql-credentials'].sort,
                'versioned Job API database bootstrap Job changed its safety contract')
    job
  end

  def local_bootstrap_path
    @local_directory.join('job-api-database-bootstrap-credentials.secret.yaml')
  end

  def local_runtime_path
    @local_directory.join('job-api-database-credentials.secret.yaml')
  end

  def load_local_credentials
    bootstrap = YAML.load_file(local_bootstrap_path)
    runtime = YAML.load_file(local_runtime_path)
    [[bootstrap, BOOTSTRAP_SECRET, 'clouddsp-data'], [runtime, RUNTIME_SECRET, 'clouddsp-app']].each do |secret, name, namespace|
      ensure_true(secret['kind'] == 'Secret' && secret.dig('metadata', 'name') == name &&
                  secret.dig('metadata', 'namespace') == namespace && secret['type'] == 'Opaque' &&
                  secret.fetch('stringData').keys.sort == CREDENTIAL_KEYS.sort,
                  "ignored local #{name} does not match the reviewed Secret contract")
    end
    values = bootstrap.fetch('stringData')
    ensure_true(values == runtime.fetch('stringData'),
                'ignored local bootstrap and runtime credentials differ; resolve before creating a role')
    ensure_true(values['JOB_API_DB_NAME'] == DATABASE && values['JOB_API_DB_USERNAME'] == ROLE &&
                !values['JOB_API_DB_PASSWORD'].to_s.empty? &&
                !values['JOB_API_DB_PASSWORD'].start_with?('REPLACE_'),
                'ignored local Job API database identity or password is invalid')
    values
  end

  def ensure_live_runtime_secret_matches(values)
    # The host already holds the ignored runtime Secret. Compare its three
    # values in memory with the namespaced copy so bootstrap cannot install
    # a role password different from the credential the API will receive.
    # Neither copy is printed or retained after this process exits.
    secret = JSON.parse(kubectl('clouddsp-app', 'get', "secret/#{RUNTIME_SECRET}", '--output=json'))
    data = secret.fetch('data')
    ensure_true(data.keys.sort == CREDENTIAL_KEYS.sort &&
                data.all? { |key, encoded| Base64.strict_decode64(encoded) == values.fetch(key) },
                'live Job API runtime Secret differs from ignored local credentials')
  end

  def ensure_prerequisites
    kubectl('clouddsp-data', 'get', 'secret/clouddsp-postgresql-credentials', '--output=name')
    database = JSON.parse(kubectl('clouddsp-data', 'get', 'statefulset/clouddsp-postgresql', '--output=json'))
    ensure_true(database.dig('status', 'readyReplicas') == 1,
                'PostgreSQL StatefulSet must be Ready before the bootstrap Job')
  end

  def create_bootstrap(job)
    kubectl('clouddsp-data', 'create', '--dry-run=server', '--filename', local_bootstrap_path.to_s)
    kubectl('clouddsp-data', 'create', '--dry-run=server', '--filename', JOB_PATH.to_s)
    kubectl('clouddsp-data', 'create', '--filename', local_bootstrap_path.to_s)
    kubectl('clouddsp-data', 'create', '--filename', JOB_PATH.to_s)
    timeout = job.dig('spec', 'activeDeadlineSeconds').to_i + 60
    kubectl('clouddsp-data', 'wait', '--for=condition=complete', "job/#{JOB}", "--timeout=#{timeout}s")
  rescue StandardError => error
    raise "#{JOB}: #{error.message}; inspect the Job and temporary bootstrap Secret"
  end
end

JobApiDatabaseBootstrap.new.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
