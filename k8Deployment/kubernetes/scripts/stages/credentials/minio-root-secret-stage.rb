#!/usr/bin/env ruby
# Create the MinIO root administrator Secret before a fresh StatefulSet install.
# The populated values stay in ignored .local configuration. This stage
# refuses to replace an existing Secret and compares live data to that source
# in memory, without displaying the administrator username or password.
require_relative '../../lib/paths'
require 'base64'
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class CloudDSPMinioRootSecretStage
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-data'.freeze
  NAME = 'clouddsp-minio-root-credentials'.freeze
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  LOCAL_PATH = ROOT.parent.join('.local', 'minio-root-credentials.secret.yaml').freeze
  EXAMPLE_PATH = ROOT.join('services', 'minio', 'minio-root-credentials.secret.example.yaml').freeze
  KEYS = %w[MINIO_ROOT_USER MINIO_ROOT_PASSWORD].freeze

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
      ensure_true(!live.nil?, 'MinIO root credential Secret is absent')
      verify_live(live, expected)
      @output.puts 'MinIO root credential Secret verified against ignored local source'
      return 0
    end

    # A partial deployment must remain inspectable. MinIO initializes its
    # root credentials against persistent state; this fresh-cluster stage
    # cannot safely treat a matching existing Secret as permission to retry.
    ensure_true(live.nil?, 'MinIO root credential Secret already exists; use verify or inspect it')
    if mode == 'plan'
      @output.puts 'MinIO root credential Secret plan: reviewed local source ready; creation pending'
      return 0
    end

    # Suppress kubectl output: Kubernetes echoes the Secret name but client
    # errors could contain source details. Validate server schema before the
    # one permitted create, then re-read and compare the live Secret.
    command('MinIO root Secret server dry run', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--dry-run=server', '--filename', @local_path.to_s)
    command('MinIO root Secret creation', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--filename', @local_path.to_s)
    created = live_secret
    ensure_true(!created.nil?, 'MinIO root Secret create returned without a live Secret')
    verify_live(created, expected)
    @output.puts 'MinIO root credential Secret created and verified'
    0
  rescue StandardError => exception
    @error.puts "MinIO root credential Secret #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def load_local_source
    ensure_true(@local_path.file?, 'ignored local MinIO root credential file is absent')
    source = YAML.safe_load(@local_path.read)
    example = YAML.safe_load(EXAMPLE_PATH.read)
    ensure_true(source.is_a?(Hash) && example.is_a?(Hash), 'MinIO root Secret source contract is invalid')
    %w[apiVersion kind metadata type].each do |field|
      ensure_true(source[field] == example[field], 'MinIO root Secret source contract differs from example')
    end
    values = source['stringData']
    ensure_true(source['data'].nil? && values.is_a?(Hash) && values.keys.sort == KEYS.sort,
                'MinIO root Secret source keys differ from example')
    ensure_true(values.values.all? { |value| value.is_a?(String) && !value.empty? && !value.include?("\n") },
                'MinIO root Secret source has an empty or invalid value')
    ensure_true(values.all? { |key, value| value != example.fetch('stringData').fetch(key) },
                'MinIO root Secret source still uses a placeholder credential')
    values
  end

  def live_secret
    output = command('MinIO root Secret lookup', 'kubectl', '--context', CONTEXT,
                     '--namespace', NAMESPACE, 'get', "secret/#{NAME}",
                     '--ignore-not-found', '--output=json', '--request-timeout=15s')
    output.empty? ? nil : JSON.parse(output)
  end

  def verify_live(secret, expected)
    ensure_true(secret['kind'] == 'Secret' && secret.dig('metadata', 'name') == NAME &&
                secret.dig('metadata', 'namespace') == NAMESPACE && secret['type'] == 'Opaque',
                'live MinIO root Secret identity or type differs')
    example_labels = YAML.safe_load(EXAMPLE_PATH.read).dig('metadata', 'labels')
    ensure_true(example_labels.all? { |key, value| secret.dig('metadata', 'labels', key) == value },
                'live MinIO root Secret labels differ')
    data = secret['data']
    ensure_true(data.is_a?(Hash) && data.keys.sort == KEYS.sort &&
                data.all? { |key, encoded| Base64.strict_decode64(encoded) == expected.fetch(key) },
                'live MinIO root Secret differs from ignored local source')
  rescue ArgumentError
    raise 'live MinIO root Secret has invalid encoded data'
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
  abort 'Usage: minio-root-secret-stage.rb plan|bootstrap|verify' unless ARGV.length == 1
  exit CloudDSPMinioRootSecretStage.new.run(ARGV.first)
end
