#!/usr/bin/env ruby
# Create the MinIO-to-RabbitMQ notification Secret before a fresh MinIO
# StatefulSet. The ignored local file supplies a restricted broker username,
# password, and the AMQP URL read by MinIO. Validate that URL against the two
# credential fields, then create only an absent Secret and verify its live
# data without displaying any secret values or URL.
require_relative '../../lib/paths'
require 'base64'
require 'json'
require 'open3'
require 'pathname'
require 'uri'
require 'yaml'

class CloudDSPMinioAmqpSecretStage
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-data'.freeze
  NAME = 'clouddsp-minio-source-intake-rabbitmq-credentials'.freeze
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  LOCAL_PATH = ROOT.parent.join('.local', 'minio-source-intake-rabbitmq-credentials.secret.yaml').freeze
  EXAMPLE_PATH = ROOT.join('services', 'minio', 'minio-source-intake-rabbitmq-credentials.secret.example.yaml').freeze
  KEYS = %w[RABBITMQ_MINIO_EVENTS_USERNAME RABBITMQ_MINIO_EVENTS_PASSWORD MINIO_NOTIFY_AMQP_URL_INTAKE].freeze

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
      ensure_true(!live.nil?, 'MinIO AMQP credential Secret is absent')
      verify_live(live, expected)
      @output.puts 'MinIO AMQP credential Secret verified against ignored local source'
      return 0
    end

    # A matching Secret on a partial cluster still blocks fresh creation.
    # Reusing a broker password without verifying its durable user account
    # belongs to the separate source-intake topology stage.
    ensure_true(live.nil?, 'MinIO AMQP credential Secret already exists; use verify or inspect it')
    if mode == 'plan'
      @output.puts 'MinIO AMQP credential Secret plan: reviewed local source ready; creation pending'
      return 0
    end

    # Suppress kubectl output because its error text could contain the AMQP
    # URL. The API server validates the ignored YAML before the one create.
    command('MinIO AMQP Secret server dry run', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--dry-run=server', '--filename', @local_path.to_s)
    command('MinIO AMQP Secret creation', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--filename', @local_path.to_s)
    created = live_secret
    ensure_true(!created.nil?, 'MinIO AMQP Secret create returned without a live Secret')
    verify_live(created, expected)
    @output.puts 'MinIO AMQP credential Secret created and verified'
    0
  rescue StandardError => exception
    @error.puts "MinIO AMQP credential Secret #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def load_local_source
    ensure_true(@local_path.file?, 'ignored local MinIO AMQP credential file is absent')
    source = YAML.safe_load(@local_path.read)
    example = YAML.safe_load(EXAMPLE_PATH.read)
    ensure_true(source.is_a?(Hash) && example.is_a?(Hash), 'MinIO AMQP Secret source contract is invalid')
    %w[apiVersion kind metadata type].each do |field|
      ensure_true(source[field] == example[field], 'MinIO AMQP Secret source contract differs from example')
    end
    values = source['stringData']
    ensure_true(source['data'].nil? && values.is_a?(Hash) && values.keys.sort == KEYS.sort,
                'MinIO AMQP Secret source keys differ from example')
    ensure_true(values.values.all? { |value| value.is_a?(String) && !value.empty? && !value.include?("\n") },
                'MinIO AMQP Secret source has an empty or invalid value')
    ensure_true(values['RABBITMQ_MINIO_EVENTS_USERNAME'] == example.dig('stringData', 'RABBITMQ_MINIO_EVENTS_USERNAME'),
                'MinIO AMQP broker username differs from reviewed identity')
    ensure_true(%w[RABBITMQ_MINIO_EVENTS_PASSWORD MINIO_NOTIFY_AMQP_URL_INTAKE].all? { |key|
                  values.fetch(key) != example.fetch('stringData').fetch(key)
                }, 'MinIO AMQP Secret source still uses a placeholder credential')
    verify_uri(values)
    values
  end

  def verify_uri(values)
    uri = URI.parse(values.fetch('MINIO_NOTIFY_AMQP_URL_INTAKE'))
    ensure_true(uri.scheme == 'amqp' && uri.host == 'clouddsp-rabbitmq.clouddsp-data.svc' &&
                uri.port == 5672 && uri.path == '/%2Fclouddsp' && uri.query.nil? && uri.fragment.nil? &&
                URI::DEFAULT_PARSER.unescape(uri.user.to_s) == values.fetch('RABBITMQ_MINIO_EVENTS_USERNAME') &&
                URI::DEFAULT_PARSER.unescape(uri.password.to_s) == values.fetch('RABBITMQ_MINIO_EVENTS_PASSWORD'),
                'MinIO AMQP URL disagrees with its broker credential contract')
  rescue URI::InvalidURIError
    raise 'MinIO AMQP URL is invalid'
  end

  def live_secret
    output = command('MinIO AMQP Secret lookup', 'kubectl', '--context', CONTEXT,
                     '--namespace', NAMESPACE, 'get', "secret/#{NAME}",
                     '--ignore-not-found', '--output=json', '--request-timeout=15s')
    output.empty? ? nil : JSON.parse(output)
  end

  def verify_live(secret, expected)
    ensure_true(secret['kind'] == 'Secret' && secret.dig('metadata', 'name') == NAME &&
                secret.dig('metadata', 'namespace') == NAMESPACE && secret['type'] == 'Opaque',
                'live MinIO AMQP Secret identity or type differs')
    example_labels = YAML.safe_load(EXAMPLE_PATH.read).dig('metadata', 'labels')
    ensure_true(example_labels.all? { |key, value| secret.dig('metadata', 'labels', key) == value },
                'live MinIO AMQP Secret labels differ')
    data = secret['data']
    ensure_true(data.is_a?(Hash) && data.keys.sort == KEYS.sort &&
                data.all? { |key, encoded| Base64.strict_decode64(encoded) == expected.fetch(key) },
                'live MinIO AMQP Secret differs from ignored local source')
  rescue ArgumentError
    raise 'live MinIO AMQP Secret has invalid encoded data'
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
  abort 'Usage: minio-amqp-secret-stage.rb plan|bootstrap|verify' unless ARGV.length == 1
  exit CloudDSPMinioAmqpSecretStage.new.run(ARGV.first)
end
