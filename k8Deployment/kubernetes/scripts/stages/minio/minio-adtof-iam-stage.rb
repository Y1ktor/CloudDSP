#!/usr/bin/env ruby
# Provision the ADTOF worker's single restricted MinIO identity.
# The fixed Kubernetes Job uses a temporary clouddsp-data Secret because the
# ongoing Pod's runtime Secret lives in clouddsp-app. Only the Job can read the
# MinIO root Secret; this runner removes its temporary restricted credential
# after the durable policy and user mapping match the reviewed source.
require_relative '../../lib/paths'
require 'rbconfig'
require_relative 'minio-state-verify'

class MinioAdtofIamStage < MinioStateVerify
  USER = 'clouddsp-adtof'.freeze
  POLICY = 'clouddsp-adtof-artifacts-v001'.freeze
  JOB = 'minio-adtof-artifacts-bootstrap'.freeze
  TEMP_SECRET = 'clouddsp-adtof-minio-bootstrap-credentials'.freeze
  TEMP_SOURCE = ROOT.parent.join('.local', 'adtof-minio-bootstrap-credentials.secret.yaml').freeze
  TEMP_EXAMPLE = ROOT.join('services', 'adtof', 'adtof-minio-bootstrap-credentials.secret.example.yaml').freeze
  PREREQUISITES = [
    ['minio-release.rb', 'verify'],
    ['adtof-minio-secret-stage.rb', 'verify'],
    ['minio-fresh-buckets-stage.rb', 'verify']
  ].freeze
  EXPECTED_ARGS = [
    %w[alias set minio-admin http://clouddsp-minio:9000 $(MINIO_ROOT_USER) $(MINIO_ROOT_PASSWORD)],
    %w[ready minio-admin],
    %w[admin policy create minio-admin clouddsp-adtof-artifacts-v001 /policy/adtof-artifacts-policy.json],
    %w[admin user add minio-admin $(ADTOF_S3_ACCESS_KEY) $(ADTOF_S3_SECRET_KEY)],
    %w[admin policy attach minio-admin clouddsp-adtof-artifacts-v001 --user=$(ADTOF_S3_ACCESS_KEY)],
    %w[admin user info minio-admin $(ADTOF_S3_ACCESS_KEY)],
    %w[alias remove minio-admin]
  ].freeze

  def run(mode)
    ensure_true(%w[plan bootstrap verify reconcile].include?(mode), 'use plan, bootstrap, verify, or reconcile')
    policy = load_source.fetch(POLICY)
    validate_job(policy)
    verify_prerequisites
    credentials = root_credentials

    if mode == 'verify' || mode == 'reconcile'
      verify_configmap(policy)
      verify_job_if_present
      verify_iam_subset(credentials, policy)
      temporary = resource('secret', TEMP_SECRET)
      if mode == 'verify'
        ensure_true(temporary.nil?, 'temporary adtof MinIO Secret remains; inspect or reconcile it')
        @output.puts 'adtof MinIO IAM: exact artifacts policy and user verified; temporary Secret absent'
      elsif temporary
        verify_temporary_secret(temporary)
        kubectl_write('delete', "secret/#{TEMP_SECRET}", '--wait=true')
        ensure_absent('secret', TEMP_SECRET)
        @output.puts 'adtof MinIO IAM reconcile: exact IAM verified; matching temporary Secret removed'
      else
        @output.puts 'adtof MinIO IAM reconcile: exact IAM verified; no temporary Secret remains'
      end
      return 0
    end

    ensure_fresh_kubernetes_state(policy)
    ensure_fresh_iam_state(credentials)
    if mode == 'plan'
      @output.puts 'adtof MinIO IAM plan: absent user and policy; versioned Job pending'
      return 0
    end

    # Validate all three inputs at the API server before creating any of them.
    # kubectl output is suppressed because the ignored Secret carries a key.
    [TEMP_SOURCE, config_path(policy), job_path].each do |path|
      kubectl_write('create', '--dry-run=server', '--filename', path.to_s)
    end
    ensure_fresh_kubernetes_state(policy)
    ensure_fresh_iam_state(credentials)
    kubectl_write('create', '--filename', TEMP_SOURCE.to_s)
    verify_temporary_secret(resource('secret', TEMP_SECRET))
    kubectl_write('create', '--filename', config_path(policy).to_s)
    kubectl_write('create', '--filename', job_path.to_s)
    kubectl_write('wait', '--for=condition=complete', "job/#{JOB}", '--timeout=210s')
    verify_configmap(policy)
    verify_job_if_present
    verify_iam_subset(credentials, policy)
    kubectl_write('delete', "secret/#{TEMP_SECRET}", '--wait=true')
    ensure_absent('secret', TEMP_SECRET)
    @output.puts 'adtof MinIO IAM bootstrap: exact policy and restricted user verified; temporary Secret removed'
    0
  rescue StandardError => exception
    @error.puts "adtof MinIO IAM #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def verify_prerequisites
    PREREQUISITES.each do |script, argument|
      _output, _stderr, status = @command.call(RbConfig.ruby, CloudDSPPaths.script(script).to_s, argument)
      ensure_true(status.success?, "#{script} prerequisite verification failed")
    end
  end

  def config_path(policy)
    SOURCE.join("minio-#{policy.fetch(:name).delete_prefix('clouddsp-')}-configmap.yaml")
  end

  def job_path
    SOURCE.join("#{JOB}-job.yaml")
  end

  def validate_job(policy)
    # This reviewed Job reuses resource and security settings through YAML
    # anchors. Psych disables aliases by default, so allow only YAML aliases
    # while keeping object deserialization disabled.
    job = YAML.safe_load(File.read(job_path.to_s), aliases: true)
    pod = job.dig('spec', 'template', 'spec')
    init = pod.fetch('initContainers')
    same('adtof IAM Job identity', [job['apiVersion'], job['kind'], job.dig('metadata', 'namespace'), job.dig('metadata', 'name')],
         ['batch/v1', 'Job', DATA_NAMESPACE, JOB])
    same('adtof IAM Job retry and cleanup bounds',
         [job.dig('spec', 'backoffLimit'), job.dig('spec', 'activeDeadlineSeconds'),
          job.dig('spec', 'ttlSecondsAfterFinished')], [0, 180, 600])
    same('adtof IAM Pod API boundary', [pod['automountServiceAccountToken'], pod['restartPolicy']],
         [false, 'Never'])
    same('adtof IAM init commands', init.map { |container| container['args'] }, EXPECTED_ARGS)
    # The first controlled ADTOF bootstrap exhausted a 128Mi limit while
    # attaching the policy. Keep the reviewed 256Mi bound for the two serial
    # association checks so an accidental manifest regression fails preflight.
    association = init.select { |container| %w[attach-adtof-artifacts-policy verify-adtof-user-policy].include?(container['name']) }
    same('adtof IAM policy association steps', association.map { |container| container['name'] },
         %w[attach-adtof-artifacts-policy verify-adtof-user-policy])
    same('adtof IAM policy association memory limit',
         association.map { |container| container.dig('resources', 'limits', 'memory') }, %w[256Mi 256Mi])
    ensure_true((init + pod.fetch('containers')).all? do |container|
      container['image'] == MC_IMAGE && container['imagePullPolicy'] == 'IfNotPresent' &&
        container.dig('securityContext', 'readOnlyRootFilesystem') == true &&
        container.dig('securityContext', 'allowPrivilegeEscalation') == false &&
        container.dig('securityContext', 'capabilities', 'drop') == ['ALL']
    end, 'adtof IAM Job image or container security changed')
    refs = init.flat_map { |container| Array(container['env']) }
               .map { |item| [item['name'], item.dig('valueFrom', 'secretKeyRef')] if item.dig('valueFrom', 'secretKeyRef') }.compact
    expected_refs = {
      'MINIO_ROOT_USER' => { 'name' => 'clouddsp-minio-root-credentials', 'key' => 'MINIO_ROOT_USER' },
      'MINIO_ROOT_PASSWORD' => { 'name' => 'clouddsp-minio-root-credentials', 'key' => 'MINIO_ROOT_PASSWORD' },
      'ADTOF_S3_ACCESS_KEY' => { 'name' => TEMP_SECRET, 'key' => 'ADTOF_S3_ACCESS_KEY' },
      'ADTOF_S3_SECRET_KEY' => { 'name' => TEMP_SECRET, 'key' => 'ADTOF_S3_SECRET_KEY' }
    }
    same('adtof IAM Job Secret references', refs.to_h, expected_refs)
    ensure_true(refs.all? { |key, reference| expected_refs[key] == reference },
                'adtof IAM Job has unexpected Secret references')
    configs = pod.fetch('volumes').map { |volume| volume['configMap'] }.compact
    same('adtof IAM Job policy ConfigMap', configs.map { |item| item['name'] }, [policy.fetch(:name)])
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

  def ensure_fresh_kubernetes_state(policy)
    ensure_absent('secret', TEMP_SECRET)
    ensure_absent('configmap', policy.fetch(:name))
    ensure_absent('job', JOB)
  end

  def verify_temporary_secret(secret)
    ensure_true(secret.is_a?(Hash) && secret['kind'] == 'Secret' &&
                secret.dig('metadata', 'name') == TEMP_SECRET &&
                secret.dig('metadata', 'namespace') == DATA_NAMESPACE &&
                secret['type'] == 'Opaque', 'temporary adtof MinIO Secret identity or type differs')
    example = YAML.safe_load(TEMP_EXAMPLE.read)
    same('temporary adtof MinIO Secret labels', secret.dig('metadata', 'labels'), example.dig('metadata', 'labels'))
    expected = YAML.safe_load(TEMP_SOURCE.read).fetch('stringData')
    data = secret.fetch('data')
    same('temporary adtof MinIO Secret keys', data.keys.sort, expected.keys.sort)
    ensure_true(data.all? { |key, encoded| Base64.strict_decode64(encoded) == expected.fetch(key) },
                'temporary adtof MinIO Secret differs from ignored local source')
  rescue ArgumentError, KeyError, Psych::Exception
    raise 'temporary adtof MinIO Secret has invalid encoded data or source'
  end

  def verify_configmap(policy)
    actual = resource('configmap', policy.fetch(:name))
    ensure_true(!actual.nil?, "configmap/#{policy.fetch(:name)} is absent")
    same('adtof policy ConfigMap immutable', actual['immutable'], true)
    same('adtof policy ConfigMap data', actual['data'], policy.fetch(:data))
  end

  def verify_job_if_present
    job = resource('job', JOB)
    same('adtof IAM Job completion', job.dig('status', 'succeeded'), 1) if job
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
                'adtof MinIO user already exists; inspect partial IAM state')
    ensure_true(mc_optional(credentials, 'XMinioAdminNoSuchPolicy', 'policy', 'info', 'audit', POLICY).nil?,
                'adtof MinIO policy already exists; inspect partial IAM state')
  end

  def verify_iam_subset(credentials, policy)
    actual = mc(credentials, 'policy', 'info', 'audit', POLICY).dig('policyInfo', 'Policy')
    same('adtof IAM policy document', normalized_policy(actual), normalized_policy(policy.fetch(:policy)))
    mappings = mc(credentials, 'policy', 'entities', '--policy', POLICY, 'audit').dig('result', 'policyMappings') || []
    same('adtof IAM policy mapping count', mappings.length, 1)
    same('adtof IAM mapped policy', mappings.first['policy'], POLICY)
    same('adtof IAM policy user', mappings.first['users'], [USER])
    ensure_true(Array(mappings.first['groups']).empty?, 'adtof IAM policy has an unexpected group')
    info = mc(credentials, 'user', 'info', 'audit', USER)
    same('adtof MinIO user status', info['userStatus'], 'enabled')
    same('adtof MinIO user policies', info['policyName'].to_s.split(',').sort, [POLICY])
    user_mappings = mc(credentials, 'policy', 'entities', '--user', USER, 'audit').dig('result', 'userMappings') || []
    same('adtof MinIO user mapping count', user_mappings.length, 1)
    same('adtof MinIO mapped user', user_mappings.first['user'], USER)
    same('adtof MinIO mapped policies', Array(user_mappings.first['policies']).sort, [POLICY])
  end

  def kubectl_write(*arguments)
    _output, _stderr, status = @command.call('kubectl', '--context', CONTEXT, '-n', DATA_NAMESPACE, *arguments)
    ensure_true(status.success?, "adtof MinIO #{arguments.first} failed; inspect fixed-name Job and temporary Secret")
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: minio-adtof-iam-stage.rb plan|bootstrap|verify|reconcile' unless ARGV.length == 1
  exit MinioAdtofIamStage.new.run(ARGV.first)
end
