#!/usr/bin/env ruby
# Bootstrap only the Job API's restricted MinIO user and its two versioned
# policies. Kubernetes Jobs carry root credentials only inside clouddsp-data;
# the API receives its separate runtime Secret in clouddsp-app. The temporary
# provisioning Secret is removed only after MinIO's durable IAM state matches
# both reviewed policy documents. A partial Job or IAM state stops for review.
require 'rbconfig'
require_relative 'minio-state-verify'

class MinioJobApiIamStage < MinioStateVerify
  USER = 'clouddsp-job-api'.freeze
  TEMP_SECRET = 'clouddsp-job-api-minio-bootstrap-credentials'.freeze
  TEMP_SOURCE = ROOT.parent.join('.local', 'job-api-minio-bootstrap-credentials.secret.yaml').freeze
  POLICIES = %w[clouddsp-job-api-uploads-v002 clouddsp-job-api-artifact-read-v003].freeze
  JOBS = %w[minio-job-api-uploads-bootstrap minio-job-api-artifact-read-bootstrap].freeze
  PREREQUISITES = [
    ['minio-release.rb', 'verify'],
    ['job-api-minio-secret-stage.rb', 'verify'],
    ['minio-fresh-samples-stage.rb', 'verify']
  ].freeze
  EXPECTED_ARGS = [
    [
      %w[alias set minio-admin http://clouddsp-minio:9000 $(MINIO_ROOT_USER) $(MINIO_ROOT_PASSWORD)],
      %w[ready minio-admin],
      %w[mb --ignore-existing minio-admin/clouddsp-uploads],
      %w[anonymous set none minio-admin/clouddsp-uploads],
      %w[admin policy create minio-admin clouddsp-job-api-uploads-v002 /policy/job-api-uploads-policy.json],
      %w[admin user add minio-admin $(JOB_API_S3_ACCESS_KEY) $(JOB_API_S3_SECRET_KEY)],
      %w[admin policy attach minio-admin clouddsp-job-api-uploads-v002 --user=$(JOB_API_S3_ACCESS_KEY)],
      %w[admin user info minio-admin $(JOB_API_S3_ACCESS_KEY)],
      %w[alias remove minio-admin]
    ],
    [
      %w[alias set minio-admin http://clouddsp-minio:9000 $(MINIO_ROOT_USER) $(MINIO_ROOT_PASSWORD)],
      %w[ready minio-admin],
      %w[admin policy create minio-admin clouddsp-job-api-artifact-read-v003 /policy/job-api-artifact-read-policy.json],
      %w[admin policy attach minio-admin clouddsp-job-api-artifact-read-v003 --user=$(JOB_API_S3_ACCESS_KEY)],
      %w[admin user info minio-admin $(JOB_API_S3_ACCESS_KEY)],
      %w[alias remove minio-admin]
    ]
  ].freeze

  def run(mode)
    ensure_true(%w[plan bootstrap verify reconcile].include?(mode), 'use plan, bootstrap, verify, or reconcile')
    source = load_source
    policies = POLICIES.to_h { |name| [name, source.fetch(name)] }
    validate_jobs(policies)
    verify_prerequisites
    credentials = root_credentials
    if mode == 'reconcile'
      verify_configmaps(policies)
      verify_jobs_if_present
      verify_iam_subset(credentials, policies)
      temporary = resource('secret', TEMP_SECRET)
      if temporary
        verify_temporary_secret(temporary)
        kubectl_write('delete', "secret/#{TEMP_SECRET}", '--wait=true')
        ensure_absent('secret', TEMP_SECRET)
        @output.puts 'Job API MinIO IAM reconcile: verified complete IAM and removed matching temporary Secret'
      else
        @output.puts 'Job API MinIO IAM reconcile: exact IAM verified; no temporary Secret remains'
      end
      return 0
    end
    if mode == 'verify'
      ensure_absent('secret', TEMP_SECRET)
      verify_configmaps(policies)
      verify_jobs_if_present
      verify_iam_subset(credentials, policies)
      @output.puts 'Job API MinIO IAM: two exact policies and restricted user verified; temporary Secret absent'
      return 0
    end

    ensure_fresh_kubernetes_state(policies)
    ensure_fresh_iam_state(credentials)
    if mode == 'plan'
      @output.puts 'Job API MinIO IAM plan: absent user and policies; two versioned Jobs pending'
      return 0
    end

    # Validate every manifest with the API server before the first write.
    # Suppress command output because the ignored Secret contains a key.
    ([TEMP_SOURCE] + policies.values.map { |item| config_path(item) } + JOBS.map { |name| job_path(name) }).each do |path|
      kubectl_write('create', '--dry-run=server', '--filename', path.to_s)
    end
    ensure_fresh_kubernetes_state(policies)
    ensure_fresh_iam_state(credentials)
    kubectl_write('create', '--filename', TEMP_SOURCE.to_s)
    verify_temporary_secret(resource('secret', TEMP_SECRET))
    POLICIES.zip(JOBS).each_with_index do |(name, job), index|
      kubectl_write('create', '--filename', config_path(policies.fetch(name)).to_s)
      kubectl_write('create', '--filename', job_path(job).to_s)
      kubectl_write('wait', '--for=condition=complete', "job/#{job}", '--timeout=210s')
      verify_policy(credentials, name, policies.fetch(name))
      verify_user(credentials, POLICIES.take(index + 1))
    end
    verify_configmaps(policies)
    verify_iam_subset(credentials, policies)
    kubectl_write('delete', "secret/#{TEMP_SECRET}", '--wait=true')
    ensure_absent('secret', TEMP_SECRET)
    @output.puts 'Job API MinIO IAM bootstrap: two policies and restricted user verified; temporary Secret removed'
    0
  rescue StandardError => exception
    @error.puts "Job API MinIO IAM #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def verify_prerequisites
    PREREQUISITES.each do |script, argument|
      _output, _stderr, status = @command.call(RbConfig.ruby, ROOT.join('scripts', script).to_s, argument)
      ensure_true(status.success?, "#{script} prerequisite verification failed")
    end
  end

  def config_path(policy)
    SOURCE.join("minio-#{policy.fetch(:name).delete_prefix('clouddsp-')}-configmap.yaml")
  end

  def job_path(name)
    SOURCE.join("#{name}-job.yaml")
  end

  def validate_jobs(policies)
    JOBS.each_with_index do |name, index|
      job = YAML.load_file(job_path(name).to_s)
      pod = job.dig('spec', 'template', 'spec')
      init = pod.fetch('initContainers')
      same("#{name} identity", [job['kind'], job.dig('metadata', 'namespace'), job.dig('metadata', 'name')],
           ['Job', DATA_NAMESPACE, name])
      same("#{name} retry and cleanup bounds",
           [job.dig('spec', 'backoffLimit'), job.dig('spec', 'activeDeadlineSeconds'),
            job.dig('spec', 'ttlSecondsAfterFinished')], [0, 180, 300])
      same("#{name} Pod API boundary", [pod['automountServiceAccountToken'], pod['restartPolicy']],
           [false, 'Never'])
      same("#{name} init commands", init.map { |container| container['args'] }, EXPECTED_ARGS.fetch(index))
      ensure_true((init + pod.fetch('containers')).all? do |container|
        container['image'] == MC_IMAGE && container['imagePullPolicy'] == 'IfNotPresent' &&
          container.dig('securityContext', 'readOnlyRootFilesystem') == true &&
          container.dig('securityContext', 'allowPrivilegeEscalation') == false &&
          container.dig('securityContext', 'capabilities', 'drop') == ['ALL']
      end, "#{name} image or container security changed")
      refs = init.flat_map { |container| Array(container['env']) }
                 .map { |item| [item['name'], item.dig('valueFrom', 'secretKeyRef')] if item.dig('valueFrom', 'secretKeyRef') }.compact
      expected_refs = {
        'MINIO_ROOT_USER' => { 'name' => 'clouddsp-minio-root-credentials', 'key' => 'MINIO_ROOT_USER' },
        'MINIO_ROOT_PASSWORD' => { 'name' => 'clouddsp-minio-root-credentials', 'key' => 'MINIO_ROOT_PASSWORD' },
        'JOB_API_S3_ACCESS_KEY' => { 'name' => TEMP_SECRET, 'key' => 'JOB_API_S3_ACCESS_KEY' }
      }
      expected_refs['JOB_API_S3_SECRET_KEY'] = { 'name' => TEMP_SECRET, 'key' => 'JOB_API_S3_SECRET_KEY' } if index.zero?
      same("#{name} Secret references", refs.to_h, expected_refs)
      ensure_true(refs.all? { |key, reference| expected_refs[key] == reference },
                  "#{name} has unexpected Secret references")
      config = pod.fetch('volumes').map { |volume| volume['configMap'] }.compact
      same("#{name} policy ConfigMap", config.map { |item| item['name'] }, [policies.fetch(POLICIES.fetch(index)).fetch(:name)])
    end
  end

  def resource(kind, name)
    output, _stderr, status = @command.call('kubectl', '--context', CONTEXT, '-n', DATA_NAMESPACE,
                                              'get', "#{kind}/#{name}", '--ignore-not-found', '-o', 'json')
    ensure_true(status.success?, "#{kind}/#{name} lookup failed")
    output.strip.empty? ? nil : JSON.parse(output)
  end

  def ensure_absent(kind, name)
    ensure_true(resource(kind, name).nil?, "#{kind}/#{name} already exists; inspect partial bootstrap")
  end

  def ensure_fresh_kubernetes_state(policies)
    ensure_absent('secret', TEMP_SECRET)
    policies.each_value { |policy| ensure_absent('configmap', policy.fetch(:name)) }
    JOBS.each { |name| ensure_absent('job', name) }
  end

  def verify_temporary_secret(secret)
    ensure_true(secret.is_a?(Hash) && secret['kind'] == 'Secret' &&
                secret.dig('metadata', 'name') == TEMP_SECRET &&
                secret.dig('metadata', 'namespace') == DATA_NAMESPACE &&
                secret['type'] == 'Opaque', 'temporary Job API MinIO Secret identity or type differs')
    example = YAML.safe_load(SOURCE.join('minio-job-api-uploads-bootstrap-credentials.secret.example.yaml').read)
    same('temporary Job API MinIO Secret labels', secret.dig('metadata', 'labels'), example.dig('metadata', 'labels'))
    expected = YAML.safe_load(TEMP_SOURCE.read).fetch('stringData')
    data = secret.fetch('data')
    same('temporary Job API MinIO Secret keys', data.keys.sort, expected.keys.sort)
    ensure_true(data.all? { |key, encoded| Base64.strict_decode64(encoded) == expected.fetch(key) },
                'temporary Job API MinIO Secret differs from ignored local source')
  rescue ArgumentError, KeyError, Psych::Exception
    raise 'temporary Job API MinIO Secret has invalid encoded data or source'
  end

  def verify_configmaps(policies)
    policies.each_value do |expected|
      actual = resource('configmap', expected.fetch(:name))
      ensure_true(!actual.nil?, "configmap/#{expected.fetch(:name)} is absent")
      same("#{expected.fetch(:name)} immutable", actual['immutable'], true)
      same("#{expected.fetch(:name)} policy data", actual['data'], expected.fetch(:data))
    end
  end

  def verify_jobs_if_present
    JOBS.each do |name|
      job = resource('job', name)
      next if job.nil?

      same("#{name} completion", job.dig('status', 'succeeded'), 1)
    end
  end

  def mc_optional(credentials, missing_code, *arguments)
    output, status = mc_raw(credentials, *arguments)
    response = JSON.parse(output)
    return response if status.success? && response['status'] == 'success'

    code = response.dig('error', 'cause', 'error', 'Code')
    return nil if !status.success? && response['status'] == 'error' && code == missing_code

    raise "MinIO admin #{arguments.first(2).join(' ')} failed"
  end

  def ensure_fresh_iam_state(credentials)
    ensure_true(mc_optional(credentials, 'XMinioAdminNoSuchUser', 'user', 'info', 'audit', USER).nil?,
                'Job API MinIO user already exists; inspect partial IAM state')
    POLICIES.each do |name|
      ensure_true(mc_optional(credentials, 'XMinioAdminNoSuchPolicy', 'policy', 'info', 'audit', name).nil?,
                  "MinIO #{name} policy already exists; inspect partial IAM state")
    end
  end

  def verify_iam_subset(credentials, policies)
    POLICIES.each { |name| verify_policy(credentials, name, policies.fetch(name)) }
    verify_user(credentials, POLICIES)
  end

  def verify_policy(credentials, name, expected)
    actual = mc(credentials, 'policy', 'info', 'audit', name).dig('policyInfo', 'Policy')
    same("#{name} IAM document", normalized_policy(actual), normalized_policy(expected.fetch(:policy)))
    mappings = mc(credentials, 'policy', 'entities', '--policy', name, 'audit').dig('result', 'policyMappings') || []
    same("#{name} mapping count", mappings.length, 1)
    same("#{name} mapped policy", mappings.first['policy'], name)
    same("#{name} user", mappings.first['users'], [USER])
    ensure_true(Array(mappings.first['groups']).empty?, "#{name} has an unexpected group")
  end

  def verify_user(credentials, expected_names)
    info = mc(credentials, 'user', 'info', 'audit', USER)
    same('Job API MinIO user status', info['userStatus'], 'enabled')
    same('Job API MinIO user policies', info['policyName'].to_s.split(',').sort, expected_names.sort)
    mappings = mc(credentials, 'policy', 'entities', '--user', USER, 'audit').dig('result', 'userMappings') || []
    same('Job API MinIO user mapping count', mappings.length, 1)
    same('Job API MinIO mapped user', mappings.first['user'], USER)
    same('Job API MinIO mapped policies', Array(mappings.first['policies']).sort, expected_names.sort)
  end

  def kubectl_write(*arguments)
    _output, _stderr, status = @command.call('kubectl', '--context', CONTEXT, '-n', DATA_NAMESPACE, *arguments)
    ensure_true(status.success?, "Job API MinIO #{arguments.first} failed; inspect fixed-name Jobs and temporary Secret")
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: minio-job-api-iam-stage.rb plan|bootstrap|verify|reconcile' unless ARGV.length == 1
  exit MinioJobApiIamStage.new.run(ARGV.first)
end
