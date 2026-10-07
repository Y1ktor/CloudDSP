#!/usr/bin/env ruby
# Apply and verify the score-only IAM permission and bucket notification with
# versioned one-shot Jobs. The existing audio policy/rule remain independent.
require_relative 'minio-state-verify'

class MinioScoreUploadStage < MinioStateVerify
  POLICY_CONFIG = 'clouddsp-job-api-score-uploads-policy-v001'.freeze
  POLICY_JOB = 'minio-job-api-score-uploads-bootstrap-v001'.freeze
  NOTIFICATION_JOB = 'minio-score-notification-bootstrap-v001'.freeze
  SCORE_ROOT = SOURCE.join('score').freeze

  def run(mode)
    ensure_true(%w[plan verify reconcile].include?(mode), 'use plan, verify, or reconcile')
    credentials = root_credentials
    validate_sources
    verify_audio_prerequisites(credentials)
    policy_ready = score_policy_state(credentials)
    notification_ready = score_notification_state(credentials)
    if mode == 'plan'
      @output.puts "Score MinIO policy: #{policy_ready ? 'ready' : 'pending'}; notification: #{notification_ready ? 'ready' : 'pending'}"
      return 0
    end
    if mode == 'verify'
      ensure_true(policy_ready && notification_ready, 'score MinIO upload boundary is incomplete')
      @output.puts 'Score MinIO IAM and notification verified'
      return 0
    end

    unless policy_ready
      create_versioned_config(SCORE_POLICY_SOURCE)
      create_job(SCORE_ROOT.join("#{POLICY_JOB}-job.yaml"), POLICY_JOB)
      ensure_true(score_policy_state(credentials), 'score IAM Job completed without exact policy')
    end
    unless notification_ready
      ensure_score_target_ready
      create_job(SCORE_ROOT.join("#{NOTIFICATION_JOB}-job.yaml"), NOTIFICATION_JOB)
      ensure_true(score_notification_state(credentials), 'score notification Job completed without exact rule')
    end
    @output.puts 'Score MinIO IAM and notification applied and verified'
    0
  rescue StandardError => error
    @error.puts "Score MinIO #{mode} stopped: #{safe_error(error)}"
    1
  end

  private

  def validate_sources
    config = YAML.load_file(SCORE_POLICY_SOURCE.to_s)
    policy = JSON.parse(config.fetch('data').fetch('score-uploads-policy.json'))
    ensure_true(config['immutable'] == true && config.dig('metadata', 'name') == POLICY_CONFIG &&
                config.dig('metadata', 'namespace') == DATA_NAMESPACE &&
                policy.fetch('Statement') == [{ 'Sid' => 'WriteOnlyScoreInputs', 'Effect' => 'Allow',
                                                 'Action' => 's3:PutObject',
                                                 'Resource' => "arn:aws:s3:::#{UPLOAD_BUCKET}/score-inputs/*" }],
                'score IAM policy source changed')
    [POLICY_JOB, NOTIFICATION_JOB].each do |name|
      job = YAML.load_file(SCORE_ROOT.join("#{name}-job.yaml").to_s)
      ensure_true(job['kind'] == 'Job' && job.dig('metadata', 'namespace') == DATA_NAMESPACE &&
                  job.dig('metadata', 'name') == name && job.dig('spec', 'backoffLimit') == 0 &&
                  job.dig('spec', 'template', 'spec', 'automountServiceAccountToken') == false,
                  "#{name} source contract changed")
    end
  end

  def verify_audio_prerequisites(credentials)
    source = load_source
    verify_configmaps(source)
    verify_bucket_boundaries(credentials)
    # The original restricted Job API user must exist before attaching the
    # score policy; this read never exposes or changes its credential.
    info = mc(credentials, 'user', 'info', 'audit', 'clouddsp-job-api')
    same('Job API MinIO user status', info['userStatus'], 'enabled')
  end

  def score_policy_state(credentials)
    config = optional_resource('configmap', POLICY_CONFIG)
    response, status = mc_raw(credentials, 'policy', 'info', 'audit', SCORE_POLICY_NAME)
    if !status.success?
      parsed = JSON.parse(response)
      code = parsed.dig('error', 'cause', 'error', 'Code')
      ensure_true(code == 'XMinioAdminNoSuchPolicy', 'score IAM policy lookup failed')
      ensure_true(config.nil?, 'score IAM ConfigMap exists without durable policy; inspect bootstrap Job')
      return false
    end
    ensure_true(!config.nil?, 'score IAM policy exists without immutable ConfigMap')
    ensure_true(score_policy_active?, 'score IAM ConfigMap differs from source')
    actual = JSON.parse(response).dig('policyInfo', 'Policy')
    expected = JSON.parse(YAML.load_file(SCORE_POLICY_SOURCE.to_s).fetch('data').fetch('score-uploads-policy.json'))
    same('score IAM document', normalized_policy(actual), normalized_policy(expected))
    mapping = mc(credentials, 'policy', 'entities', '--policy', SCORE_POLICY_NAME, 'audit')
              .dig('result', 'policyMappings') || []
    same('score IAM user mapping', mapping.map { |item| item['users'] }, [['clouddsp-job-api']])
    true
  end

  def score_notification_state(credentials)
    notification = aws(credentials, 'get-bucket-notification-configuration', '--bucket', UPLOAD_BUCKET)
    queues = notification.fetch('QueueConfigurations', [])
    score = queues.find { |item| item['QueueArn'] == SCORE_NOTIFICATION_ARN }
    return false unless score

    same('score notification event family', score['Events'], ['s3:ObjectCreated:*'])
    same('score notification prefix', score['Filter'],
         { 'Key' => { 'FilterRules' => [{ 'Name' => 'prefix', 'Value' => 'score-inputs/' }] } })
    ensure_true(upload_notification_state(credentials) == :ready,
                'audio or score notification has drifted')
    true
  end

  def ensure_score_target_ready
    sts = kube(DATA_NAMESPACE, 'statefulset/clouddsp-minio')
    vars = sts.dig('spec', 'template', 'spec', 'containers', 0, 'env') || []
    target = vars.to_h { |item| [item['name'], item] }
    same('score AMQP target enabled', target.dig('MINIO_NOTIFY_AMQP_ENABLE_SCORE', 'value'), 'on')
    same('score AMQP routing key', target.dig('MINIO_NOTIFY_AMQP_ROUTING_KEY_SCORE', 'value'), 'score.upload.created')
    same('MinIO ready replicas', sts.dig('status', 'readyReplicas'), 1)
  end

  def optional_resource(kind, name)
    output, _stderr, status = @command.call('kubectl', '--context', CONTEXT, '-n', DATA_NAMESPACE,
                                              'get', "#{kind}/#{name}", '--ignore-not-found', '-o', 'json')
    ensure_true(status.success?, "#{kind}/#{name} lookup failed")
    output.strip.empty? ? nil : JSON.parse(output)
  end

  def create_versioned_config(path)
    ensure_true(optional_resource('configmap', POLICY_CONFIG).nil?, 'score policy ConfigMap appeared during preflight')
    kubectl_write('create', '--dry-run=server', '--filename', path.to_s)
    kubectl_write('create', '--filename', path.to_s)
  end

  def create_job(path, name)
    ensure_true(optional_resource('job', name).nil?, "#{name} exists without complete durable state")
    kubectl_write('create', '--dry-run=server', '--filename', path.to_s)
    kubectl_write('create', '--filename', path.to_s)
    kubectl_write('wait', '--for=condition=complete', "job/#{name}", '--timeout=240s')
  end

  def kubectl_write(*args)
    _output, _stderr, status = @command.call('kubectl', '--context', CONTEXT, '-n', DATA_NAMESPACE, *args)
    ensure_true(status.success?, "score MinIO #{args.first} failed; inspect the versioned resource")
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: minio-score-upload-stage.rb plan|verify|reconcile' unless ARGV.length == 1
  exit MinioScoreUploadStage.new.run(ARGV.first)
end
