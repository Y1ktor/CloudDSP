#!/usr/bin/env ruby
# Verify the durable Keycloak realm state left by the versioned bootstrap Jobs.
#
# Helm owns the Keycloak Deployment, Service, and Ingress, but not the realm,
# browser client, API audience, Mailpit SMTP settings, or registration flow.
# Read those through the local Admin API and compare only reviewed non-secret
# fields with the committed Job payloads. This command creates no Job, user,
# token file, Helm revision, or Keycloak configuration change.

require_relative '../../lib/paths'
require 'base64'
require 'json'
require 'net/http'
require 'open3'
require 'pathname'
require 'uri'
require 'yaml'

class KeycloakConfigVerify
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  SOURCE = ROOT.join('services', 'keycloak').freeze
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-data'.freeze
  SECRET = 'clouddsp-keycloak-bootstrap-admin'.freeze
  HOST = 'keycloak.localhost'.freeze
  PORT = 8080
  JOBS = {
    realm: 'keycloak-realm-bootstrap-job.yaml',
    registration: 'keycloak-realm-registration-policy-job.yaml',
    smtp: 'keycloak-realm-smtp-config-job.yaml',
    react: 'keycloak-frontend-oidc-client-bootstrap-job.yaml',
    audience: 'keycloak-job-api-audience-bootstrap-job.yaml',
    password_form: 'keycloak-registration-password-form-policy-job.yaml'
  }.freeze

  def initialize(command: Open3.method(:capture3), http_factory: nil, output: $stdout, error: $stderr)
    @command = command
    @http_factory = http_factory || -> { Net::HTTP.new('127.0.0.1', PORT, nil) }
    @output = output
    @error = error
  end

  def run
    desired = load_desired
    credentials = admin_credentials
    http = @http_factory.call
    http.open_timeout = 8
    http.read_timeout = 15
    http.start
    begin
      token = api(http, '/realms/master/protocol/openid-connect/token', form: {
        'grant_type' => 'password', 'client_id' => 'admin-cli',
        'username' => credentials.fetch('KC_BOOTSTRAP_ADMIN_USERNAME'),
        'password' => credentials.fetch('KC_BOOTSTRAP_ADMIN_PASSWORD')
      }).fetch('access_token')
      verify_live(http, token, desired)
    ensure
      http.finish if http.started?
    end
    @output.puts 'Keycloak realm, clients, audience, SMTP, registration flow, and issuer verified.'
    0
  rescue StandardError => exception
    # Never print an Admin API response, token, Secret value, request object,
    # or client representation. A field label is enough for diagnosis.
    @error.puts "Keycloak configuration verify stopped: #{safe_error(exception)}"
    1
  end

  def load_desired
    jobs = JOBS.transform_values { |filename| source_job(filename) }
    realm_name = jobs.fetch(:realm).fetch(:env).fetch('CLOUDDSP_REALM')
    ensure_true(realm_name == 'clouddsp', 'source realm name changed')
    ensure_true(jobs.values.all? { |job| job.fetch(:env)['CLOUDDSP_REALM'] == realm_name },
                'source Jobs disagree on realm name')
    ensure_true(jobs.values.all? { |job| job.fetch(:env)['KEYCLOAK_ADMIN_REALM'] == 'master' },
                'source Jobs changed the administrator realm')
    ensure_true(jobs.fetch(:realm).fetch(:script).include?("--set='enabled=true'"),
                'source realm bootstrap no longer enables the realm')

    payloads = %i[registration smtp react audience password_form].to_h do |name|
      [name, json_payloads(jobs.fetch(name))]
    end
    { registration: 1, smtp: 1, react: 1, audience: 2, password_form: 1 }.each do |name, count|
      ensure_true(payloads.fetch(name).length == count, "source #{name} JSON payload count changed")
    end
    registration = payloads.fetch(:registration).fetch(0)
    smtp = payloads.fetch(:smtp).fetch(0).fetch('smtpServer')
    react = payloads.fetch(:react).fetch(0)
    resource, mapper = payloads.fetch(:audience)
    password_form = payloads.fetch(:password_form).fetch(0)
    ensure_true(registration == {
      'registrationAllowed' => true, 'verifyEmail' => true, 'resetPasswordAllowed' => true
    }, 'source registration policy changed')
    ensure_true(smtp == {
      'host' => 'clouddsp-mailpit-smtp', 'port' => '1025', 'from' => 'noreply@clouddsp.test',
      'fromDisplayName' => 'CloudDSP local', 'auth' => 'false', 'ssl' => 'false', 'starttls' => 'false'
    }, 'source SMTP policy changed')
    ensure_true(react['clientId'] == 'clouddsp-react' &&
                react['enabled'] == true &&
                react['rootUrl'] == 'http://clouddsp.localhost:8080' &&
                react['redirectUris'] == ['http://clouddsp.localhost:8080/'] &&
                react['webOrigins'] == ['http://clouddsp.localhost:8080'] &&
                react.dig('attributes', 'pkce.code.challenge.method') == 'S256' &&
                react.dig('attributes', 'post.logout.redirect.uris') == 'http://clouddsp.localhost:8080/',
                'source React redirect or PKCE policy changed')
    ensure_true(resource['clientId'] == 'clouddsp-job-api' && resource['enabled'] == true &&
                mapper.dig('config', 'included.client.audience') == resource['clientId'] &&
                mapper.dig('config', 'access.token.claim') == 'true' &&
                mapper.dig('config', 'id.token.claim') == 'false',
                'source Job API audience policy changed')
    [react, resource].each do |client|
      ensure_true(client['publicClient'] == true && client['implicitFlowEnabled'] == false &&
                  client['directAccessGrantsEnabled'] == false &&
                  client['serviceAccountsEnabled'] == false &&
                  client['authorizationServicesEnabled'] == false &&
                  client['fullScopeAllowed'] == false,
                  "source #{client['clientId']} grant policy changed")
    end
    ensure_true(react['standardFlowEnabled'] == true && resource['standardFlowEnabled'] == false,
                'source client authorization-code policy changed')
    ensure_true(password_form['alias'] == 'clouddsp-registration-password-form-policy' &&
                password_form.dig('config', 'always_set_password_on_register_form') == 'true',
                'source registration password-form policy changed')

    { realm: realm_name, registration: registration, smtp: smtp, react: react,
      resource: resource, mapper: mapper, password_form: password_form }
  end

  private

  def source_job(filename)
    document = YAML.load_file(SOURCE.join(filename).to_s)
    ensure_true(document['kind'] == 'Job' && document.dig('metadata', 'namespace') == NAMESPACE,
                "source #{filename} identity changed")
    container = document.dig('spec', 'template', 'spec', 'containers', 0)
    ensure_true(container.is_a?(Hash), "source #{filename} has no container")
    env = container.fetch('env').each_with_object({}) do |item, values|
      values[item['name']] = item['value'] if item.key?('value')
    end
    { env: env, script: container.fetch('command').last }
  end

  def json_payloads(job)
    texts = job.fetch(:script).scan(/<<'?JSON'?\n(.*?)\n\s*JSON/m).map(&:first)
    ensure_true(!texts.empty?, 'source Job has no JSON payload')
    texts.map do |text|
      expanded = text.gsub(/\$([A-Z][A-Z0-9_]*)/) { job.fetch(:env).fetch(Regexp.last_match(1)) }
      JSON.parse(expanded)
    end
  end

  def admin_credentials
    raw, _stderr, status = @command.call('kubectl', '--context', CONTEXT, '-n', NAMESPACE,
                                          'get', "secret/#{SECRET}", '-o', 'json')
    ensure_true(status.success?, 'bootstrap administrator Secret is unavailable')
    values = JSON.parse(raw).fetch('data')
    %w[KC_BOOTSTRAP_ADMIN_USERNAME KC_BOOTSTRAP_ADMIN_PASSWORD].to_h do |key|
      [key, Base64.strict_decode64(values.fetch(key))]
    end
  end

  def api(http, path, token: nil, form: nil)
    request = form ? Net::HTTP::Post.new(path) : Net::HTTP::Get.new(path)
    request['Host'] = HOST
    request['Authorization'] = "Bearer #{token}" if token
    request.set_form_data(form) if form
    response = http.request(request)
    ensure_true(response.is_a?(Net::HTTPSuccess), "Keycloak HTTP #{response.code} at #{path.split('?').first}")
    JSON.parse(response.body)
  end

  def verify_live(http, token, desired)
    realm_name = desired.fetch(:realm)
    realm = api(http, "/admin/realms/#{realm_name}", token: token)
    same('realm name', realm['realm'], realm_name)
    same('realm enabled', realm['enabled'], true)
    desired.fetch(:registration).each { |key, value| same("realm #{key}", realm[key], value) }
    smtp = realm.fetch('smtpServer')
    desired.fetch(:smtp).each { |key, value| same("realm SMTP #{key}", smtp[key], value) }
    ensure_true(%w[user password].none? { |key| smtp.key?(key) && !smtp[key].to_s.empty? },
                'realm SMTP has unexpected credentials')

    react = client(http, token, realm_name, desired.fetch(:react).fetch('clientId'))
    resource = client(http, token, realm_name, desired.fetch(:resource).fetch('clientId'))
    compare_client('React client', react, desired.fetch(:react))
    compare_client('Job API client', resource, desired.fetch(:resource))
    mappers = api(http, "/admin/realms/#{realm_name}/clients/#{react.fetch('id')}/protocol-mappers/models", token: token)
    name = desired.fetch(:mapper).fetch('name')
    matching = mappers.select { |mapper| mapper['name'] == name }
    same('React audience mapper count', matching.length, 1)
    compare_fields('React audience mapper', matching.first, desired.fetch(:mapper))

    executions = api(http, "/admin/realms/#{realm_name}/authentication/flows/registration/executions", token: token)
    actions = executions.select { |item| item['providerId'] == 'registration-password-action' }
    same('registration password action count', actions.length, 1)
    config_id = actions.first['authenticationConfig']
    ensure_true(config_id.is_a?(String) && !config_id.empty?, 'registration password action has no configuration')
    config = api(http, "/admin/realms/#{realm_name}/authentication/config/#{config_id}", token: token)
    compare_fields('registration password form', config, desired.fetch(:password_form))

    discovery = api(http, "/realms/#{realm_name}/.well-known/openid-configuration")
    same('public OIDC issuer', discovery['issuer'], "http://#{HOST}:#{PORT}/realms/#{realm_name}")
  end

  def client(http, token, realm_name, client_id)
    query = URI.encode_www_form(clientId: client_id)
    listing = api(http, "/admin/realms/#{realm_name}/clients?#{query}", token: token)
    matching = listing.select { |item| item['clientId'] == client_id }
    same("#{client_id} client count", matching.length, 1)
    api(http, "/admin/realms/#{realm_name}/clients/#{matching.first.fetch('id')}", token: token)
  end

  def compare_client(label, actual, expected)
    compare_fields(label, actual, expected)
  end

  def compare_fields(label, actual, expected)
    expected.each do |key, value|
      next if %w[name description].include?(key) && label.end_with?('client')
      live = actual[key]
      # Keycloak omits this false field from the public-client representation.
      live = false if key == 'authorizationServicesEnabled' && live.nil? && value == false
      if value.is_a?(Hash)
        ensure_true(live.is_a?(Hash), "#{label} #{key} missing")
        compare_fields("#{label} #{key}", live, value)
      else
        same("#{label} #{key}", live, value)
      end
    end
  end

  def same(label, actual, expected)
    ensure_true(actual == expected, "#{label} drifted")
  end

  def ensure_true(condition, message)
    raise message unless condition
  end

  def safe_error(error)
    return error.message if error.instance_of?(RuntimeError)

    error.class.to_s
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: keycloak-config-verify.rb verify' unless ARGV == ['verify']
  exit KeycloakConfigVerify.new.run
end
