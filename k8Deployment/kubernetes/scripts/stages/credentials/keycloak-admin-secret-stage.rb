#!/usr/bin/env ruby
# Stage the bootstrap administrator login before a fresh Keycloak Helm install.
# Keycloak consumes this Secret only when it creates the initial master-realm
# administrator. Its lifetime and ownership are separate from the Helm chart;
# changing it later is not a password-reset operation. The ignored local YAML
# is compared with the committed template without printing either credential.
require_relative '../../lib/paths'
require 'base64'
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class CloudDSPKeycloakAdminSecretStage
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-data'.freeze
  NAME = 'clouddsp-keycloak-bootstrap-admin'.freeze
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  LOCAL_PATH = ROOT.parent.join('.local', 'keycloak-bootstrap-admin.secret.yaml').freeze
  EXAMPLE_PATH = ROOT.join('services', 'keycloak', 'keycloak-bootstrap-admin.secret.example.yaml').freeze
  KEYS = %w[KC_BOOTSTRAP_ADMIN_USERNAME KC_BOOTSTRAP_ADMIN_PASSWORD].freeze

  def initialize(command: Open3.method(:capture3), local_path: LOCAL_PATH, output: $stdout, error: $stderr)
    @command = command
    @local_path = Pathname.new(local_path)
    @output = output
    @error = error
  end

  def run(mode)
    ensure_true(%w[plan bootstrap verify].include?(mode), 'use plan, bootstrap, or verify')
    expected = load_local_source
    live = live_secret
    if mode == 'verify'
      ensure_true(!live.nil?, 'Keycloak bootstrap-admin Secret is absent')
      verify_live(live, expected)
      @output.puts 'Keycloak bootstrap-admin Secret verified against ignored local source'
      return 0
    end

    # An existing Secret can mean a partial deployment. A fresh install must
    # never silently adopt it or change a running administrator's credential.
    ensure_true(live.nil?, 'Keycloak bootstrap-admin Secret already exists; use verify or inspect it')
    if mode == 'plan'
      @output.puts 'Keycloak bootstrap-admin Secret plan: reviewed local source ready; creation pending'
      return 0
    end

    # The API server checks the ignored source before the only write. Neither
    # server response is printed because admission errors can echo Secret data.
    command('Keycloak admin Secret server dry run', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--dry-run=server', '--filename', @local_path.to_s)
    command('Keycloak admin Secret creation', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--filename', @local_path.to_s)
    created = live_secret
    ensure_true(!created.nil?, 'Keycloak admin Secret create returned without a live Secret')
    verify_live(created, expected)
    @output.puts 'Keycloak bootstrap-admin Secret created and verified'
    0
  rescue StandardError => exception
    @error.puts "Keycloak bootstrap-admin Secret #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def load_local_source
    ensure_true(@local_path.file?, 'ignored local Keycloak bootstrap-admin Secret file is absent')
    source = YAML.safe_load(@local_path.read)
    example = YAML.safe_load(EXAMPLE_PATH.read)
    ensure_true(source.is_a?(Hash) && example.is_a?(Hash), 'Keycloak admin Secret source contract is invalid')
    ensure_true(source.keys.sort == example.keys.sort, 'Keycloak admin Secret source fields differ from example')
    %w[apiVersion kind metadata type].each do |field|
      ensure_true(source[field] == example[field], 'Keycloak admin Secret source contract differs from example')
    end
    values = source['stringData']
    ensure_true(source['data'].nil? && values.is_a?(Hash) && values.keys.sort == KEYS.sort,
                'Keycloak admin Secret source keys differ from example')
    ensure_true(KEYS.all? { |key| values[key].is_a?(String) && !values[key].empty? &&
                              values[key] != example.dig('stringData', key) },
                'Keycloak admin Secret contains an invalid or placeholder value')
    ensure_true(values['KC_BOOTSTRAP_ADMIN_PASSWORD'].length >= 8 &&
                !values.values.any? { |value| value.include?("\n") },
                'Keycloak admin Secret password or username is invalid')
    values
  end

  def live_secret
    output = command('Keycloak admin Secret lookup', 'kubectl', '--context', CONTEXT,
                     '--namespace', NAMESPACE, 'get', "secret/#{NAME}",
                     '--ignore-not-found', '--output=json', '--request-timeout=15s')
    output.empty? ? nil : JSON.parse(output)
  end

  def verify_live(secret, expected)
    ensure_true(secret.is_a?(Hash) && secret['kind'] == 'Secret' &&
                secret.dig('metadata', 'name') == NAME &&
                secret.dig('metadata', 'namespace') == NAMESPACE && secret['type'] == 'Opaque',
                'live Keycloak admin Secret identity or type differs')
    labels = YAML.safe_load(EXAMPLE_PATH.read).dig('metadata', 'labels')
    ensure_true(labels.all? { |key, value| secret.dig('metadata', 'labels', key) == value },
                'live Keycloak admin Secret labels differ')
    data = secret['data']
    ensure_true(data.is_a?(Hash) && data.keys.sort == KEYS.sort &&
                data.all? { |key, encoded| Base64.strict_decode64(encoded) == expected.fetch(key) },
                'live Keycloak admin Secret differs from ignored local source')
  rescue ArgumentError
    raise 'live Keycloak admin Secret has invalid encoded data'
  end

  def command(label, *arguments)
    output, _stderr, status = @command.call(*arguments)
    ensure_true(status.success?, "#{label} failed")
    output
  end

  def ensure_true(condition, message)
    raise message unless condition
  end

  def safe_error(exception)
    exception.instance_of?(RuntimeError) ? exception.message : exception.class.to_s
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: keycloak-admin-secret-stage.rb plan|bootstrap|verify' unless ARGV.length == 1
  exit CloudDSPKeycloakAdminSecretStage.new.run(ARGV.first)
end
