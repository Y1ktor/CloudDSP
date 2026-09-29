#!/usr/bin/env ruby
# Bootstrap the Basic Pitch and ADTOF database roles after schema migration
# v006 and before v007. The versioned Jobs own their SQL and privilege checks;
# this runner owns Secret matching, creation order, and durable role inspection.
# A failed Job and its temporary Secret remain for diagnosis. No password or
# Kubernetes Secret value is written to stdout or stderr.
require 'base64'
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class CloudDSPWorkerDatabaseStage
  CONTEXT = 'k3d-clouddsp-local'.freeze
  ROOT = Pathname.new(File.expand_path('..', __dir__)).freeze
  LOCAL = ROOT.parent.join('.local').freeze
  WORKERS = {
    'basic-pitch' => { prefix: 'BASIC_PITCH', role: 'clouddsp-basic-pitch', migration: 'v005_basic_pitch_processing_tasks' },
    'adtof' => { prefix: 'ADTOF', role: 'clouddsp-adtof', migration: 'v006_adtof_processing_tasks' }
  }.freeze
  PSQL_IN_POD = 'export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=10; exec psql --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --host=127.0.0.1 --username="$POSTGRES_USER" --dbname=clouddsp_job_api --tuples-only --no-align --command "$1"'.freeze

  def initialize(worker, command: Open3.method(:capture3), output: $stdout, error: $stderr,
                 local: LOCAL)
    @worker = worker
    @config = WORKERS.fetch(worker)
    @command = command
    @output = output
    @error = error
    @local = Pathname.new(local)
  end

  def run(mode)
    check(%w[plan bootstrap verify].include?(mode), 'use plan, bootstrap, or verify')
    values = local_credentials
    runtime = live('clouddsp-app', 'secret', runtime_name)
    temporary = live('clouddsp-data', 'secret', temporary_name)
    job = live('clouddsp-data', 'job', job_name)
    role = role_metadata

    if mode == 'verify'
      check(runtime && matching_secret?(runtime, values, runtime_name, 'clouddsp-app'), 'runtime Secret is absent or differs')
      check(temporary.nil?, 'temporary bootstrap Secret remains')
      check(role == expected_role, 'PostgreSQL role metadata or privileges differ')
      check(job.nil? || complete?(job), 'bootstrap Job is active or failed')
      @output.puts "#{@worker} PostgreSQL role and runtime Secret verified"
      return 0
    end

    # Fresh bootstrap never adopts a role or Secret whose password cannot be
    # compared with the ignored source. It also refuses a prior failed Job.
    check(runtime.nil? && temporary.nil? && job.nil? && role.nil?,
          'role, Secret, or bootstrap Job already exists; inspect or use verify')
    check(migration_applied?, "required #{@config[:migration]} migration is absent")
    check(postgresql_ready?, 'PostgreSQL StatefulSet is not Ready')
    validate_job
    if mode == 'plan'
      @output.puts "#{@worker} PostgreSQL role bootstrap ready"
      return 0
    end

    # Both namespace copies contain exactly the same reviewed credential.
    # The temporary copy is needed only while the data-namespace Job runs.
    create('clouddsp-app', runtime_path)
    check(matching_secret?(live('clouddsp-app', 'secret', runtime_name), values, runtime_name, 'clouddsp-app'),
          'created runtime Secret differs from ignored source')
    create('clouddsp-data', temporary_path)
    create('clouddsp-data', job_path)
    run_command('bootstrap Job wait', 'kubectl', '--context', CONTEXT, '--namespace', 'clouddsp-data',
                'wait', '--for=condition=complete', "job/#{job_name}", '--timeout=180s')
    check(role_metadata == expected_role, 'completed Job left unexpected PostgreSQL role privileges')
    run_command('temporary Secret removal', 'kubectl', '--context', CONTEXT, '--namespace', 'clouddsp-data',
                'delete', "secret/#{temporary_name}")
    @output.puts "#{@worker} PostgreSQL role bootstrapped; temporary Secret removed"
    0
  rescue StandardError => exception
    # Command output can include credentials if API validation fails. Report
    # only fixed operation labels or our own bounded, nonsecret messages.
    @error.puts "#{@worker} PostgreSQL #{mode} stopped: #{exception.instance_of?(RuntimeError) ? exception.message : exception.class}"
    1
  end

  private

  def check(condition, message)
    raise message unless condition
  end

  def prefix
    @config.fetch(:prefix)
  end

  def keys
    %W[#{prefix}_DB_NAME #{prefix}_DB_USERNAME #{prefix}_DB_PASSWORD]
  end

  def runtime_name
    "clouddsp-#{@worker}-database-credentials"
  end

  def temporary_name
    "clouddsp-#{@worker}-database-bootstrap-credentials"
  end

  def job_name
    "#{@worker}-database-bootstrap"
  end

  def runtime_path
    @local.join("#{@worker}-database-credentials.secret.yaml")
  end

  def temporary_path
    @local.join("#{@worker}-database-bootstrap-credentials.secret.yaml")
  end

  def job_path
    ROOT.join('services', @worker, "#{job_name}-job.yaml")
  end

  def validate_job
    job = YAML.safe_load(job_path.read, aliases: true)
    pod = job.dig('spec', 'template', 'spec')
    containers = pod && pod['containers']
    references = containers&.first&.fetch('env')&.map do |entry|
      entry.dig('valueFrom', 'secretKeyRef', 'name')
    end&.compact&.uniq&.sort
    check(job['kind'] == 'Job' && job.dig('metadata', 'name') == job_name &&
          job.dig('metadata', 'namespace') == 'clouddsp-data' &&
          job.dig('spec', 'backoffLimit') == 0 &&
          job.dig('spec', 'activeDeadlineSeconds').to_i.positive? &&
          pod['automountServiceAccountToken'] == false &&
          containers.is_a?(Array) && containers.length == 1 &&
          references == [temporary_name, 'clouddsp-postgresql-credentials'].sort,
          'versioned worker database Job changed its safety contract')
  end

  def source(path, name, namespace)
    check(path.file?, "ignored #{name} source is absent")
    data = YAML.safe_load(path.read, aliases: true)
    example = YAML.safe_load(ROOT.join('services', @worker, path.basename.to_s.sub('.secret.yaml', '.secret.example.yaml')).read)
    check(data.is_a?(Hash) && example.is_a?(Hash) &&
          %w[apiVersion kind metadata type].all? { |field| data[field] == example[field] } &&
          data['kind'] == 'Secret' && data.dig('metadata', 'name') == name &&
          data.dig('metadata', 'namespace') == namespace && data['type'] == 'Opaque' &&
          data['data'].nil? && data.fetch('stringData').keys.sort == keys.sort,
          "ignored #{name} source contract differs")
    data.fetch('stringData')
  end

  def local_credentials
    runtime = source(runtime_path, runtime_name, 'clouddsp-app')
    temporary = source(temporary_path, temporary_name, 'clouddsp-data')
    check(runtime == temporary, 'runtime and temporary credentials differ')
    check(runtime.fetch("#{prefix}_DB_NAME") == 'clouddsp_job_api' &&
          runtime.fetch("#{prefix}_DB_USERNAME") == @config.fetch(:role), 'database or role identity differs')
    password = runtime.fetch("#{prefix}_DB_PASSWORD")
    check(password.is_a?(String) && password.length >= 8 && !password.include?("\n") &&
          !password.start_with?('REPLACE_'), 'database password is invalid or a placeholder')
    runtime
  end

  def run_command(label, *argv)
    stdout, _stderr, status = @command.call(*argv)
    check(status.success?, "#{label} failed")
    stdout
  end

  def live(namespace, kind, name)
    output = run_command("#{kind} lookup", 'kubectl', '--context', CONTEXT, '--namespace', namespace,
                         'get', "#{kind}/#{name}", '--ignore-not-found', '--output=json', '--request-timeout=15s').strip
    output.empty? ? nil : JSON.parse(output)
  end

  def matching_secret?(secret, values, name, namespace)
    secret && secret['kind'] == 'Secret' && secret.dig('metadata', 'name') == name &&
      secret.dig('metadata', 'namespace') == namespace && secret['type'] == 'Opaque' &&
      secret.fetch('data').keys.sort == keys.sort &&
      secret.fetch('data').all? { |key, encoded| Base64.strict_decode64(encoded) == values.fetch(key) }
  rescue ArgumentError
    false
  end

  def role_metadata
    # This fixed SQL selects structural booleans only. It never reads
    # pg_authid, password verifiers, or rows in application tables.
    role = @config.fetch(:role)
    lock_function = "public.clouddsp_lock_#{@worker.tr('-', '_')}_job_for_claim(uuid)"
    sql = <<~SQL
      SELECT row_to_json(r) FROM (
        SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
               rolreplication, rolinherit, rolbypassrls,
               has_column_privilege('#{role}', 'public.processing_tasks', 'task_id', 'INSERT') AS can_create_task,
               has_column_privilege('#{role}', 'public.jobs', 'owner_sub', 'SELECT') AS can_read_owner,
               has_column_privilege('#{role}', 'public.jobs', 'status', 'UPDATE') AS can_update_job,
               has_function_privilege('#{role}', '#{lock_function}', 'EXECUTE') AS can_lock_claim
        FROM pg_roles WHERE rolname = '#{role}'
      ) AS r;
    SQL
    output = run_command('PostgreSQL role metadata query', 'kubectl', '--context', CONTEXT,
                         '--namespace', 'clouddsp-data', 'exec', 'pod/clouddsp-postgresql-0', '--',
                         'sh', '-ec', PSQL_IN_POD, 'role-audit', sql).strip
    output.empty? ? nil : JSON.parse(output)
  end

  def expected_role
    { 'rolname' => @config.fetch(:role), 'rolcanlogin' => true,
      'rolsuper' => false, 'rolcreatedb' => false, 'rolcreaterole' => false,
      'rolreplication' => false, 'rolinherit' => false, 'rolbypassrls' => false,
      'can_create_task' => true, 'can_read_owner' => false,
      'can_update_job' => false, 'can_lock_claim' => true }
  end

  def migration_applied?
    sql = "SELECT EXISTS (SELECT 1 FROM public.schema_migrations WHERE migration_id = '#{@config.fetch(:migration)}');"
    run_command('migration prerequisite query', 'kubectl', '--context', CONTEXT,
                '--namespace', 'clouddsp-data', 'exec', 'pod/clouddsp-postgresql-0', '--',
                'sh', '-ec', PSQL_IN_POD, 'migration-audit', sql).strip == 't'
  end

  def postgresql_ready?
    state = live('clouddsp-data', 'statefulset', 'clouddsp-postgresql')
    state && state.dig('status', 'readyReplicas') == 1
  end

  def complete?(job)
    job.fetch('status', {}).fetch('conditions', []).any? { |condition|
      condition['type'] == 'Complete' && condition['status'] == 'True'
    }
  end

  def create(namespace, path)
    check(path.file?, "versioned or ignored #{path.basename} source is absent")
    %w[server actual].each do |pass|
      args = pass == 'server' ? ['--dry-run=server'] : []
      run_command("#{path.basename} #{pass} create", 'kubectl', '--context', CONTEXT,
                  '--namespace', namespace, 'create', *args, '--filename', path.to_s)
    end
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: worker-database-stage.rb basic-pitch|adtof plan|bootstrap|verify' unless ARGV.length == 2 && CloudDSPWorkerDatabaseStage::WORKERS.key?(ARGV.first)
  exit CloudDSPWorkerDatabaseStage.new(ARGV.first).run(ARGV.last)
end
