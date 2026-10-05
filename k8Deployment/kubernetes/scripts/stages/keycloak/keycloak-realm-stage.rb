#!/usr/bin/env ruby
# Run the six reviewed Keycloak Admin API Jobs on a fresh local cluster.
# The realm and client records inside Keycloak/PostgreSQL are durable state;
# completed Kubernetes Jobs are disposable and can expire after their TTL.
# An existing but incomplete realm is never silently repaired by this stage.
require_relative '../../lib/paths'
require 'base64'
require 'json'
require 'net/http'
require 'open3'
require 'pathname'
require 'yaml'
require_relative 'keycloak-config-verify'

class CloudDSPKeycloakRealmStage
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-data'.freeze
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  SOURCE = ROOT.join('services', 'keycloak').freeze
  # Registration must exist before its password form policy. The React client
  # must exist before the Job API audience mapper is added to it. Mailpit is
  # verified before the SMTP Job so its configured Service already resolves.
  JOBS = [
    ['realm', 'keycloak-realm-bootstrap-job.yaml', 'keycloak-realm-bootstrap'],
    ['registration', 'keycloak-realm-registration-policy-job.yaml', 'keycloak-realm-registration-policy'],
    ['smtp', 'keycloak-realm-smtp-config-job.yaml', 'keycloak-realm-smtp-config'],
    ['react', 'keycloak-frontend-oidc-client-bootstrap-job.yaml', 'keycloak-frontend-oidc-client-bootstrap'],
    ['audience', 'keycloak-job-api-audience-bootstrap-job.yaml', 'keycloak-job-api-audience-bootstrap'],
    ['password form', 'keycloak-registration-password-form-policy-job.yaml', 'keycloak-registration-password-form-policy']
  ].freeze
  ADMIN_REFS = [
    ['KC_BOOTSTRAP_ADMIN_USERNAME', 'clouddsp-keycloak-bootstrap-admin', 'KC_BOOTSTRAP_ADMIN_USERNAME'],
    ['KC_BOOTSTRAP_ADMIN_PASSWORD', 'clouddsp-keycloak-bootstrap-admin', 'KC_BOOTSTRAP_ADMIN_PASSWORD']
  ].freeze
  PREREQUISITES = [
    ['Keycloak database', 'keycloak-database-stage.rb'],
    ['Keycloak admin Secret', 'keycloak-admin-secret-stage.rb'],
    ['Mailpit release', 'mailpit-release.rb'],
    ['Keycloak release', 'keycloak-release.rb']
  ].freeze

  def initialize(command: Open3.method(:capture3), probe: nil, verifier: nil,
                 http_factory: nil, output: $stdout, error: $stderr)
    @command = command
    @probe = probe || method(:probe_realm)
    @output = output
    @error = error
    @http_factory = http_factory || -> { Net::HTTP.new('127.0.0.1', KeycloakConfigVerify::PORT, nil) }
    @verifier = verifier || KeycloakConfigVerify.new(command: command, output: output, error: error)
  end

  def run(mode)
    ensure_true(%w[plan bootstrap verify].include?(mode), 'use plan, bootstrap, or verify')
    @verifier.load_desired
    jobs = load_jobs
    check_prerequisites
    if mode == 'verify'
      verify_configuration
      @output.puts 'Keycloak realm and clients verified'
      return 0
    end

    state = @probe.call
    ensure_true(%i[absent present].include?(state), 'Keycloak realm probe returned an invalid state')
    if state == :present
      verify_configuration
      @output.puts 'Keycloak realm and clients already verified; no work pending'
      return 0
    end

    ensure_jobs_absent
    if mode == 'plan'
      @output.puts 'Keycloak realm plan: realm absent; six versioned Admin API Jobs pending'
      return 0
    end

    # Validate every Job with the API server before the first mutation. A
    # second realm and Job lookup narrows the race with another bootstrap run.
    jobs.each do |job|
      job_command("Keycloak #{job.fetch(:label)} Job server dry run", 'create',
                  '--dry-run=server', '--filename', job.fetch(:path).to_s)
    end
    ensure_true(@probe.call == :absent, 'Keycloak realm appeared during preflight')
    ensure_jobs_absent
    jobs.each_with_index do |job, index|
      @output.puts "Keycloak realm bootstrap #{index + 1}/#{jobs.length}: #{job.fetch(:label)}"
      @output.flush
      job_command("Keycloak #{job.fetch(:label)} Job creation", 'create',
                  '--filename', job.fetch(:path).to_s)
      job_command("Keycloak #{job.fetch(:label)} Job completion", 'wait',
                  '--for=condition=complete', "job/#{job.fetch(:name)}",
                  "--timeout=#{job.fetch(:deadline) + 60}s")
    end
    verify_configuration
    @output.puts 'Keycloak realm, clients, audience, SMTP, and registration policies bootstrapped'
    0
  rescue StandardError => exception
    # Admin API responses, Job logs, and kubectl errors may contain tokens or
    # credential-bearing details. Only our fixed RuntimeError text is shown.
    detail = exception.instance_of?(RuntimeError) ? exception.message : exception.class.to_s
    @error.puts "Keycloak realm #{mode} stopped: #{detail}"
    1
  end

  private

  def ensure_true(condition, message)
    raise message unless condition
  end

  def command(label, *argv)
    stdout, _stderr, status = @command.call(*argv)
    ensure_true(status.success?, "#{label} failed; inspect the named resource")
    stdout
  end

  def kubectl(*args)
    command('Kubernetes operation', 'kubectl', '--context', CONTEXT, '--namespace', NAMESPACE, *args)
  end

  def job_command(label, *args)
    command(label, 'kubectl', '--context', CONTEXT, '--namespace', NAMESPACE, *args)
  end

  def load_jobs
    lock = YAML.safe_load(ROOT.join('images.lock.yaml').read)
    image = lock.fetch('images').fetch('keycloak').fetch('immutableReference')
    JOBS.map do |label, filename, name|
      path = SOURCE.join(filename)
      source = YAML.safe_load(path.read)
      pod = source.dig('spec', 'template', 'spec')
      containers = pod&.fetch('containers', nil)
      container = containers&.first
      refs = container&.fetch('env', nil)&.map do |entry|
        key = entry['valueFrom']&.dig('secretKeyRef')
        [entry['name'], key['name'], key['key']] if key
      end&.compact
      deadline = source.dig('spec', 'activeDeadlineSeconds')
      ensure_true(source['apiVersion'] == 'batch/v1' && source['kind'] == 'Job' &&
                  source.dig('metadata', 'name') == name &&
                  source.dig('metadata', 'namespace') == NAMESPACE &&
                  source.dig('metadata', 'labels', 'app.kubernetes.io/managed-by') == 'kubectl' &&
                  source.dig('spec', 'ttlSecondsAfterFinished') == 300 &&
                  source.dig('spec', 'backoffLimit') == 0 &&
                  deadline.is_a?(Integer) && deadline.positive? &&
                  pod&.dig('restartPolicy') == 'Never' &&
                  pod&.dig('automountServiceAccountToken') == false &&
                  containers.is_a?(Array) && containers.length == 1 &&
                  container['image'] == image && refs&.sort == ADMIN_REFS.sort &&
                  container.dig('command', -1).to_s.include?('--no-config'),
                  "versioned Keycloak #{label} Job changed its reviewed contract")
      { label: label, name: name, path: path, deadline: deadline }
    end
  end

  def check_prerequisites
    PREREQUISITES.each do |label, filename|
      command("#{label} verification", 'ruby', CloudDSPPaths.script(filename).to_s, 'verify')
    end
  end

  def ensure_jobs_absent
    JOBS.each do |label, _filename, name|
      output = kubectl('get', "job/#{name}", '--ignore-not-found', '--output=name')
      ensure_true(output.strip.empty?, "Keycloak #{label} Job already exists without complete realm state")
    end
  end

  def verify_configuration
    ensure_true(@verifier.run == 0, 'Keycloak realm/client state is incomplete or drifted')
  end

  def probe_realm
    # Read only the two encoded live Secret values; the prerequisite stage has
    # already compared them with the ignored source. Tokens stay in memory.
    raw = kubectl('get', 'secret/clouddsp-keycloak-bootstrap-admin', '--output=json')
    encoded = JSON.parse(raw).fetch('data')
    username = Base64.strict_decode64(encoded.fetch('KC_BOOTSTRAP_ADMIN_USERNAME'))
    password = Base64.strict_decode64(encoded.fetch('KC_BOOTSTRAP_ADMIN_PASSWORD'))
    http = @http_factory.call
    http.open_timeout = 8
    http.read_timeout = 15
    http.start
    begin
      login = Net::HTTP::Post.new('/realms/master/protocol/openid-connect/token', { 'Host' => KeycloakConfigVerify::HOST })
      login.set_form_data('grant_type' => 'password', 'client_id' => 'admin-cli',
                          'username' => username, 'password' => password)
      response = http.request(login)
      ensure_true(response.is_a?(Net::HTTPSuccess), 'Keycloak administrator login failed')
      token = JSON.parse(response.body).fetch('access_token')
      ensure_true(token.is_a?(String) && !token.empty?, 'Keycloak administrator token is invalid')
      request = Net::HTTP::Get.new('/admin/realms/clouddsp', { 'Host' => KeycloakConfigVerify::HOST,
                                                               'Authorization' => "Bearer #{token}" })
      realm = http.request(request)
      return :absent if realm.code == '404'
      return :present if realm.is_a?(Net::HTTPSuccess)

      raise "Keycloak realm lookup returned HTTP #{realm.code}"
    ensure
      http.finish if http.started?
    end
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: keycloak-realm-stage.rb plan|bootstrap|verify' unless ARGV.length == 1
  exit CloudDSPKeycloakRealmStage.new.run(ARGV.first)
end
