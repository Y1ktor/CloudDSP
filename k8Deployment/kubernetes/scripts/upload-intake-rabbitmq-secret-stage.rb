#!/usr/bin/env ruby
# Create the upload-intake consumer's runtime RabbitMQ Secret before the
# source-intake broker bootstrap. The runtime Secret lives in clouddsp-app;
# the bootstrap Job later uses a temporary clouddsp-data Secret with the same
# values. Validate both ignored local files now, create only the absent
# runtime Secret, and compare its live data without displaying credentials.
require 'base64'
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class CloudDSPUploadIntakeRabbitmqSecretStage
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-app'.freeze
  NAME = 'clouddsp-upload-intake-rabbitmq-credentials'.freeze
  ROOT = Pathname.new(File.expand_path('..', __dir__)).freeze
  LOCAL_PATH = ROOT.parent.join('.local', 'upload-intake-rabbitmq-credentials.secret.yaml').freeze
  EXAMPLE_PATH = ROOT.join('services', 'upload-intake', 'upload-intake-rabbitmq-credentials.secret.example.yaml').freeze
  BOOTSTRAP_LOCAL_PATH = ROOT.parent.join('.local', 'upload-intake-rabbitmq-bootstrap-credentials.secret.yaml').freeze
  BOOTSTRAP_EXAMPLE_PATH = ROOT.join('services', 'rabbitmq', 'rabbitmq-upload-intake-bootstrap-credentials.secret.example.yaml').freeze
  KEYS = %w[RABBITMQ_UPLOAD_INTAKE_USERNAME RABBITMQ_UPLOAD_INTAKE_PASSWORD].freeze

  def initialize(command: Open3.method(:capture3), local_path: LOCAL_PATH,
                 bootstrap_local_path: BOOTSTRAP_LOCAL_PATH, output: $stdout, error: $stderr)
    @command = command
    @local_path = Pathname.new(local_path)
    @bootstrap_local_path = Pathname.new(bootstrap_local_path)
    @output = output
    @error = error
  end

  def run(mode)
    ensure_true(%w[plan bootstrap verify].include?(mode), 'use plan, bootstrap, or verify')
    expected = load_local_sources
    live = live_secret

    if mode == 'verify'
      ensure_true(!live.nil?, 'upload-intake RabbitMQ runtime Secret is absent')
      verify_live(live, expected)
      @output.puts 'upload-intake RabbitMQ runtime Secret verified against ignored local source'
      return 0
    end

    # The broker user and temporary bootstrap Secret have their own guarded
    # lifecycle. An existing runtime Secret is evidence of a partial attempt,
    # even if its values match; never replace it through fresh bootstrap.
    ensure_true(live.nil?, 'upload-intake RabbitMQ runtime Secret already exists; use verify or inspect it')
    if mode == 'plan'
      @output.puts 'upload-intake RabbitMQ runtime Secret plan: reviewed local sources ready; creation pending'
      return 0
    end

    # The API server validates the ignored YAML before creation. Suppress all
    # kubectl output because client errors could include private values.
    command('upload-intake RabbitMQ Secret server dry run', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--dry-run=server', '--filename', @local_path.to_s)
    command('upload-intake RabbitMQ Secret creation', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--filename', @local_path.to_s)
    created = live_secret
    ensure_true(!created.nil?, 'upload-intake RabbitMQ Secret create returned without a live Secret')
    verify_live(created, expected)
    @output.puts 'upload-intake RabbitMQ runtime Secret created and verified'
    0
  rescue StandardError => exception
    @error.puts "upload-intake RabbitMQ runtime Secret #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def load_local_sources
    runtime = read_source(@local_path, EXAMPLE_PATH, 'runtime')
    bootstrap = read_source(@bootstrap_local_path, BOOTSTRAP_EXAMPLE_PATH, 'bootstrap')
    ensure_true(runtime == bootstrap, 'upload-intake RabbitMQ runtime/bootstrap credentials differ')
    runtime
  end

  def read_source(path, example_path, label)
    ensure_true(path.file?, "ignored local upload-intake RabbitMQ #{label} credential file is absent")
    source = YAML.safe_load(path.read)
    example = YAML.safe_load(example_path.read)
    ensure_true(source.is_a?(Hash) && example.is_a?(Hash), "upload-intake RabbitMQ #{label} Secret source contract is invalid")
    %w[apiVersion kind metadata type].each do |field|
      ensure_true(source[field] == example[field], "upload-intake RabbitMQ #{label} Secret source contract differs from example")
    end
    values = source['stringData']
    ensure_true(source['data'].nil? && values.is_a?(Hash) && values.keys.sort == KEYS.sort,
                "upload-intake RabbitMQ #{label} Secret source keys differ from example")
    ensure_true(values.values.all? { |value| value.is_a?(String) && !value.empty? && !value.include?("\n") },
                "upload-intake RabbitMQ #{label} Secret source has an empty or invalid value")
    ensure_true(values['RABBITMQ_UPLOAD_INTAKE_USERNAME'] == example.dig('stringData', 'RABBITMQ_UPLOAD_INTAKE_USERNAME'),
                "upload-intake RabbitMQ #{label} username differs from reviewed identity")
    ensure_true(values['RABBITMQ_UPLOAD_INTAKE_PASSWORD'] != example.dig('stringData', 'RABBITMQ_UPLOAD_INTAKE_PASSWORD'),
                "upload-intake RabbitMQ #{label} Secret source still uses a placeholder password")
    values
  end

  def live_secret
    output = command('upload-intake RabbitMQ Secret lookup', 'kubectl', '--context', CONTEXT,
                     '--namespace', NAMESPACE, 'get', "secret/#{NAME}",
                     '--ignore-not-found', '--output=json', '--request-timeout=15s')
    output.empty? ? nil : JSON.parse(output)
  end

  def verify_live(secret, expected)
    ensure_true(secret['kind'] == 'Secret' && secret.dig('metadata', 'name') == NAME &&
                secret.dig('metadata', 'namespace') == NAMESPACE && secret['type'] == 'Opaque',
                'live upload-intake RabbitMQ Secret identity or type differs')
    example_labels = YAML.safe_load(EXAMPLE_PATH.read).dig('metadata', 'labels')
    ensure_true(example_labels.all? { |key, value| secret.dig('metadata', 'labels', key) == value },
                'live upload-intake RabbitMQ Secret labels differ')
    data = secret['data']
    ensure_true(data.is_a?(Hash) && data.keys.sort == KEYS.sort &&
                data.all? { |key, encoded| Base64.strict_decode64(encoded) == expected.fetch(key) },
                'live upload-intake RabbitMQ Secret differs from ignored local source')
  rescue ArgumentError
    raise 'live upload-intake RabbitMQ Secret has invalid encoded data'
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
  abort 'Usage: upload-intake-rabbitmq-secret-stage.rb plan|bootstrap|verify' unless ARGV.length == 1
  exit CloudDSPUploadIntakeRabbitmqSecretStage.new.run(ARGV.first)
end
