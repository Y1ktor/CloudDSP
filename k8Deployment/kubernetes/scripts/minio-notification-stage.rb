#!/usr/bin/env ruby
# Restore only a wholly absent MinIO source-upload notification on an existing
# local cluster. The versioned Job performs the one write; MinIO's S3 metadata
# is the durable completion evidence after Kubernetes TTL deletes that Job.
# A different or additional notification is drift and requires review.
require 'rbconfig'
require_relative 'minio-state-verify'

class MinioNotificationStage < MinioStateVerify
  JOB = 'minio-source-intake-notification-bootstrap'.freeze
  JOB_PATH = SOURCE.join("#{JOB}-job.yaml").freeze
  INIT_NAMES = %w[
    configure-minio-root-alias
    wait-for-minio-service
    add-source-upload-notification
    verify-source-upload-notification
    remove-root-alias-from-temporary-config
  ].freeze

  def run(mode)
    ensure_true(%w[plan verify reconcile].include?(mode), 'use plan, verify, or reconcile')
    source_policies = load_source
    validate_source_job
    credentials = root_credentials
    verify_configmaps(source_policies)
    verify_runtime_keys
    verify_bucket_boundaries(credentials)
    verify_iam(credentials, source_policies)
    state = upload_notification_state(credentials)

    if state == :ready
      @output.puts "MinIO notification #{mode}: exact source-upload rule verified; no work pending"
      return 0
    end

    # A fixed-name Job could be still running, failed, or left by a previous
    # attempt. None proves a successful durable notification, so never delete
    # or rerun it automatically. Preserve it for diagnosis.
    ensure_true(job_name.empty?, 'source-upload notification is absent but bootstrap Job exists')
    @output.puts 'MinIO notification: source-upload rule absent; versioned Job pending'
    return 0 if mode == 'plan'

    ensure_true(mode == 'reconcile', 'source-upload notification bootstrap is incomplete')
    verify_prerequisites
    ensure_true(upload_notification_state(credentials) == :absent,
                'source-upload notification changed during preflight')
    ensure_true(job_name.empty?, 'source-upload notification bootstrap Job appeared during preflight')
    kubectl_write('create', '--dry-run=server', '--filename', JOB_PATH.to_s)
    kubectl_write('create', '--filename', JOB_PATH.to_s)
    kubectl_write('wait', '--for=condition=complete', "job/#{JOB}", '--timeout=210s')
    ensure_true(upload_notification_state(credentials) == :ready,
                'bootstrap Job completed without exact source-upload notification')
    @output.puts 'MinIO notification reconcile: versioned Job completed and durable rule verified'
    0
  rescue StandardError => exception
    # Kubectl, mc, and S3 output may include private endpoint or credential
    # details. Report only the fixed field or command label from this runner.
    @error.puts "MinIO notification #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def job_name
    output, _stderr, status = @command.call('kubectl', '--context', CONTEXT, '-n', DATA_NAMESPACE,
                                              'get', "job/#{JOB}", '--ignore-not-found', '-o', 'name')
    ensure_true(status.success?, 'source-upload notification bootstrap Job lookup failed')
    output.strip
  end

  def validate_source_job
    job = YAML.load_file(JOB_PATH.to_s)
    pod = job.dig('spec', 'template', 'spec')
    init = pod.fetch('initContainers')
    main = pod.fetch('containers')
    same('source notification Job identity',
         [job['kind'], job.dig('metadata', 'namespace'), job.dig('metadata', 'name')],
         ['Job', DATA_NAMESPACE, JOB])
    same('source notification Job limits',
         [job.dig('spec', 'backoffLimit'), job.dig('spec', 'activeDeadlineSeconds'),
          job.dig('spec', 'ttlSecondsAfterFinished')], [0, 180, 600])
    same('source notification Pod boundary',
         [pod['automountServiceAccountToken'], pod['restartPolicy'], pod['hostNetwork']],
         [false, 'Never', nil])
    same('source notification Pod identity', pod.fetch('securityContext').slice(
      'runAsNonRoot', 'runAsUser', 'runAsGroup', 'fsGroup', 'seccompProfile'
    ), { 'runAsNonRoot' => true, 'runAsUser' => 1000, 'runAsGroup' => 1000,
         'fsGroup' => 1000, 'seccompProfile' => { 'type' => 'RuntimeDefault' } })
    same('source notification init sequence', init.map { |container| container['name'] }, INIT_NAMES)
    same('source notification completion container', main.map { |container| container['name'] },
         ['completion-marker'])
    expected_args = [
      ['alias', 'set', 'minio-admin', 'http://clouddsp-minio:9000', '$(MINIO_ROOT_USER)', '$(MINIO_ROOT_PASSWORD)'],
      ['ready', 'minio-admin'],
      ['event', 'add', '--event', 'put', '--prefix', 'uploads/', '--ignore-existing',
       "minio-admin/#{UPLOAD_BUCKET}", NOTIFICATION_ARN],
      ['event', 'list', "minio-admin/#{UPLOAD_BUCKET}", NOTIFICATION_ARN],
      ['alias', 'remove', 'minio-admin']
    ]
    same('source notification init commands', init.map { |container| container['args'] }, expected_args)
    same('source notification completion command', main.first['args'], ['--version'])
    (init + main).each do |container|
      ensure_true(container['image'] == MC_IMAGE && container['imagePullPolicy'] == 'IfNotPresent' &&
                  !container.key?('command') &&
                  container.dig('securityContext', 'readOnlyRootFilesystem') == true &&
                  container.dig('securityContext', 'allowPrivilegeEscalation') == false &&
                  container.dig('securityContext', 'capabilities', 'drop') == ['ALL'],
                  "source notification #{container['name']} security contract changed")
    end
    root_refs = init.first.fetch('env').to_h { |item| [item['name'], item.dig('valueFrom', 'secretKeyRef')] }
    same('source notification root Secret references', root_refs,
         { 'MC_CONFIG_DIR' => nil,
           'MINIO_ROOT_USER' => { 'name' => 'clouddsp-minio-root-credentials', 'key' => 'MINIO_ROOT_USER' },
           'MINIO_ROOT_PASSWORD' => { 'name' => 'clouddsp-minio-root-credentials', 'key' => 'MINIO_ROOT_PASSWORD' } })
    same('source notification root config path', init.first.fetch('env').first,
         { 'name' => 'MC_CONFIG_DIR', 'value' => '/mc-config' })
    init.drop(1).each do |container|
      same("source notification #{container['name']} environment", container['env'],
           [{ 'name' => 'MC_CONFIG_DIR', 'value' => '/mc-config' }])
    end
    same('source notification config volume', pod.fetch('volumes').map { |volume| volume['name'] }, ['mc-config'])
    same('source notification config lifetime', pod.fetch('volumes').first['emptyDir'],
         { 'sizeLimit' => '1Mi' })
    init.each do |container|
      same("source notification #{container['name']} config mount", container['volumeMounts'],
           [{ 'name' => 'mc-config', 'mountPath' => '/mc-config' }])
    end
  end

  def verify_prerequisites
    %w[minio-release.rb rabbitmq-source-intake-bootstrap.rb].each do |script|
      _output, _stderr, status = @command.call(RbConfig.ruby, ROOT.join('scripts', script).to_s, 'verify')
      ensure_true(status.success?, "#{script} prerequisite verification failed")
    end
  end

  def kubectl_write(*arguments)
    _output, _stderr, status = @command.call('kubectl', '--context', CONTEXT, '-n', DATA_NAMESPACE, *arguments)
    ensure_true(status.success?, "notification Job #{arguments.first} failed; inspect the fixed-name Job")
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: minio-notification-stage.rb plan|verify|reconcile' unless ARGV.length == 1
  exit MinioNotificationStage.new.run(ARGV.first)
end
