#!/usr/bin/env ruby
# Verify durable MinIO state left outside its Helm release: two bucket
# boundaries, the source-intake notification, six IAM policy documents, and
# their five restricted application identities. No object is uploaded or read.
#
  # The host AWS CLI reads S3 metadata through the existing local Ingress. The
  # pinned mc image is mirrored from Docker Hub into the local registry before
  # the first transient run inside the k3d server node. Its credentials arrive
  # over stdin, its config lives on tmpfs, and `ctr run --rm` removes the client
  # after each query. No root credential enters a command argument, file,
  # Kubernetes Job, or normal output.

require_relative '../../lib/paths'
require 'base64'
require 'cgi'
require 'json'
require 'open3'
require 'pathname'
require 'securerandom'
require 'yaml'

class MinioStateVerify
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  SOURCE = ROOT.join('services', 'minio').freeze
  CONTEXT = 'k3d-clouddsp-local'.freeze
  DATA_NAMESPACE = 'clouddsp-data'.freeze
  APP_NAMESPACE = 'clouddsp-app'.freeze
  NODE = 'k3d-clouddsp-local-server-0'.freeze
  ENDPOINT = 'http://minio.localhost:8080'.freeze
  UPLOAD_BUCKET = 'clouddsp-uploads'.freeze
  SAMPLE_BUCKET = 'clouddsp-midi-samples'.freeze
  NOTIFICATION_ARN = 'arn:minio:sqs::INTAKE:amqp'.freeze
  MC_IMAGE = 'clouddsp-registry.localhost:5001/minio-mc@sha256:37d109dddbbb2c95873f5fc81ac93f37023264770fc580a7564148892087b1b7'.freeze
  # k3s rewrites the public-facing :5001 registry address to the registry
  # container's :5000 port for kubelet pulls. Direct `ctr` calls bypass that
  # rewrite, so this verifier uses the node-internal HTTP endpoint explicitly.
  MC_NODE_IMAGE = MC_IMAGE.sub('clouddsp-registry.localhost:5001/',
                               'clouddsp-registry.localhost:5000/').freeze
  # Policy names are fixed, versioned MinIO IAM identities. An application
  # user may have several policies, but each policy belongs to one user only.
  POLICY_USERS = {
    'clouddsp-job-api-uploads-v002' => 'clouddsp-job-api',
    'clouddsp-job-api-artifact-read-v003' => 'clouddsp-job-api',
    'clouddsp-upload-intake-source-read-v001' => 'clouddsp-upload-intake',
    'clouddsp-demucs-artifacts-v001' => 'clouddsp-demucs',
    'clouddsp-basic-pitch-artifacts-v001' => 'clouddsp-basic-pitch',
    'clouddsp-adtof-artifacts-v001' => 'clouddsp-adtof'
  }.freeze
  RUNTIME_KEYS = {
    'clouddsp-job-api' => ['clouddsp-job-api-minio-credentials', 'JOB_API_S3_ACCESS_KEY'],
    'clouddsp-upload-intake' => ['clouddsp-upload-intake-minio-credentials', 'UPLOAD_INTAKE_S3_ACCESS_KEY'],
    'clouddsp-demucs' => ['clouddsp-demucs-minio-credentials', 'DEMUCS_S3_ACCESS_KEY'],
    'clouddsp-basic-pitch' => ['clouddsp-basic-pitch-minio-credentials', 'BASIC_PITCH_S3_ACCESS_KEY'],
    'clouddsp-adtof' => ['clouddsp-adtof-minio-credentials', 'ADTOF_S3_ACCESS_KEY']
  }.freeze

  def initialize(command: Open3.method(:capture3), output: $stdout, error: $stderr)
    @command = command
    @output = output
    @error = error
    @mc_image_ready = false
  end

  def run
    source_policies = load_source
    credentials = root_credentials
    verify_configmaps(source_policies)
    verify_runtime_keys
    verify_buckets(credentials)
    verify_iam(credentials, source_policies)
    @output.puts 'MinIO buckets, public boundary, six IAM policies, five users, and source notification verified.'
    0
  rescue StandardError => exception
    # External command bodies and Admin API responses may contain credential
    # material. Only fixed field labels from this verifier may reach stderr.
    @error.puts "MinIO state verify stopped: #{safe_error(exception)}"
    1
  end

  def load_source
    lock = YAML.load_file(ROOT.join('images.lock.yaml').to_s)
    same('pinned MinIO client image', lock.dig('images', 'minio-mc', 'immutableReference'), MC_IMAGE)
    files = Dir.glob(SOURCE.join('*policy-v*-configmap.yaml').to_s).sort
    policies = files.to_h do |path|
      document = YAML.load_file(path)
      name = document.dig('metadata', 'name')
      same("#{File.basename(path)} namespace", document.dig('metadata', 'namespace'), DATA_NAMESPACE)
      same("#{File.basename(path)} immutable", document['immutable'], true)
      ensure_true(name.is_a?(String) && name.end_with?('-policy-v001', '-policy-v002', '-policy-v003'),
                  "#{File.basename(path)} ConfigMap name changed")
      data = document.fetch('data')
      ensure_true(data.is_a?(Hash) && data.length == 1, "#{name} policy data shape changed")
      policy_name = name.sub(/-policy-v(\d+)\z/, '-v\\1')
      [policy_name, { name: name, data: data, policy: JSON.parse(data.values.fetch(0)) }]
    end
    same('source MinIO policy set', policies.keys.sort, POLICY_USERS.keys.sort)

    upload_job = YAML.load_file(SOURCE.join('minio-job-api-uploads-bootstrap-job.yaml').to_s)
    upload_args = upload_job.dig('spec', 'template', 'spec', 'initContainers').flat_map { |container| container['args'] || [] }
    ensure_true(upload_args.include?("minio-admin/#{UPLOAD_BUCKET}"), 'source private upload bucket changed')
    notification_job = YAML.load_file(SOURCE.join('minio-source-intake-notification-bootstrap-job.yaml').to_s)
    same('source notification namespace', notification_job.dig('metadata', 'namespace'), DATA_NAMESPACE)
    notification_container = notification_job.dig('spec', 'template', 'spec', 'initContainers')
                                             .find { |container| container['name'] == 'add-source-upload-notification' }
    same('source MinIO notification command', notification_container&.fetch('args', nil),
         ['event', 'add', '--event', 'put', '--prefix', 'uploads/', '--ignore-existing',
          "minio-admin/#{UPLOAD_BUCKET}", NOTIFICATION_ARN])
    sample_source = SOURCE.join('midi_sample_mirror.py').read
    ensure_true(sample_source.include?("BUCKET = \"#{SAMPLE_BUCKET}\""),
                'source shared sample bucket name changed')
    # The shared-sample policy is published by Python rather than a manifest.
    # Pin the whole small publisher body so an added permission or second S3
    # write cannot pass just because the old GetObject text still exists.
    expected_publisher = <<~'PYTHON'
      def publish_read_only_policy(env: dict[str, str], directory: Path) -> None:
          policy = {
              "Version": "2012-10-17",
              "Statement": [{
                  "Sid": "BrowserReadSharedMidiSamplesOnly",
                  "Effect": "Allow",
                  "Principal": "*",
                  "Action": "s3:GetObject",
                  "Resource": f"arn:aws:s3:::{BUCKET}/*",
              }],
          }
          policy_file = directory / "public-read-only-policy.json"
          policy_file.write_text(json.dumps(policy, sort_keys=True))
          aws(env, "s3api", "put-bucket-policy", "--bucket", BUCKET, "--policy", f"file://{policy_file}")
    PYTHON
    publisher = sample_source[/^def publish_read_only_policy.*?(?=^def main\()/m]
    same('source shared sample policy publisher', publisher&.strip, expected_publisher.strip)
    policies
  end

  private

  def kube(namespace, resource)
    output, _stderr, status = @command.call('kubectl', '--context', CONTEXT, '-n', namespace,
                                              'get', resource, '-o', 'json')
    ensure_true(status.success?, "Kubernetes #{namespace}/#{resource} lookup failed")
    JSON.parse(output)
  end

  def root_credentials
    values = kube(DATA_NAMESPACE, 'secret/clouddsp-minio-root-credentials').fetch('data')
    user = Base64.strict_decode64(values.fetch('MINIO_ROOT_USER'))
    password = Base64.strict_decode64(values.fetch('MINIO_ROOT_PASSWORD'))
    ensure_true([user, password].all? { |value| !value.empty? && !value.include?("\n") },
                'MinIO root Secret is empty or invalid')
    ip = kube(DATA_NAMESPACE, 'service/clouddsp-minio').dig('spec', 'clusterIP')
    ensure_true(ip.is_a?(String) && ip.match?(/\A\d{1,3}(?:\.\d{1,3}){3}\z/),
                'MinIO Service ClusterIP is invalid')
    { user: user, password: password, ip: ip }
  end

  def verify_configmaps(source_policies)
    source_policies.each_value do |expected|
      actual = kube(DATA_NAMESPACE, "configmap/#{expected.fetch(:name)}")
      same("#{expected.fetch(:name)} immutable", actual['immutable'], true)
      same("#{expected.fetch(:name)} source data", actual['data'], expected.fetch(:data))
    end
  end

  def verify_runtime_keys
    RUNTIME_KEYS.each do |user, (secret_name, key)|
      data = kube(APP_NAMESPACE, "secret/#{secret_name}").fetch('data')
      same("#{secret_name} access key", Base64.strict_decode64(data.fetch(key)), user)
      ensure_true(data.keys.any? { |name| name.end_with?('SECRET_KEY') },
                  "#{secret_name} has no secret key field")
    end
  end

  def aws(credentials, *arguments, missing_policy_ok: false)
    environment = ENV.to_h.merge(
      'AWS_ACCESS_KEY_ID' => credentials.fetch(:user),
      'AWS_SECRET_ACCESS_KEY' => credentials.fetch(:password),
      'AWS_DEFAULT_REGION' => 'us-east-1',
      'AWS_EC2_METADATA_DISABLED' => 'true',
      'AWS_PAGER' => '',
      'AWS_MAX_ATTEMPTS' => '3'
    )
    environment.delete('AWS_SESSION_TOKEN')
    output, stderr, status = @command.call(environment, 'aws', '--no-cli-pager',
                                             '--endpoint-url', ENDPOINT, 's3api', *arguments, '--output', 'json')
    return nil if missing_policy_ok && !status.success? && stderr.include?('NoSuchBucketPolicy')

    ensure_true(status.success?, "S3 #{arguments.first} failed")
    output.strip.empty? ? {} : JSON.parse(output)
  end

  def verify_buckets(credentials)
    verify_bucket_boundaries(credentials)
    same('private upload notification state', upload_notification_state(credentials), :ready)
  end

  def verify_bucket_boundaries(credentials)
    names = aws(credentials, 'list-buckets').fetch('Buckets').map { |item| item.fetch('Name') }.sort
    same('MinIO bucket set', names, [UPLOAD_BUCKET, SAMPLE_BUCKET].sort)
    same('private uploads bucket policy',
         aws(credentials, 'get-bucket-policy', '--bucket', UPLOAD_BUCKET, missing_policy_ok: true), nil)
    sample = aws(credentials, 'get-bucket-policy', '--bucket', SAMPLE_BUCKET)
    same('anonymous shared-sample policy', normalized_policy(JSON.parse(sample.fetch('Policy'))),
         normalized_policy(sample_policy))
    same('shared-sample notifications',
         aws(credentials, 'get-bucket-notification-configuration', '--bucket', SAMPLE_BUCKET), {})
  end

  def sample_policy
    {
      'Version' => '2012-10-17',
      'Statement' => [{ 'Sid' => 'BrowserReadSharedMidiSamplesOnly', 'Effect' => 'Allow',
                        'Principal' => '*', 'Action' => 's3:GetObject',
                        'Resource' => "arn:aws:s3:::#{SAMPLE_BUCKET}/*" }]
    }
  end

  def upload_notification_state(credentials)
    notification = aws(credentials, 'get-bucket-notification-configuration', '--bucket', UPLOAD_BUCKET)
    queues = notification.fetch('QueueConfigurations', [])
    return :absent if notification.empty? || notification == { 'QueueConfigurations' => [] }

    same('private upload notification count', queues.length, 1)
    ensure_true((queues.first.keys - %w[Id QueueArn Events Filter]).empty?,
                'private upload notification has unexpected fields')
    same('private upload notification target', queues.first['QueueArn'], NOTIFICATION_ARN)
    same('private upload notification events', queues.first['Events'], ['s3:ObjectCreated:*'])
    same('private upload notification filter', queues.first['Filter'],
         { 'Key' => { 'FilterRules' => [{ 'Name' => 'prefix', 'Value' => 'uploads/' }] } })
    ensure_true((notification.keys - ['QueueConfigurations']).empty?, 'private upload has unexpected notifications')
    :ready
  end

  def mc(credentials, *arguments)
    output, status = mc_raw(credentials, *arguments)
    ensure_true(status.success?, "MinIO admin #{arguments.first(2).join(' ')} failed")
    response = JSON.parse(output)
    same('MinIO admin response status', response['status'], 'success')
    response
  end

  # Keep the same ephemeral client boundary for narrowly checked absent-IAM
  # probes. Callers receive raw JSON only in memory and may classify a known
  # NoSuchUser/NoSuchPolicy code; every other failure must stop bootstrap.
  def mc_raw(credentials, *arguments)
    ensure_mc_image
    encode = ->(value) { CGI.escape(value).tr('+', '%20') }
    host = "http://#{encode.call(credentials.fetch(:user))}:#{encode.call(credentials.fetch(:password))}@#{credentials.fetch(:ip)}:9000"
    input = "MC_HOST_audit=#{host}\nMC_CONFIG_DIR=/mc-config\n"
    command = ['docker', 'exec', '-i', NODE, 'ctr', '-n', 'k8s.io', 'run', '--rm',
               '--net-host', '--read-only', '--mount', 'type=tmpfs,dst=/mc-config,options=rw',
               '--env-file', '/dev/stdin', MC_NODE_IMAGE, "clouddsp-minio-verify-#{SecureRandom.hex(6)}",
               '/usr/bin/mc', '--json', 'admin', *arguments]
    output, _stderr, status = @command.call(*command, stdin_data: input)
    [output, status]
  end

  # `ctr run` does not pull images. Pull the digest-pinned client into the
  # server node's containerd namespace once, using the local registry that the
  # root deployment stages have already populated. This keeps admin checks
  # independent of whatever images happen to be cached on the host or node.
  def ensure_mc_image
    return if @mc_image_ready

    _output, _stderr, status = @command.call('docker', 'exec', NODE, 'ctr', '-n', 'k8s.io',
                                              'images', 'pull', '--plain-http', MC_NODE_IMAGE)
    ensure_true(status.success?, 'pinned MinIO client image pull failed')
    @mc_image_ready = true
  end

  def verify_iam(credentials, source_policies)
    source_policies.each do |name, expected|
      actual = mc(credentials, 'policy', 'info', 'audit', name).dig('policyInfo', 'Policy')
      same("#{name} IAM document", normalized_policy(actual), normalized_policy(expected.fetch(:policy)))
      mappings = mc(credentials, 'policy', 'entities', '--policy', name, 'audit').dig('result', 'policyMappings') || []
      same("#{name} entity count", mappings.length, 1)
      same("#{name} mapped policy", mappings.first['policy'], name)
      same("#{name} user", mappings.first['users'], [POLICY_USERS.fetch(name)])
      ensure_true(Array(mappings.first['groups']).empty?, "#{name} has an unexpected group")
    end
    RUNTIME_KEYS.each_key do |user|
      expected_names = POLICY_USERS.select { |_name, owner| owner == user }.keys.sort
      info = mc(credentials, 'user', 'info', 'audit', user)
      same("#{user} enabled", info['userStatus'], 'enabled')
      same("#{user} policy names", info['policyName'].to_s.split(',').sort, expected_names)
      mappings = mc(credentials, 'policy', 'entities', '--user', user, 'audit').dig('result', 'userMappings') || []
      same("#{user} entity count", mappings.length, 1)
      same("#{user} mapped user", mappings.first['user'], user)
      same("#{user} entity policy names", Array(mappings.first['policies']).sort, expected_names)
    end
  end

  def normalized_policy(value, field = nil)
    case value
    when Hash
      value.keys.sort.to_h { |key| [key, normalized_policy(value.fetch(key), key)] }
    when Array
      # The field's scalar-to-array conversion happens at the parent. Passing
      # it into each array item would repeatedly wrap the same scalar.
      value.map { |item| normalized_policy(item) }.sort_by { |item| JSON.generate(item) }
    else
      return { 'AWS' => [value] } if field == 'Principal'
      return [value] if %w[Action Resource AWS].include?(field)

      value
    end
  end

  def same(label, actual, expected)
    ensure_true(actual == expected, "#{label} drifted")
  end

  def ensure_true(condition, message)
    raise message unless condition
  end

  def safe_error(exception)
    exception.instance_of?(RuntimeError) ? exception.message : exception.class.to_s
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: minio-state-verify.rb verify' unless ARGV == ['verify']
  exit MinioStateVerify.new.run
end
