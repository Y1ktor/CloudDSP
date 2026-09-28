#!/usr/bin/env ruby
# Reconcile the Job API's immutable PostgreSQL migrations in numeric order.
# The ledger, not the lifetime of a Kubernetes Job, records successful DDL.
# This stage assumes PostgreSQL, the clouddsp_job_api database, and the Job API
# schema-owner Secret have already been bootstrapped. It never reads Secret
# values into the host process or prints database credentials.
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class JobApiMigrations
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-app'.freeze
  ROOT = Pathname.new(File.expand_path('..', __dir__)).freeze
  SOURCE = ROOT.join('services', 'api').freeze
  CONFIG_PATTERN = 'job-api-schema-migration-v[0-9][0-9][0-9]*-configmap.yaml'.freeze
  TABLE_QUERY = "SELECT CASE WHEN to_regclass('public.schema_migrations') IS NULL THEN 'absent' ELSE 'present' END;".freeze
  LEDGER_QUERY = 'SELECT row_to_json(ledger) FROM (SELECT migration_id, description FROM public.schema_migrations ORDER BY migration_id) AS ledger;'.freeze
  # Kubernetes passes this fixed shell program to the existing database Pod.
  # The password expands only inside that Pod, from its existing Secret-backed
  # environment. SQL is supplied as a separate argv value, never interpolated
  # into the program or a host-side shell command.
  PSQL_IN_POD = 'export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=10; exec psql --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --host=127.0.0.1 --username="$POSTGRES_USER" --dbname=clouddsp_job_api --tuples-only --no-align --command "$1"'.freeze
  Migration = Struct.new(:number, :id, :description, :config_path, :job_path, :config, :job, keyword_init: true)

  def initialize(runner: nil)
    @runner = runner || method(:capture)
  end

  def run(mode)
    raise ArgumentError, 'use plan, verify, or reconcile' unless %w[plan verify reconcile].include?(mode)

    migrations = load_sources
    rows = read_ledger
    applied = audit_ledger(migrations, rows)
    # An applied row must still have its original immutable SQL ConfigMap.
    # Otherwise the committed source cannot be compared with the SQL retained
    # in the cluster, and an accidental historical edit would go unnoticed.
    configs = migrations.map { |migration| live_object('configmap', migration.config.dig('metadata', 'name')) }
    migrations.each_with_index do |migration, index|
      config = configs.fetch(index)
      if config
        ensure_true(config['immutable'] == true && config['data'] == migration.config['data'],
                    "#{migration.id}: live immutable SQL ConfigMap differs from source")
      elsif index < applied
        raise "#{migration.id}: applied migration has no immutable SQL ConfigMap"
      end
    end

    pending = migrations.drop(applied)
    # A fixed-name Job with no ledger row could be running, failed, or have
    # committed only part of an external action. Preserve it for inspection.
    pending.each do |migration|
      ensure_true(live_object('job', migration.job.dig('metadata', 'name')).nil?,
                  "#{migration.id}: Job exists without a ledger row; inspect it before retrying")
    end
    puts "Job API migrations: #{applied}/#{migrations.length} applied; pending: #{pending.empty? ? 'none' : pending.map(&:id).join(', ')}"
    if mode == 'verify'
      ensure_true(pending.empty?, 'schema migration ledger is incomplete')
    elsif mode == 'reconcile'
      ensure_prerequisites unless pending.empty?
      pending.each_with_index do |migration, offset|
        # Recheck the authoritative ledger before each write. Another runner
        # may have advanced it since the initial snapshot.
        ensure_true(audit_ledger(migrations, read_ledger) == applied + offset,
                    "#{migration.id}: ledger advanced during reconciliation")
        install_migration(migration, configs.fetch(applied + offset))
        ensure_true(audit_ledger(migrations, read_ledger) == applied + offset + 1,
                    "#{migration.id}: Job finished without the expected ledger entry")
        puts "#{migration.id}: applied and recorded"
      end
    end
    puts "Job API migration #{mode} passed"
  rescue StandardError => error
    warn "Job API migration #{mode} stopped: #{error.message}"
    exit 1
  end

  # The ledger must be an exact prefix of the versioned source inventory.
  # Checking descriptions catches a changed or foreign row with a familiar ID.
  def audit_ledger(migrations, rows)
    ensure_true(rows.length <= migrations.length, 'schema ledger has more rows than versioned migrations')
    rows.each_with_index do |row, index|
      expected = migrations.fetch(index)
      ensure_true(row['migration_id'] == expected.id && row['description'] == expected.description,
                  "schema ledger differs from #{expected.id} at position #{index + 1}")
    end
    rows.length
  end

  def load_sources
    paths = Dir.glob(SOURCE.join(CONFIG_PATTERN).to_s).sort
    ensure_true(!paths.empty?, 'no versioned Job API migrations found')
    paths.each_with_index.map do |path, index|
      number = index + 1
      actual_number = File.basename(path)[/migration-v(\d{3})/, 1]&.to_i
      ensure_true(actual_number == number, "migration source sequence skips v#{format('%03d', number)}")
      config = YAML.load_file(path)
      job_path = path.sub(/-configmap\.yaml\z/, '-job.yaml')
      ensure_true(File.file?(job_path), "migration v#{format('%03d', number)} has no matching Job")
      job = YAML.load_file(job_path)
      sql_data = config.fetch('data')
      ensure_true(config['kind'] == 'ConfigMap' && config.dig('metadata', 'namespace') == NAMESPACE &&
                  config['immutable'] == true && sql_data.length == 1,
                  "migration v#{format('%03d', number)} must use one immutable app-namespace SQL ConfigMap")
      sql_key, sql = sql_data.first
      match = sql.match(/INSERT INTO (?:public\.)?schema_migrations\s*\(migration_id, description\)\s*VALUES\s*\(\s*'([^']+)'\s*,\s*'([^']+)'/i)
      ensure_true(match, "migration v#{format('%03d', number)} has no literal ledger entry")
      id, description = match.captures
      ensure_true(id.start_with?("v#{format('%03d', number)}_") && sql_key == "#{id.delete_prefix('v')}.sql",
                  "migration v#{format('%03d', number)} SQL key and ledger ID disagree")
      ensure_true(job['kind'] == 'Job' && job.dig('metadata', 'namespace') == NAMESPACE &&
                  job.dig('metadata', 'name') == config.dig('metadata', 'name') &&
                  job.dig('spec', 'template', 'spec', 'volumes')&.any? { |volume|
                    volume.dig('configMap', 'name') == config.dig('metadata', 'name') &&
                      volume.dig('configMap', 'items')&.any? { |item| item['key'] == sql_key }
                  }, "#{id}: Job does not mount its matching SQL ConfigMap")
      containers = job.dig('spec', 'template', 'spec', 'containers')
      ensure_true(containers&.length == 1 &&
                  containers.first.fetch('command').join(' ').include?("/migrations/#{sql_key}"),
                  "#{id}: Job command does not execute its versioned SQL file")
      ensure_true(job.dig('spec', 'activeDeadlineSeconds').to_i.positive?, "#{id}: Job needs a bounded deadline")
      Migration.new(number: number, id: id, description: description,
                    config_path: path, job_path: job_path, config: config, job: job)
    end
  end

  private

  def ensure_true(condition, message)
    raise message unless condition
  end

  def capture(*argv)
    stdout, _stderr, status = Open3.capture3(*argv)
    unless status.success?
      action = argv.include?('exec') ? 'PostgreSQL ledger query' : "kubectl #{argv[5]} #{argv[6]}"
      raise "#{action} failed (exit #{status.exitstatus}); inspect the named resource and its Pod logs"
    end
    stdout
  end

  def command(*argv)
    @runner.call(*argv)
  end

  def kubectl(*args)
    command('kubectl', '--context', CONTEXT, '--namespace', NAMESPACE, *args)
  end

  def psql(sql)
    command('kubectl', '--context', CONTEXT, '--namespace', 'clouddsp-data',
            'exec', 'pod/clouddsp-postgresql-0', '--', 'sh', '-ec', PSQL_IN_POD, 'migration-ledger', sql).strip
  end

  def read_ledger
    state = psql(TABLE_QUERY)
    ensure_true(%w[absent present].include?(state), 'unexpected schema ledger existence result')
    return [] if state == 'absent'

    output = psql(LEDGER_QUERY)
    return [] if output.empty?
    output.lines.map { |line| JSON.parse(line) }
  end

  def live_object(kind, name)
    output = kubectl('get', "#{kind}/#{name}", '--ignore-not-found', '--output=json').strip
    output.empty? ? nil : JSON.parse(output)
  end

  def ensure_prerequisites
    # The Job's schema-owner Secret must exist, but its data is never fetched.
    kubectl('get', 'secret/clouddsp-job-api-database-credentials', '--output=name')
    database = JSON.parse(command('kubectl', '--context', CONTEXT, '--namespace', 'clouddsp-data',
                                  'get', 'statefulset/clouddsp-postgresql', '--output=json'))
    ensure_true(database.dig('status', 'readyReplicas') == 1,
                'PostgreSQL StatefulSet must be Ready before migration Jobs')
  end

  def install_migration(migration, existing_config)
    unless existing_config
      kubectl('create', '--dry-run=server', '--filename', migration.config_path)
      kubectl('create', '--filename', migration.config_path)
      config = live_object('configmap', migration.config.dig('metadata', 'name'))
      ensure_true(config && config['immutable'] == true && config['data'] == migration.config['data'],
                  "#{migration.id}: created ConfigMap differs from reviewed SQL")
    end
    kubectl('create', '--dry-run=server', '--filename', migration.job_path)
    kubectl('create', '--filename', migration.job_path)
    name = migration.job.dig('metadata', 'name')
    # Add one minute to this versioned Job's active deadline for Pod startup
    # and controller condition propagation, while keeping host waits bounded.
    timeout = migration.job.dig('spec', 'activeDeadlineSeconds').to_i + 60
    kubectl('wait', '--for=condition=complete', "job/#{name}", "--timeout=#{timeout}s")
  rescue StandardError => error
    raise "#{migration.id}: #{error.message}"
  end
end

JobApiMigrations.new.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
