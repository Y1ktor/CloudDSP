#!/usr/bin/env ruby
# Reconcile the source-upload RabbitMQ topology and its two narrow users.
# Broker objects and permission regexes, not a TTL-bound Kubernetes Job, are
# the durable completion evidence. Passwords enter this process only to compare
# ignored/runtime Secrets and to run a suppressed broker authentication check.
require_relative '../../lib/paths'
require 'base64'
require 'uri'
require_relative 'rabbitmq-processing-topology'

class RabbitmqSourceIntakeBootstrap < RabbitmqProcessingTopology
  CONFIG = 'rabbitmq-source-intake-topology-v001'.freeze
  JOB = 'rabbitmq-source-intake-bootstrap'.freeze
  TEMP_SECRET = 'clouddsp-upload-intake-rabbitmq-bootstrap-credentials'.freeze
  MINIO_SECRET = 'clouddsp-minio-source-intake-rabbitmq-credentials'.freeze
  UPLOAD_SECRET = 'clouddsp-upload-intake-rabbitmq-credentials'.freeze
  MINIO_USER = 'clouddsp-minio-events'.freeze
  UPLOAD_USER = 'clouddsp-upload-intake'.freeze
  EXPECTED_PERMISSIONS = {
    MINIO_USER => { 'vhost' => VHOST, 'configure' => '^clouddsp\\.source-events$',
                    'write' => '^clouddsp\\.source-events$', 'read' => '^$' },
    UPLOAD_USER => { 'vhost' => VHOST, 'configure' => '^$',
                     'write' => '^clouddsp\\.source-(retry|dead-letter)$',
                     'read' => '^clouddsp\\.source-intake$' }
  }.freeze
  # Credentials travel through kubectl exec standard input. They are never
  # interpolated into the host shell or the recorded Kubernetes exec command.
  AUTH_SCRIPT = 'IFS= read -r username; IFS= read -r password; exec rabbitmqctl -q authenticate_user "$username" "$password" >/dev/null 2>&1'.freeze

  def initialize(runner: nil, authenticator: nil, local_directory: ROOT.parent.join('.local'))
    super(runner: runner)
    @authenticator = authenticator || method(:authenticate)
    @local_directory = Pathname.new(local_directory)
  end

  def run(mode)
    ensure_true(MODES.include?(mode), 'use plan, verify, or reconcile')
    stage = load_stage
    state = read_state(stage)
    inspect_kubernetes(stage, state)
    ensure_true(kubectl('get', "secret/#{TEMP_SECRET}", '--ignore-not-found', '--output=name').strip.empty?,
                'temporary source-intake RabbitMQ bootstrap Secret remains; inspect and remove it')
    credentials = load_credentials

    if state == :ready
      authenticate_credentials(credentials)
      puts 'RabbitMQ source-intake bootstrap: topology, permissions, and credentials verified; no work pending'
    else
      puts 'RabbitMQ source-intake bootstrap: topology and restricted users absent; versioned Job pending'
      if mode == 'verify'
        raise 'source-intake RabbitMQ bootstrap is incomplete'
      elsif mode == 'reconcile'
        ensure_prerequisites
        ensure_true(read_state(stage) == :absent, 'broker state changed during preflight')
        create_bootstrap(stage)
        ensure_true(read_state(stage) == :ready, 'bootstrap Job completed without expected broker state')
        authenticate_credentials(credentials)
        kubectl('delete', "secret/#{TEMP_SECRET}")
        puts 'RabbitMQ source-intake bootstrap applied; temporary Secret removed'
      end
    end
    puts "RabbitMQ source-intake bootstrap #{mode} passed"
  rescue StandardError => error
    warn "RabbitMQ source-intake bootstrap #{mode} stopped: #{error.message}"
    exit 1
  end

  def load_stage
    config_path = SOURCE.join("#{CONFIG}-configmap.yaml")
    job_path = SOURCE.join("#{JOB}-job.yaml")
    config = YAML.load_file(config_path)
    job = YAML.load_file(job_path)
    key = "#{CONFIG}.json"
    ensure_true(config['kind'] == 'ConfigMap' && config.dig('metadata', 'namespace') == NAMESPACE &&
                config.dig('metadata', 'name') == CONFIG && config['immutable'] == true &&
                config.fetch('data').keys == [key], 'source-intake immutable ConfigMap contract changed')
    definition = JSON.parse(config.fetch('data').fetch(key))
    ensure_true((definition.keys - %w[vhosts exchanges queues bindings]).empty? &&
                definition.fetch('vhosts') == [{ 'name' => VHOST }] &&
                %w[exchanges queues bindings].all? { |kind| definition.fetch(kind).length == 3 },
                'source-intake definition contains unexpected broker objects')
    %w[exchanges queues bindings].each do |kind|
      ensure_true(definition.fetch(kind).all? { |item| item['vhost'] == VHOST },
                  "source-intake #{kind} vhost changed")
    end
    pod = job.dig('spec', 'template', 'spec')
    volume = pod.fetch('volumes').find { |item| item.dig('configMap', 'name') == CONFIG }
    user_step = pod.fetch('initContainers').find { |item| item['name'] == 'create-or-rotate-application-users' }
    env = user_step && user_step.fetch('env')
    secret_refs = (env || []).to_h { |item| [item['name'], item.dig('valueFrom', 'secretKeyRef')] }
    script = user_step && user_step.fetch('command').last
    permission_flags = EXPECTED_PERMISSIONS.values.flat_map do |permission|
      %w[configure write read].map { |action| "--#{action} '#{permission.fetch(action)}'" }
    end
    ensure_true(job['kind'] == 'Job' && job.dig('metadata', 'namespace') == NAMESPACE &&
                job.dig('metadata', 'name') == JOB && job.dig('spec', 'backoffLimit') == 0 &&
                job.dig('spec', 'activeDeadlineSeconds').to_i.positive? &&
                pod['automountServiceAccountToken'] == false &&
                volume && volume.dig('configMap', 'items')&.any? { |item| item['key'] == key } &&
                secret_refs['RABBITMQADMIN_USERNAME'] == { 'name' => 'clouddsp-rabbitmq-credentials', 'key' => 'RABBITMQ_DEFAULT_USER' } &&
                secret_refs['RABBITMQADMIN_PASSWORD'] == { 'name' => 'clouddsp-rabbitmq-credentials', 'key' => 'RABBITMQ_DEFAULT_PASS' } &&
                secret_refs['MINIO_EVENTS_USERNAME'] == { 'name' => MINIO_SECRET, 'key' => 'RABBITMQ_MINIO_EVENTS_USERNAME' } &&
                secret_refs['MINIO_EVENTS_PASSWORD'] == { 'name' => MINIO_SECRET, 'key' => 'RABBITMQ_MINIO_EVENTS_PASSWORD' } &&
                secret_refs['UPLOAD_INTAKE_USERNAME'] == { 'name' => TEMP_SECRET, 'key' => 'RABBITMQ_UPLOAD_INTAKE_USERNAME' } &&
                secret_refs['UPLOAD_INTAKE_PASSWORD'] == { 'name' => TEMP_SECRET, 'key' => 'RABBITMQ_UPLOAD_INTAKE_PASSWORD' } &&
                script.scan('rabbitmqadmin users declare').length == 2 &&
                script.scan('rabbitmqadmin permissions declare').length == 2 &&
                permission_flags.all? { |flag| script.scan(Regexp.new(Regexp.escape(flag))).length == 1 },
                'source-intake bootstrap Job safety contract changed')
    Stage.new(version: 'source-v001', config_name: CONFIG, job_name: JOB,
              config_path: config_path, job_path: job_path,
              config: config, job: job, definition: definition)
  end

  private

  def read_state(stage)
    topology = assess(stage, snapshot)
    users = broker_json('list_users')
    present = 0
    EXPECTED_PERMISSIONS.each do |name, expected|
      user = users.find { |item| item['user'] == name }
      next unless user

      present += 1
      ensure_true(user['tags'] == [], "#{name} broker tags drifted")
      permissions = broker_json('list_user_permissions', name)
      ensure_true(permissions == [expected], "#{name} broker permissions drifted")
    end
    return :absent if topology == :absent && present.zero?
    return :ready if topology == :ready && present == EXPECTED_PERMISSIONS.length

    raise 'source-intake topology and users are partially present; inspect before retrying'
  end

  def load_credentials
    minio = local_secret('minio-source-intake-rabbitmq-credentials.secret.yaml', MINIO_SECRET,
                         'clouddsp-data', %w[RABBITMQ_MINIO_EVENTS_USERNAME RABBITMQ_MINIO_EVENTS_PASSWORD MINIO_NOTIFY_AMQP_URL_INTAKE])
    upload = local_secret('upload-intake-rabbitmq-credentials.secret.yaml', UPLOAD_SECRET,
                          'clouddsp-app', %w[RABBITMQ_UPLOAD_INTAKE_USERNAME RABBITMQ_UPLOAD_INTAKE_PASSWORD])
    bootstrap = local_secret('upload-intake-rabbitmq-bootstrap-credentials.secret.yaml', TEMP_SECRET,
                             NAMESPACE, %w[RABBITMQ_UPLOAD_INTAKE_USERNAME RABBITMQ_UPLOAD_INTAKE_PASSWORD])
    ensure_true(upload == bootstrap, 'ignored upload-intake runtime/bootstrap credentials differ')
    ensure_true(minio['RABBITMQ_MINIO_EVENTS_USERNAME'] == MINIO_USER &&
                upload['RABBITMQ_UPLOAD_INTAKE_USERNAME'] == UPLOAD_USER,
                'ignored RabbitMQ source-intake username changed')
    verify_minio_uri(minio)
    ensure_live_secret_matches(NAMESPACE, MINIO_SECRET, minio)
    ensure_live_secret_matches('clouddsp-app', UPLOAD_SECRET, upload)
    { MINIO_USER => minio.fetch('RABBITMQ_MINIO_EVENTS_PASSWORD'),
      UPLOAD_USER => upload.fetch('RABBITMQ_UPLOAD_INTAKE_PASSWORD') }
  end

  def local_secret(filename, name, namespace, keys)
    path = @local_directory.join(filename)
    ensure_true(path.file?, "ignored local #{name} Secret file is absent")
    document = YAML.load_file(path)
    values = document.fetch('stringData')
    ensure_true(document['kind'] == 'Secret' && document.dig('metadata', 'name') == name &&
                document.dig('metadata', 'namespace') == namespace && document['type'] == 'Opaque' &&
                values.keys.sort == keys.sort && values.values.all? { |value|
                  value.is_a?(String) && !value.empty? && !value.start_with?('REPLACE_') && !value.include?("\n")
                }, "ignored local #{name} Secret contract is invalid")
    values
  rescue Psych::Exception, KeyError
    raise "ignored local #{name} Secret contract is invalid"
  end

  def verify_minio_uri(values)
    uri = URI.parse(values.fetch('MINIO_NOTIFY_AMQP_URL_INTAKE'))
    ensure_true(uri.scheme == 'amqp' && uri.host == 'clouddsp-rabbitmq.clouddsp-data.svc' &&
                uri.port == 5672 && uri.path == '/%2Fclouddsp' && uri.query.nil? && uri.fragment.nil? &&
                URI::DEFAULT_PARSER.unescape(uri.user.to_s) == MINIO_USER &&
                URI::DEFAULT_PARSER.unescape(uri.password.to_s) == values.fetch('RABBITMQ_MINIO_EVENTS_PASSWORD'),
                'ignored MinIO AMQP URL disagrees with its RabbitMQ credentials')
  rescue URI::InvalidURIError
    raise 'ignored MinIO AMQP URL is invalid'
  end

  def ensure_live_secret_matches(namespace, name, values)
    output = @runner.call('kubectl', '--context', CONTEXT, '--namespace', namespace,
                          'get', "secret/#{name}", '--output=json')
    data = JSON.parse(output).fetch('data')
    ensure_true(data.keys.sort == values.keys.sort &&
                data.all? { |key, encoded| Base64.strict_decode64(encoded) == values.fetch(key) },
                "live #{name} Secret differs from ignored local source")
  rescue JSON::ParserError, KeyError, ArgumentError
    raise "live #{name} Secret has invalid encoded data"
  end

  def authenticate_credentials(credentials)
    credentials.each do |name, password|
      ensure_true(@authenticator.call(name, password), "#{name} RabbitMQ authentication failed")
    end
  end

  def authenticate(username, password)
    _stdout, _stderr, status = Open3.capture3(
      'kubectl', '--context', CONTEXT, '--namespace', NAMESPACE,
      'exec', '-i', POD, '--', 'sh', '-ec', AUTH_SCRIPT,
      stdin_data: "#{username}\n#{password}\n"
    )
    status.success?
  end

  def create_bootstrap(stage)
    path = @local_directory.join('upload-intake-rabbitmq-bootstrap-credentials.secret.yaml')
    kubectl('create', '--dry-run=server', '--filename', path.to_s)
    kubectl('create', '--dry-run=server', '--filename', stage.job_path.to_s)
    kubectl('create', '--filename', path.to_s)
    install(stage)
  rescue StandardError => error
    raise "source-intake bootstrap: #{error.message}; inspect the fixed-name Job and temporary Secret"
  end
end

RabbitmqSourceIntakeBootstrap.new.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
