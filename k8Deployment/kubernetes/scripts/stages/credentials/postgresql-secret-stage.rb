#!/usr/bin/env ruby
# Create the PostgreSQL runtime Secret before a fresh Helm StatefulSet install.
# The populated manifest lives only in ignored .local configuration. This
# stage reads its values in memory, never prints them, and refuses to replace
# an existing Secret; a separate read-only verify compares it to the source.
require_relative '../../lib/paths'
require 'base64'
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class CloudDSPPostgresqlSecretStage
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-data'.freeze
  NAME = 'clouddsp-postgresql-credentials'.freeze
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  LOCAL_PATH = ROOT.parent.join('.local', 'postgresql-credentials.secret.yaml').freeze
  EXAMPLE_PATH = ROOT.join('services', 'postgresql', 'postgresql-credentials.secret.example.yaml').freeze
  KEYS = %w[POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD].freeze

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
      ensure_true(!live.nil?, 'PostgreSQL credential Secret is absent')
      verify_live(live, expected)
      @output.puts 'PostgreSQL credential Secret verified against ignored local source'
      return 0
    end

    # A partial earlier deployment must be inspected, even if the Secret
    # happens to match. The full fresh bootstrap starts from an empty target.
    ensure_true(live.nil?, 'PostgreSQL credential Secret already exists; use verify or inspect it')
    if mode == 'plan'
      @output.puts 'PostgreSQL credential Secret plan: reviewed local source ready; creation pending'
      return 0
    end

    # Send the ignored manifest to the API server without displaying command
    # output. The dry run validates schema before the one permitted create.
    command('PostgreSQL Secret server dry run', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--dry-run=server', '--filename', @local_path.to_s)
    command('PostgreSQL Secret creation', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--filename', @local_path.to_s)
    created = live_secret
    ensure_true(!created.nil?, 'PostgreSQL Secret create returned without a live Secret')
    verify_live(created, expected)
    @output.puts 'PostgreSQL credential Secret created and verified'
    0
  rescue StandardError => exception
    @error.puts "PostgreSQL credential Secret #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def load_local_source
    ensure_true(@local_path.file?, 'ignored local PostgreSQL credential file is absent')
    source = YAML.safe_load(@local_path.read)
    example = YAML.safe_load(EXAMPLE_PATH.read)
    ensure_true(source.is_a?(Hash) && example.is_a?(Hash), 'PostgreSQL Secret source contract is invalid')
    %w[apiVersion kind metadata type].each do |field|
      ensure_true(source[field] == example[field], 'PostgreSQL Secret source contract differs from example')
    end
    values = source['stringData']
    ensure_true(source['data'].nil? && values.is_a?(Hash) && values.keys.sort == KEYS.sort,
                'PostgreSQL Secret source keys differ from example')
    ensure_true(values.values.all? { |value| value.is_a?(String) && !value.empty? },
                'PostgreSQL Secret source has an empty or invalid value')
    # The example's database and username are illustrative local defaults;
    # the populated ignored file is authoritative for this installation.
    # Only the obvious example password is forbidden.
    ensure_true(values['POSTGRES_PASSWORD'] != example.dig('stringData', 'POSTGRES_PASSWORD'),
                'PostgreSQL Secret source still uses the placeholder password')
    values
  end

  def live_secret
    output = command('PostgreSQL Secret lookup', 'kubectl', '--context', CONTEXT,
                     '--namespace', NAMESPACE, 'get', "secret/#{NAME}",
                     '--ignore-not-found', '--output=json', '--request-timeout=15s')
    output.empty? ? nil : JSON.parse(output)
  end

  def verify_live(secret, expected)
    ensure_true(secret['kind'] == 'Secret' && secret.dig('metadata', 'name') == NAME &&
                secret.dig('metadata', 'namespace') == NAMESPACE && secret['type'] == 'Opaque',
                'live PostgreSQL Secret identity or type differs')
    example_labels = YAML.safe_load(EXAMPLE_PATH.read).dig('metadata', 'labels')
    ensure_true(example_labels.all? { |key, value| secret.dig('metadata', 'labels', key) == value },
                'live PostgreSQL Secret labels differ')
    data = secret['data']
    ensure_true(data.is_a?(Hash) && data.keys.sort == KEYS.sort &&
                data.all? { |key, encoded| Base64.strict_decode64(encoded) == expected.fetch(key) },
                'live PostgreSQL Secret differs from ignored local source')
  rescue ArgumentError
    raise 'live PostgreSQL Secret has invalid encoded data'
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
  abort 'Usage: postgresql-secret-stage.rb plan|bootstrap|verify' unless ARGV.length == 1
  exit CloudDSPPostgresqlSecretStage.new.run(ARGV.first)
end
