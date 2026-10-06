#!/usr/bin/env ruby
# Create Keycloak's isolated PostgreSQL login and database on a fresh cluster.
# The durable PostgreSQL role, database, and grants are completion evidence;
# the one-shot Kubernetes Job can disappear after its five-minute TTL. The
# ignored credential Secret remains in clouddsp-data for the Keycloak Pod.
# Existing or partial state is inspected, never repaired or rotated here.
require_relative '../../lib/paths'
require 'base64'
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class CloudDSPKeycloakDatabaseStage
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-data'.freeze
  DATABASE = 'clouddsp_keycloak'.freeze
  ROLE = 'clouddsp-keycloak'.freeze
  SECRET = 'clouddsp-keycloak-database-credentials'.freeze
  JOB = 'keycloak-database-bootstrap'.freeze
  KEYS = %w[KEYCLOAK_DB_NAME KEYCLOAK_DB_USERNAME KEYCLOAK_DB_PASSWORD].freeze
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  LOCAL_PATH = ROOT.parent.join('.local', 'keycloak-database-credentials.secret.yaml').freeze
  EXAMPLE_PATH = ROOT.join('services', 'keycloak', 'keycloak-database-credentials.secret.example.yaml').freeze
  JOB_PATH = ROOT.join('services', 'keycloak', 'keycloak-database-bootstrap-job.yaml').freeze

  # These fixed projections contain names and privileges only. Querying the
  # system catalogs, rather than Job status, also works after Job TTL cleanup.
  DATABASE_QUERY = <<~SQL.freeze
    SELECT row_to_json(record) FROM (
      SELECT datname, pg_get_userbyid(datdba) AS owner, datallowconn,
             pg_encoding_to_char(encoding) AS encoding,
             NOT EXISTS (
               SELECT 1 FROM aclexplode(coalesce(datacl, acldefault('d', datdba)))
               WHERE grantee = 0
             ) AS no_public_privileges
      FROM pg_database WHERE datname = 'clouddsp_keycloak'
    ) AS record;
  SQL
  ROLE_QUERY = <<~SQL.freeze
    SELECT row_to_json(record) FROM (
      SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
             rolreplication, rolinherit, rolbypassrls,
             NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = pg_roles.oid) AS no_memberships
      FROM pg_roles WHERE rolname = 'clouddsp-keycloak'
    ) AS record;
  SQL
  SCHEMA_QUERY = <<~SQL.freeze
    SELECT row_to_json(record) FROM (
      SELECT nspname, pg_get_userbyid(nspowner) AS owner,
             NOT EXISTS (
               SELECT 1 FROM aclexplode(coalesce(nspacl, acldefault('n', nspowner)))
               WHERE grantee = 0
             ) AS no_public_privileges,
             has_schema_privilege('clouddsp-keycloak', 'public', 'USAGE') AS role_usage,
             has_schema_privilege('clouddsp-keycloak', 'public', 'CREATE') AS role_create,
             has_database_privilege('clouddsp-keycloak', 'clouddsp_keycloak', 'CONNECT') AS role_connect,
             has_database_privilege('clouddsp-keycloak', 'clouddsp_keycloak', 'TEMPORARY') AS role_temporary
      FROM pg_namespace WHERE nspname = 'public'
    ) AS record;
  SQL
  # The shell runs only inside the PostgreSQL Pod. Its password is obtained
  # from that Pod's existing environment; no credential enters a host argv.
  PSQL_IN_POD = 'export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=10; database="$1"; if [ -z "$database" ]; then database="$POSTGRES_DB"; fi; exec psql --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --host=127.0.0.1 --username="$POSTGRES_USER" --dbname="$database" --tuples-only --no-align --command "$2"'.freeze
  # stdin carries the ignored password to the existing PostgreSQL Pod. This
  # authentication probe creates no extra resource and puts no password in a
  # host process argument, log line, or SQL text.
  AUTH_IN_POD = 'IFS= read -r PGPASSWORD; export PGPASSWORD PGCONNECT_TIMEOUT=10; exec psql --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --host=127.0.0.1 --username=clouddsp-keycloak --dbname=clouddsp_keycloak --tuples-only --no-align --command="SELECT 1"'.freeze

  def initialize(command: Open3.method(:capture3), local_path: LOCAL_PATH,
                 output: $stdout, error: $stderr)
    @command = command
    @local_path = Pathname.new(local_path)
    @output = output
    @error = error
  end

  def run(mode)
    ensure_true(%w[plan bootstrap verify].include?(mode), 'use plan, bootstrap, or verify')
    credentials = load_credentials
    job_source = load_job
    state = read_state
    secret = live_resource('secret', SECRET)
    job = live_resource('job', JOB)

    if state == :ready
      ensure_true(!secret.nil?, 'Keycloak database exists but runtime Secret is absent')
      verify_secret(secret, credentials)
      ensure_true(job.nil? || completed?(job), 'Keycloak database Job remains active or failed')
      authenticate(credentials)
      @output.puts 'Keycloak database, role, grants, and runtime Secret verified; no work pending'
      return 0
    end

    ensure_true(job.nil? && secret.nil?, 'partial Keycloak bootstrap resources require inspection')
    ensure_true(mode != 'verify', 'Keycloak database bootstrap is incomplete')
    if mode == 'plan'
      @output.puts 'Keycloak database plan: role and database absent; credential Secret and versioned Job pending'
      return 0
    end

    ensure_postgresql_ready
    # Validate both resources with the API server before the first write. A
    # second metadata lookup narrows the race with another bootstrap process.
    kubectl('create', '--dry-run=server', '--filename', @local_path.to_s)
    kubectl('create', '--dry-run=server', '--filename', JOB_PATH.to_s)
    ensure_true(read_state == :absent && live_resource('secret', SECRET).nil? &&
                live_resource('job', JOB).nil?, 'Keycloak bootstrap state changed during preflight')
    kubectl('create', '--filename', @local_path.to_s)
    verify_secret(live_resource('secret', SECRET), credentials)
    kubectl('create', '--filename', JOB_PATH.to_s)
    kubectl('wait', '--for=condition=complete', "job/#{JOB}",
            "--timeout=#{job_source.dig('spec', 'activeDeadlineSeconds') + 60}s")
    ensure_true(read_state == :ready, 'Keycloak Job completed without the expected database state')
    authenticate(credentials)
    @output.puts 'Keycloak credential Secret, database, role, and grants created and verified'
    0
  rescue StandardError => exception
    # kubectl output and parser details can contain credential-bearing YAML.
    # Only our fixed RuntimeError messages are safe to show to the operator.
    detail = exception.instance_of?(RuntimeError) ? exception.message : exception.class.to_s
    @error.puts "Keycloak database #{mode} stopped: #{detail}"
    1
  end

  # A one-sided or drifted database is evidence requiring inspection. This
  # fresh-cluster command never changes ownership or a live password in place.
  def assess(database, role, schema)
    return :absent if database.nil? && role.nil?
    ensure_true(database && role, 'partial Keycloak database/role state requires inspection')
    ensure_true(database == {
      'datname' => DATABASE, 'owner' => ROLE, 'datallowconn' => true,
      'encoding' => 'UTF8', 'no_public_privileges' => true
    }, 'Keycloak database owner, encoding, connection, or public grants drifted')
    ensure_true(role == {
      'rolname' => ROLE, 'rolcanlogin' => true, 'rolsuper' => false,
      'rolcreatedb' => false, 'rolcreaterole' => false, 'rolreplication' => false,
      'rolinherit' => false, 'rolbypassrls' => false, 'no_memberships' => true
    }, 'Keycloak role privileges drifted')
    ensure_true(schema == {
      'nspname' => 'public', 'owner' => ROLE, 'no_public_privileges' => true,
      'role_usage' => true, 'role_create' => true,
      'role_connect' => true, 'role_temporary' => true
    }, 'Keycloak public schema owner or grants drifted')
    :ready
  end

  private

  def ensure_true(condition, message)
    raise message unless condition
  end

  def load_credentials
    ensure_true(@local_path.file?, 'ignored local Keycloak database Secret is absent')
    source = YAML.safe_load(@local_path.read)
    example = YAML.safe_load(EXAMPLE_PATH.read)
    ensure_true(source.is_a?(Hash) && example.is_a?(Hash), 'Keycloak Secret source is invalid')
    %w[apiVersion kind metadata type].each do |field|
      ensure_true(source[field] == example[field], 'Keycloak Secret source differs from committed example')
    end
    values = source['stringData']
    ensure_true(source['data'].nil? && values.is_a?(Hash) && values.keys.sort == KEYS.sort,
                'Keycloak Secret source keys differ from committed example')
    ensure_true(values['KEYCLOAK_DB_NAME'] == DATABASE && values['KEYCLOAK_DB_USERNAME'] == ROLE,
                'Keycloak database or role differs from reviewed identity')
    password = values['KEYCLOAK_DB_PASSWORD']
    ensure_true(password.is_a?(String) && password.length >= 8 && !password.include?("\n") &&
                password != example.dig('stringData', 'KEYCLOAK_DB_PASSWORD'),
                'Keycloak database Secret uses an invalid or placeholder password')
    values
  end

  def load_job
    job = YAML.safe_load(JOB_PATH.read)
    pod = job.dig('spec', 'template', 'spec')
    containers = pod&.fetch('containers', nil)
    env = containers&.first&.fetch('env', nil)
    refs = env&.map do |entry|
      key = entry['valueFrom']&.dig('secretKeyRef')
      [entry['name'], key['name'], key['key']] if key
    end&.compact
    expected = [
      ['PGDATABASE', 'clouddsp-postgresql-credentials', 'POSTGRES_DB'],
      ['PGUSER', 'clouddsp-postgresql-credentials', 'POSTGRES_USER'],
      ['PGPASSWORD', 'clouddsp-postgresql-credentials', 'POSTGRES_PASSWORD'],
      ['KEYCLOAK_DB_NAME', SECRET, 'KEYCLOAK_DB_NAME'],
      ['KEYCLOAK_DB_USERNAME', SECRET, 'KEYCLOAK_DB_USERNAME'],
      ['KEYCLOAK_DB_PASSWORD', SECRET, 'KEYCLOAK_DB_PASSWORD']
    ]
    ensure_true(job['apiVersion'] == 'batch/v1' && job['kind'] == 'Job' &&
                job.dig('metadata', 'name') == JOB && job.dig('metadata', 'namespace') == NAMESPACE &&
                job.dig('spec', 'backoffLimit') == 0 &&
                job.dig('spec', 'activeDeadlineSeconds').is_a?(Integer) &&
                job.dig('spec', 'activeDeadlineSeconds').positive? &&
                pod&.dig('automountServiceAccountToken') == false &&
                containers.is_a?(Array) && containers.length == 1 &&
                containers.first['image'].to_s.match?(/\A[^\s]+@sha256:[0-9a-f]{64}\z/) &&
                refs&.sort == expected.sort,
                'versioned Keycloak database Job changed its reviewed contract')
    job
  end

  def command(label, *argv, **options)
    stdout, _stderr, status = @command.call(*argv, **options)
    ensure_true(status.success?, "#{label} failed; inspect the named resource or Pod logs")
    stdout
  end

  def kubectl(*args)
    command('Kubernetes operation', 'kubectl', '--context', CONTEXT, '--namespace', NAMESPACE, *args)
  end

  def live_resource(kind, name)
    output = kubectl('get', "#{kind}/#{name}", '--ignore-not-found', '--output=json', '--request-timeout=15s')
    output.empty? ? nil : JSON.parse(output)
  end

  def verify_secret(secret, values)
    ensure_true(secret.is_a?(Hash) && secret['kind'] == 'Secret' &&
                secret.dig('metadata', 'name') == SECRET &&
                secret.dig('metadata', 'namespace') == NAMESPACE && secret['type'] == 'Opaque',
                'live Keycloak database Secret identity or type differs')
    labels = YAML.safe_load(EXAMPLE_PATH.read).dig('metadata', 'labels')
    ensure_true(labels.all? { |key, value| secret.dig('metadata', 'labels', key) == value },
                'live Keycloak database Secret labels differ')
    data = secret['data']
    ensure_true(data.is_a?(Hash) && data.keys.sort == KEYS.sort &&
                data.all? { |key, encoded| Base64.strict_decode64(encoded) == values.fetch(key) },
                'live Keycloak database Secret differs from ignored local source')
  rescue ArgumentError
    raise 'live Keycloak database Secret has invalid encoded data'
  end

  def completed?(job)
    job.fetch('status', {}).fetch('conditions', []).any? do |condition|
      condition['type'] == 'Complete' && condition['status'] == 'True'
    end
  end

  def psql(query, database = '')
    kubectl('exec', 'pod/clouddsp-postgresql-0', '--', 'sh', '-ec', PSQL_IN_POD,
            'keycloak-audit', database, query).strip
  end

  def authenticate(credentials)
    command('Keycloak database credential authentication', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'exec', '-i', 'pod/clouddsp-postgresql-0', '--',
            'sh', '-ec', AUTH_IN_POD,
            stdin_data: "#{credentials.fetch('KEYCLOAK_DB_PASSWORD')}\n")
  end

  def row(query, database = '')
    output = psql(query, database)
    output.empty? ? nil : JSON.parse(output)
  end

  def read_state
    database = row(DATABASE_QUERY)
    role = row(ROLE_QUERY)
    schema = database && role ? row(SCHEMA_QUERY, DATABASE) : nil
    assess(database, role, schema)
  end

  def ensure_postgresql_ready
    ensure_true(!live_resource('secret', 'clouddsp-postgresql-credentials').nil?,
                'PostgreSQL administrator Secret is absent')
    database = live_resource('statefulset', 'clouddsp-postgresql')
    ensure_true(database&.dig('status', 'readyReplicas') == 1,
                'PostgreSQL StatefulSet must be Ready before Keycloak bootstrap')
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: keycloak-database-stage.rb plan|bootstrap|verify' unless ARGV.length == 1
  exit CloudDSPKeycloakDatabaseStage.new.run(ARGV.first)
end
