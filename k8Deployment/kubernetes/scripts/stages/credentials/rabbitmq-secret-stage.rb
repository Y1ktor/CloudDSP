#!/usr/bin/env ruby
# Create the broker administrator Secret before a fresh RabbitMQ StatefulSet
# install. Its populated values live only in ignored .local configuration.
# Creation requires absence; read-only verification compares exact live data
# with the local source in memory without displaying user or password values.
require_relative '../../lib/paths'
require 'base64'
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class CloudDSPRabbitmqSecretStage
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-data'.freeze
  NAME = 'clouddsp-rabbitmq-credentials'.freeze
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  LOCAL_PATH = ROOT.parent.join('.local', 'rabbitmq-credentials.secret.yaml').freeze
  EXAMPLE_PATH = ROOT.join('services', 'rabbitmq', 'rabbitmq-credentials.secret.example.yaml').freeze
  KEYS = %w[RABBITMQ_DEFAULT_USER RABBITMQ_DEFAULT_PASS].freeze

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
      ensure_true(!live.nil?, 'RabbitMQ credential Secret is absent')
      verify_live(live, expected)
      @output.puts 'RabbitMQ credential Secret verified against ignored local source'
      return 0
    end

    # Never replace a Secret on a partial cluster. In RabbitMQ, these values
    # initialize the broker only when its data directory is empty; changing
    # the Secret later would not rotate the durable broker account.
    ensure_true(live.nil?, 'RabbitMQ credential Secret already exists; use verify or inspect it')
    if mode == 'plan'
      @output.puts 'RabbitMQ credential Secret plan: reviewed local source ready; creation pending'
      return 0
    end

    # Suppress kubectl output because the ignored YAML contains administrator
    # credentials. Validate with the server before this one permitted create.
    command('RabbitMQ Secret server dry run', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--dry-run=server', '--filename', @local_path.to_s)
    command('RabbitMQ Secret creation', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--filename', @local_path.to_s)
    created = live_secret
    ensure_true(!created.nil?, 'RabbitMQ Secret create returned without a live Secret')
    verify_live(created, expected)
    @output.puts 'RabbitMQ credential Secret created and verified'
    0
  rescue StandardError => exception
    @error.puts "RabbitMQ credential Secret #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def load_local_source
    ensure_true(@local_path.file?, 'ignored local RabbitMQ credential file is absent')
    source = YAML.safe_load(@local_path.read)
    example = YAML.safe_load(EXAMPLE_PATH.read)
    ensure_true(source.is_a?(Hash) && example.is_a?(Hash), 'RabbitMQ Secret source contract is invalid')
    %w[apiVersion kind metadata type].each do |field|
      ensure_true(source[field] == example[field], 'RabbitMQ Secret source contract differs from example')
    end
    values = source['stringData']
    ensure_true(source['data'].nil? && values.is_a?(Hash) && values.keys.sort == KEYS.sort,
                'RabbitMQ Secret source keys differ from example')
    ensure_true(values.values.all? { |value| value.is_a?(String) && !value.empty? },
                'RabbitMQ Secret source has an empty or invalid value')
    ensure_true(values.all? { |key, value| value != example.fetch('stringData').fetch(key) },
                'RabbitMQ Secret source still uses a placeholder credential')
    ensure_true(values['RABBITMQ_DEFAULT_USER'] != 'guest',
                'RabbitMQ Secret source must not initialize the reserved guest account')
    values
  end

  def live_secret
    output = command('RabbitMQ Secret lookup', 'kubectl', '--context', CONTEXT,
                     '--namespace', NAMESPACE, 'get', "secret/#{NAME}",
                     '--ignore-not-found', '--output=json', '--request-timeout=15s')
    output.empty? ? nil : JSON.parse(output)
  end

  def verify_live(secret, expected)
    ensure_true(secret['kind'] == 'Secret' && secret.dig('metadata', 'name') == NAME &&
                secret.dig('metadata', 'namespace') == NAMESPACE && secret['type'] == 'Opaque',
                'live RabbitMQ Secret identity or type differs')
    example_labels = YAML.safe_load(EXAMPLE_PATH.read).dig('metadata', 'labels')
    ensure_true(example_labels.all? { |key, value| secret.dig('metadata', 'labels', key) == value },
                'live RabbitMQ Secret labels differ')
    data = secret['data']
    ensure_true(data.is_a?(Hash) && data.keys.sort == KEYS.sort &&
                data.all? { |key, encoded| Base64.strict_decode64(encoded) == expected.fetch(key) },
                'live RabbitMQ Secret differs from ignored local source')
  rescue ArgumentError
    raise 'live RabbitMQ Secret has invalid encoded data'
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
  abort 'Usage: rabbitmq-secret-stage.rb plan|bootstrap|verify' unless ARGV.length == 1
  exit CloudDSPRabbitmqSecretStage.new.run(ARGV.first)
end
