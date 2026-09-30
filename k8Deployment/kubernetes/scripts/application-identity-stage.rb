#!/usr/bin/env ruby
# Provision one restricted application identity from its ignored app-namespace
# credential source and a reviewed, versioned Job. Runtime Secrets belong in
# clouddsp-app; temporary administrator-Job copies are generated in memory
# from the same reviewed values, dry-run validated, and removed after success.
# Durable PostgreSQL grants and RabbitMQ permissions are checked directly so
# root verify does not depend on short-lived Job history.
require 'base64'
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class CloudDSPApplicationIdentityStage
  CONTEXT = 'k3d-clouddsp-local'.freeze
  ROOT = Pathname.new(File.expand_path('..', __dir__)).freeze
  LOCAL = ROOT.parent.join('.local').freeze
  MODES = %w[plan bootstrap verify].freeze
  PSQL_IN_POD = 'export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=10; exec psql --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --host=127.0.0.1 --username="$POSTGRES_USER" --dbname=clouddsp_job_api --tuples-only --no-align --command "$1"'.freeze
  RABBIT_JSON = %w[rabbitmqctl -q].freeze

  DATABASE_IDENTITIES = {
    'upload-intake' => {
      runtime_local: 'upload-intake-database-credentials.secret.yaml',
      runtime_example: 'services/upload-intake/upload-intake-database-credentials.secret.example.yaml',
      bootstrap_example: 'services/upload-intake/upload-intake-database-bootstrap-credentials.secret.example.yaml',
      bootstrap_name: 'clouddsp-upload-intake-database-bootstrap-credentials',
      role: 'clouddsp-upload-intake',
      database_key: 'UPLOAD_INTAKE_DB_NAME',
      username_key: 'UPLOAD_INTAKE_DB_USERNAME',
      password_key: 'UPLOAD_INTAKE_DB_PASSWORD',
      jobs: %w[
        services/upload-intake/upload-intake-database-bootstrap-job.yaml
        services/upload-intake/upload-intake-outbox-permissions-bootstrap-job.yaml
      ],
      required: [
        [:column, 'public.jobs', 'job_id', 'SELECT'],
        [:column, 'public.jobs', 'source_uploaded', 'UPDATE'],
        [:column, 'public.outbox_events', 'event_id', 'INSERT'],
        [:column, 'public.outbox_events', 'stage', 'SELECT']
      ],
      forbidden: [
        [:column, 'public.jobs', 'owner_sub', 'SELECT'],
        [:column, 'public.outbox_events', 'payload', 'SELECT'],
        [:table, 'public.outbox_events', 'UPDATE'],
        [:table, 'public.outbox_events', 'DELETE']
      ]
    },
    'dispatcher' => {
      runtime_local: 'dispatcher-database-credentials.secret.yaml',
      runtime_example: 'services/dispatcher/dispatcher-database-credentials.secret.example.yaml',
      bootstrap_example: 'services/dispatcher/dispatcher-database-bootstrap-credentials.secret.example.yaml',
      bootstrap_name: 'clouddsp-dispatcher-database-bootstrap-credentials',
      role: 'clouddsp-dispatcher',
      database_key: 'DISPATCHER_DB_NAME',
      username_key: 'DISPATCHER_DB_USERNAME',
      password_key: 'DISPATCHER_DB_PASSWORD',
      jobs: %w[services/dispatcher/dispatcher-database-bootstrap-job.yaml],
      required: [
        [:column, 'public.outbox_events', 'payload', 'SELECT'],
        [:column, 'public.outbox_events', 'publication_status', 'UPDATE'],
        [:column, 'public.outbox_events', 'lease_token', 'UPDATE']
      ],
      forbidden: [
        [:table, 'public.jobs', 'SELECT'],
        [:column, 'public.outbox_events', 'event_id', 'INSERT'],
        [:table, 'public.outbox_events', 'DELETE']
      ]
    },
    'demucs' => {
      runtime_local: 'demucs-database-credentials.secret.yaml',
      runtime_example: 'services/demucs/demucs-database-credentials.secret.example.yaml',
      bootstrap_example: 'services/demucs/demucs-database-bootstrap-credentials.secret.example.yaml',
      bootstrap_name: 'clouddsp-demucs-database-bootstrap-credentials',
      role: 'clouddsp-demucs',
      database_key: 'DEMUCS_DB_NAME',
      username_key: 'DEMUCS_DB_USERNAME',
      password_key: 'DEMUCS_DB_PASSWORD',
      jobs: %w[
        services/demucs/demucs-database-bootstrap-job.yaml
        services/demucs/demucs-downstream-outbox-permissions-bootstrap-job.yaml
        services/demucs/demucs-recovery-verifier-bootstrap-job.yaml
      ],
      required: [
        [:column, 'public.jobs', 'job_id', 'SELECT'],
        [:column, 'public.processing_tasks', 'task_id', 'INSERT'],
        [:function, 'public.clouddsp_lock_demucs_job_for_claim(uuid)', 'EXECUTE'],
        [:function, 'public.clouddsp_demucs_recovery_event_matches(uuid, uuid, uuid, text, text, text, integer, uuid)', 'EXECUTE'],
        [:column, 'public.outbox_events', 'event_id', 'INSERT']
      ],
      forbidden: [
        [:column, 'public.jobs', 'owner_sub', 'SELECT'],
        [:column, 'public.outbox_events', 'payload', 'SELECT'],
        [:column, 'public.jobs', 'status', 'UPDATE'],
        [:table, 'public.outbox_events', 'DELETE']
      ]
    },
    'keda-demucs' => {
      runtime_local: 'keda-demucs-postgresql-credentials.secret.yaml',
      runtime_example: 'helm/keda/keda-demucs-postgresql-credentials.secret.example.yaml',
      bootstrap_example: 'services/postgresql/postgresql-keda-demucs-bootstrap-credentials.secret.example.yaml',
      bootstrap_name: 'clouddsp-keda-demucs-postgresql-bootstrap-credentials',
      role: 'clouddsp-keda-demucs',
      password_key: 'POSTGRESQL_KEDA_DEMUCS_PASSWORD',
      value_mapping: { 'KEDA_DEMUCS_DB_USERNAME' => 'clouddsp-keda-demucs',
                       'KEDA_DEMUCS_DB_PASSWORD' => 'POSTGRESQL_KEDA_DEMUCS_PASSWORD' },
      jobs: %w[services/postgresql/postgresql-keda-demucs-bootstrap-job.yaml],
      required: [
        [:column, 'public.processing_tasks', 'stage', 'SELECT'],
        [:column, 'public.processing_tasks', 'status', 'SELECT'],
        [:column, 'public.processing_tasks', 'available_at', 'SELECT']
      ],
      forbidden: [
        [:column, 'public.processing_tasks', 'job_id', 'SELECT'],
        [:table, 'public.jobs', 'SELECT'],
        [:table, 'public.outbox_events', 'SELECT'],
        [:table, 'public.processing_tasks', 'INSERT'],
        [:table, 'public.processing_tasks', 'UPDATE'],
        [:table, 'public.processing_tasks', 'DELETE']
      ]
    }
  }.freeze

  RABBITMQ_IDENTITIES = {
    'dispatcher' => {
      runtime_local: 'dispatcher-rabbitmq-credentials.secret.yaml',
      runtime_example: 'services/dispatcher/dispatcher-rabbitmq-credentials.secret.example.yaml',
      bootstrap_example: 'services/rabbitmq/rabbitmq-dispatcher-publisher-bootstrap-credentials.secret.example.yaml',
      bootstrap_name: 'clouddsp-dispatcher-rabbitmq-bootstrap-credentials',
      job: 'services/rabbitmq/rabbitmq-dispatcher-publisher-bootstrap-job.yaml',
      username_key: 'RABBITMQ_DISPATCHER_USERNAME',
      password_key: 'RABBITMQ_DISPATCHER_PASSWORD',
      username: 'clouddsp-dispatcher',
      tags: [],
      permissions: { 'vhost' => '/clouddsp', 'configure' => '^$',
                     'write' => '^clouddsp\\.processing-events$', 'read' => '^$' }
    },
    'demucs' => {
      runtime_local: 'demucs-rabbitmq-credentials.secret.yaml',
      runtime_example: 'services/demucs/demucs-rabbitmq-credentials.secret.example.yaml',
      bootstrap_example: 'services/rabbitmq/rabbitmq-demucs-consumer-bootstrap-credentials.secret.example.yaml',
      bootstrap_name: 'clouddsp-demucs-rabbitmq-bootstrap-credentials',
      job: 'services/rabbitmq/rabbitmq-demucs-consumer-bootstrap-job.yaml',
      username_key: 'RABBITMQ_DEMUCS_USERNAME',
      password_key: 'RABBITMQ_DEMUCS_PASSWORD',
      username: 'clouddsp-demucs',
      tags: [],
      permissions: { 'vhost' => '/clouddsp', 'configure' => '^$', 'write' => '^$',
                     'read' => '^clouddsp\\.demucs\\.requests$' }
    },
    'basic-pitch' => {
      runtime_local: 'basic-pitch-rabbitmq-credentials.secret.yaml',
      runtime_example: 'services/basic-pitch/basic-pitch-rabbitmq-credentials.secret.example.yaml',
      bootstrap_example: 'services/rabbitmq/rabbitmq-basic-pitch-consumer-bootstrap-credentials.secret.example.yaml',
      bootstrap_name: 'clouddsp-basic-pitch-rabbitmq-bootstrap-credentials',
      job: 'services/rabbitmq/rabbitmq-basic-pitch-consumer-bootstrap-job.yaml',
      username_key: 'RABBITMQ_BASIC_PITCH_USERNAME',
      password_key: 'RABBITMQ_BASIC_PITCH_PASSWORD',
      username: 'clouddsp-basic-pitch',
      tags: [],
      permissions: { 'vhost' => '/clouddsp', 'configure' => '^$', 'write' => '^$',
                     'read' => '^clouddsp\\.basic-pitch\\.requests$' }
    },
    'adtof' => {
      runtime_local: 'adtof-rabbitmq-credentials.secret.yaml',
      runtime_example: 'services/adtof/adtof-rabbitmq-credentials.secret.example.yaml',
      bootstrap_example: 'services/rabbitmq/rabbitmq-adtof-consumer-bootstrap-credentials.secret.example.yaml',
      bootstrap_name: 'clouddsp-adtof-rabbitmq-bootstrap-credentials',
      job: 'services/rabbitmq/rabbitmq-adtof-consumer-bootstrap-job.yaml',
      username_key: 'RABBITMQ_ADTOF_USERNAME',
      password_key: 'RABBITMQ_ADTOF_PASSWORD',
      username: 'clouddsp-adtof',
      tags: [],
      permissions: { 'vhost' => '/clouddsp', 'configure' => '^$', 'write' => '^$',
                     'read' => '^clouddsp\\.adtof\\.requests$' }
    },
    'keda-scaler' => {
      runtime_local: 'keda-rabbitmq-scaler-credentials.secret.yaml',
      runtime_example: 'helm/keda/keda-rabbitmq-scaler-credentials.secret.example.yaml',
      bootstrap_example: 'services/rabbitmq/rabbitmq-keda-scaler-bootstrap-credentials.secret.example.yaml',
      bootstrap_name: 'clouddsp-keda-rabbitmq-scaler-bootstrap-credentials',
      job: 'services/rabbitmq/rabbitmq-keda-scaler-bootstrap-job.yaml',
      username_key: 'RABBITMQ_KEDA_SCALER_USERNAME',
      password_key: 'RABBITMQ_KEDA_SCALER_PASSWORD',
      username: 'clouddsp-keda-scaler',
      tags: ['monitoring'],
      permissions: { 'vhost' => '/clouddsp', 'configure' => '^$', 'write' => '^$', 'read' => '^$' }
    }
  }.freeze

  def initialize(kind, identity, command: Open3.method(:capture3), local_directory: LOCAL,
                 output: $stdout, error: $stderr)
    @kind = kind
    @identity = identity
    @config = (kind == 'database' ? DATABASE_IDENTITIES : RABBITMQ_IDENTITIES).fetch(identity)
    @command = command
    @local_directory = Pathname.new(local_directory)
    @output = output
    @error = error
  end

  def run(mode)
    ensure_true(MODES.include?(mode), 'use plan, bootstrap, or verify')
    @mode = mode
    @runtime_example = load_yaml(ROOT.join(@config.fetch(:runtime_example)))
    @bootstrap_example = load_yaml(ROOT.join(@config.fetch(:bootstrap_example)))
    @runtime_path = @local_directory.join(@config.fetch(:runtime_local))
    @runtime_values = read_runtime_values
    @bootstrap_manifest = build_bootstrap_manifest
    verify_job_sources
    state = read_identity_state

    if mode == 'verify'
      ensure_true(state == :ready, "#{@identity} #{@kind} identity is missing or drifted")
      verify_complete_state
      @output.puts "#{@identity} #{@kind} identity, runtime Secret, and restricted permissions verified"
      return 0
    end

    ensure_true(state == :absent,
                "#{@identity} #{@kind} identity, Secret, or bootstrap Job already exists; inspect or use verify")
    if mode == 'plan'
      @output.puts "#{@identity} #{@kind} identity plan: reviewed credentials and fresh bootstrap boundary ready"
      return 0
    end

    check_prerequisites
    server_validate_sources
    create_runtime_secret
    create_bootstrap_secret
    run_bootstrap_jobs
    ensure_true(@kind == 'database' ? database_state == :ready : rabbitmq_state == :ready,
                "#{@identity} #{@kind} bootstrap Jobs did not establish the reviewed identity")
    kubectl('delete', '--namespace', 'clouddsp-data', "secret/#{@config.fetch(:bootstrap_name)}")
    ensure_true(read_identity_state == :ready, 'identity verification after bootstrap failed')
    verify_complete_state
    @output.puts "#{@identity} #{@kind} identity bootstrapped; temporary Secret removed"
    0
  rescue StandardError => exception
    @error.puts "#{@identity} #{@kind} #{mode} stopped: #{exception.instance_of?(RuntimeError) ? exception.message : exception.class}"
    1
  end

  private

  def ensure_true(condition, message)
    raise message unless condition
  end

  def load_yaml(path)
    document = YAML.safe_load(path.read, aliases: true)
    ensure_true(document.is_a?(Hash), "#{path.basename} must contain one YAML object")
    document
  rescue Errno::ENOENT, Psych::Exception
    raise "reviewed source #{path.basename} is absent or invalid"
  end

  def read_runtime_values
    ensure_true(@runtime_path.file?, "ignored local #{@identity} #{@kind} runtime Secret is absent")
    source = load_yaml(@runtime_path)
    expected_keys = @runtime_example.fetch('stringData').keys.sort
    ensure_true(%w[apiVersion kind metadata type].all? { |field| source[field] == @runtime_example[field] } &&
                source['data'].nil? && source['stringData'].is_a?(Hash) &&
                source['stringData'].keys.sort == expected_keys,
                "ignored local #{@identity} #{@kind} runtime Secret contract differs from its example")
    values = source.fetch('stringData')
    template_values = @runtime_example.fetch('stringData')
    values.each do |key, value|
      ensure_true(value.is_a?(String) && !value.empty? && !value.include?("\n") &&
                  !value.start_with?('REPLACE_') &&
                  (!key.include?('PASSWORD') || value != template_values.fetch(key)),
                  "ignored local #{@identity} #{@kind} runtime Secret has a placeholder or invalid value")
      ensure_true(!key.include?('PASSWORD') || value.length >= 8,
                  "ignored local #{@identity} #{@kind} password is shorter than the service minimum")
    end
    validate_identity_values(values)
    values
  rescue KeyError
    raise "ignored local #{@identity} #{@kind} runtime Secret contract is invalid"
  end

  def validate_identity_values(values)
    if @kind == 'database'
      if @config[:username_key]
        ensure_true(values.fetch(@config.fetch(:username_key)) == @config.fetch(:role) &&
                    values.fetch(@config.fetch(:database_key)) == 'clouddsp_job_api',
                    'database runtime Secret names an unexpected database or role')
      end
    else
      ensure_true(values.fetch(@config.fetch(:username_key)) == @config.fetch(:username),
                  'RabbitMQ runtime Secret names an unexpected broker user')
    end
  end

  def build_bootstrap_manifest
    template = Marshal.load(Marshal.dump(@bootstrap_example))
    expected = template.fetch('stringData')
    mapping = @config[:value_mapping] || expected.keys.to_h { |key| [key, key] }
    ensure_true(mapping.keys.sort == expected.keys.sort, 'bootstrap/runtime credential key mapping differs from the example')
    mapped = mapping.to_h do |bootstrap_key, runtime_source|
      value = runtime_source.is_a?(String) && @runtime_values.key?(runtime_source) ? @runtime_values.fetch(runtime_source) : runtime_source
      ensure_true(value.is_a?(String) && !value.empty? && !value.start_with?('REPLACE_'),
                  'bootstrap Secret would contain a placeholder credential')
      [bootstrap_key, value]
    end
    expected.each do |key, example_value|
      ensure_true(!mapped.fetch(key).start_with?('REPLACE_') &&
                  (!key.include?('PASSWORD') || mapped.fetch(key) != example_value),
                  'bootstrap Secret credential still uses its example placeholder')
    end
    template['stringData'] = mapped
    template
  rescue KeyError
    raise 'bootstrap credential template is invalid'
  end

  def verify_job_sources
    job_paths.each_with_index do |path, index|
      job = load_yaml(path)
      name = path.basename.to_s.sub(/-job\.yaml\z/, '')
      refs = collect_secret_names(job)
      ensure_true(job['kind'] == 'Job' && job.dig('metadata', 'namespace') == 'clouddsp-data' &&
                  job.dig('metadata', 'name') == name && job.dig('spec', 'backoffLimit') == 0 &&
                  job.dig('spec', 'activeDeadlineSeconds').to_i.positive? &&
                  job.dig('spec', 'template', 'spec', 'automountServiceAccountToken') == false &&
                  (index.positive? || refs.include?(@config.fetch(:bootstrap_name))),
                  "#{path.basename} safety or temporary Secret reference changed")
    end
  end

  def collect_secret_names(value, names = [])
    case value
    when Hash
      reference = value['secretKeyRef']
      names << reference['name'] if reference.is_a?(Hash) && reference['name']
      value.each_value { |child| collect_secret_names(child, names) }
    when Array
      value.each { |child| collect_secret_names(child, names) }
    end
    names
  end

  def job_paths
    @kind == 'database' ? @config.fetch(:jobs).map { |path| ROOT.join(path) } : [ROOT.join(@config.fetch(:job))]
  end

  def check_prerequisites
    if @kind == 'database'
      state = kubectl('--namespace', 'clouddsp-data', 'get', 'statefulset/clouddsp-postgresql', '--output=json')
      status = JSON.parse(state).fetch('status', {})
      ensure_true(status.fetch('readyReplicas', 0).to_i == 1, 'PostgreSQL is not Ready')
      ensure_true(database_state == :absent, "PostgreSQL role #{@config.fetch(:role)} already exists")
    else
      vhosts = rabbitmq_json('list_vhosts', 'name').map { |row| row.fetch('name') }
      ensure_true(vhosts.include?('/clouddsp'), 'RabbitMQ processing vhost is absent')
      ensure_true(rabbitmq_state == :absent, "RabbitMQ user #{@config.fetch(:username)} already exists")
    end
  end

  def read_identity_state
    runtime = live_secret('clouddsp-app', @runtime_example.dig('metadata', 'name'))
    temporary = live_secret('clouddsp-data', @config.fetch(:bootstrap_name))
    jobs = job_paths.map do |path|
      live_object('clouddsp-data', 'job', path.basename.to_s.sub(/-job\.yaml\z/, ''))
    end
    external = @kind == 'database' ? database_state : rabbitmq_state
    return :absent if runtime.nil? && temporary.nil? && jobs.all?(&:nil?) && external == :absent

    complete_jobs = jobs.all? do |job|
      job.nil? || job.fetch('status', {}).fetch('conditions', []).any? do |condition|
        condition['type'] == 'Complete' && condition['status'] == 'True'
      end
    end
    return :ready if runtime && temporary.nil? && complete_jobs && external == :ready

    :partial
  end

  def server_validate_sources
    kubectl('--namespace', 'clouddsp-app', 'create', '--dry-run=server', '--filename', @runtime_path.to_s)
    kubectl('--namespace', 'clouddsp-data', 'create', '--dry-run=server', '--filename=-', stdin_data: YAML.dump(@bootstrap_manifest))
    job_paths.each do |path|
      kubectl('--namespace', 'clouddsp-data', 'create', '--dry-run=server', '--filename', path.to_s)
    end
  end

  def create_runtime_secret
    kubectl('--namespace', 'clouddsp-app', 'create', '--filename', @runtime_path.to_s)
    live = live_secret('clouddsp-app', @runtime_example.dig('metadata', 'name'))
    verify_live_secret(live, @runtime_values, @runtime_example)
  end

  def create_bootstrap_secret
    kubectl('--namespace', 'clouddsp-data', 'create', '--filename=-', stdin_data: YAML.dump(@bootstrap_manifest))
  end

  def run_bootstrap_jobs
    job_paths.each do |path|
      name = path.basename.to_s.sub(/-job\.yaml\z/, '')
      kubectl('--namespace', 'clouddsp-data', 'create', '--filename', path.to_s)
      kubectl('--namespace', 'clouddsp-data', 'wait', '--for=condition=complete', "job/#{name}", '--timeout=180s')
    end
  end

  def database_state
    role = @config.fetch(:role)
    checks = @config.fetch(:required).each_with_index.map { |entry, index| [entry, "required_#{index}", true] } +
             @config.fetch(:forbidden).each_with_index.map { |entry, index| [entry, "forbidden_#{index}", false] }
    expressions = checks.map do |(kind, *fields), alias_name|
      expression = case kind
                   when :column then "has_column_privilege(#{sql_string(role)}, #{sql_string(fields[0])}, #{sql_string(fields[1])}, #{sql_string(fields[2])})"
                   when :table then "has_table_privilege(#{sql_string(role)}, #{sql_string(fields[0])}, #{sql_string(fields[1])})"
                   when :function then "has_function_privilege(#{sql_string(role)}, #{sql_string(fields[0])}, 'EXECUTE')"
                   else raise 'unknown PostgreSQL privilege audit kind'
                   end
      "#{expression} AS #{alias_name}"
    end
    sql = <<~SQL
      SELECT row_to_json(r) FROM (
        SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
               rolreplication, rolinherit, rolbypassrls,
               has_database_privilege(#{sql_string(role)}, 'clouddsp_job_api', 'CONNECT') AS can_connect,
               has_schema_privilege(#{sql_string(role)}, 'public', 'USAGE') AS can_use_schema,
               has_schema_privilege(#{sql_string(role)}, 'public', 'CREATE') AS can_create_schema,
               #{expressions.join(",\n       ")}
        FROM pg_roles WHERE rolname = #{sql_string(role)}
      ) AS r;
    SQL
    result = kubectl('--namespace', 'clouddsp-data', 'exec', 'pod/clouddsp-postgresql-0', '--',
                     'sh', '-ec', PSQL_IN_POD, 'application-identity-audit', sql).strip
    return :absent if result.empty?

    facts = JSON.parse(result)
    expected = {
      'rolname' => role, 'rolcanlogin' => true, 'rolsuper' => false,
      'rolcreatedb' => false, 'rolcreaterole' => false, 'rolreplication' => false,
      'rolinherit' => false, 'rolbypassrls' => false, 'can_connect' => true,
      'can_use_schema' => true, 'can_create_schema' => false
    }
    checks.each do |_entry, alias_name, expected_value|
      expected[alias_name] = expected_value
    end
    facts == expected ? :ready : :partial
  rescue JSON::ParserError
    raise 'PostgreSQL role audit returned invalid output'
  end

  def sql_string(value)
    "'#{value.to_s.gsub("'", "''")}'"
  end

  def rabbitmq_state
    users = rabbitmq_json('list_users')
    user = users.find { |entry| entry['user'] == @config.fetch(:username) }
    return :absent unless user

    permissions = rabbitmq_json('list_user_permissions', @config.fetch(:username))
    permissions == [@config.fetch(:permissions)] && user['tags'] == @config.fetch(:tags) ? :ready : :partial
  end

  def authenticate_database
    password = @runtime_values.fetch(@config.fetch(:password_key))
    user = @config.fetch(:role)
    script = 'IFS= read -r username; IFS= read -r password; export PGPASSWORD="$password" PGCONNECT_TIMEOUT=10; exec psql --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --host=127.0.0.1 --username="$username" --dbname=clouddsp_job_api --tuples-only --no-align --command "SELECT 1"'
    result = kubectl('--namespace', 'clouddsp-data', 'exec', '-i', 'pod/clouddsp-postgresql-0', '--',
                     'sh', '-ec', script, stdin_data: "#{user}\n#{password}\n").strip
    ensure_true(result == '1', 'restricted PostgreSQL login authentication failed')
  end

  def authenticate_rabbitmq
    script = 'IFS= read -r username; IFS= read -r password; exec rabbitmqctl -q authenticate_user "$username" "$password" >/dev/null 2>&1'
    password = @runtime_values.fetch(@config.fetch(:password_key))
    rabbitmq('--namespace', 'clouddsp-data', 'exec', '-i', 'pod/clouddsp-rabbitmq-0', '--',
             'sh', '-ec', script, stdin_data: "#{@config.fetch(:username)}\n#{password}\n")
  end

  def rabbitmq_json(*arguments)
    output = rabbitmq('--namespace', 'clouddsp-data', 'exec', 'pod/clouddsp-rabbitmq-0', '--',
                      *RABBIT_JSON, *arguments, '--formatter', 'json')
    JSON.parse(output)
  rescue JSON::ParserError
    raise 'RabbitMQ identity audit returned invalid output'
  end

  def verify_live_secret(secret, values, example)
    labels = example.dig('metadata', 'labels') || {}
    keys = values.keys.sort
    ensure_true(secret && secret['kind'] == 'Secret' && secret.dig('metadata', 'name') == example.dig('metadata', 'name') &&
                secret.dig('metadata', 'namespace') == example.dig('metadata', 'namespace') &&
                secret['type'] == example['type'] && labels.all? { |key, value| secret.dig('metadata', 'labels', key) == value } &&
                secret.fetch('data', {}).keys.sort == keys &&
                secret.fetch('data').all? { |key, encoded| Base64.strict_decode64(encoded) == values.fetch(key) },
                'live runtime Secret differs from ignored local source')
  rescue ArgumentError, KeyError
    raise 'live runtime Secret identity or data is invalid'
  end

  def live_secret(namespace, name)
    output = kubectl('--namespace', namespace, 'get', "secret/#{name}", '--ignore-not-found', '--output=json').strip
    output.empty? ? nil : JSON.parse(output)
  end

  def live_object(namespace, kind, name)
    output = kubectl('--namespace', namespace, 'get', "#{kind}/#{name}", '--ignore-not-found', '--output=json').strip
    output.empty? ? nil : JSON.parse(output)
  end

  def kubectl(*arguments, stdin_data: nil)
    command('kubectl', '--context', CONTEXT, *arguments, stdin_data: stdin_data)
  end

  def rabbitmq(*arguments, stdin_data: nil)
    command('kubectl', '--context', CONTEXT, *arguments, stdin_data: stdin_data)
  end

  def command(*arguments, stdin_data: nil)
    output, _error, status = @command.call(*arguments, stdin_data: stdin_data)
    ensure_true(status.success?, "#{arguments.first} operation failed")
    output
  end

  def verify_complete_state
    runtime = live_secret('clouddsp-app', @runtime_example.dig('metadata', 'name'))
    temporary = live_secret('clouddsp-data', @config.fetch(:bootstrap_name))
    ensure_true(!runtime.nil? && temporary.nil?,
                "#{@identity} #{@kind} runtime Secret is absent or temporary Secret remains")
    verify_live_secret(runtime, @runtime_values, @runtime_example)
    state = @kind == 'database' ? database_state : rabbitmq_state
    ensure_true(state == :ready, "#{@identity} #{@kind} identity or permissions changed during verification")
    authenticate_database if @kind == 'database'
    authenticate_rabbitmq if @kind == 'rabbitmq'
  end
end

if $PROGRAM_NAME == __FILE__
  unless ARGV.length == 3 && %w[database rabbitmq].include?(ARGV[0])
    abort 'Usage: application-identity-stage.rb database|rabbitmq IDENTITY plan|bootstrap|verify'
  end
  kind, identity, mode = ARGV
  choices = kind == 'database' ? CloudDSPApplicationIdentityStage::DATABASE_IDENTITIES : CloudDSPApplicationIdentityStage::RABBITMQ_IDENTITIES
  abort "Unknown #{kind} identity #{identity}" unless choices.key?(identity)
  exit CloudDSPApplicationIdentityStage.new(kind, identity).run(mode)
end
