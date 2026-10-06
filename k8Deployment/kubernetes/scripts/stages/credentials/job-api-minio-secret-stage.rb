#!/usr/bin/env ruby
# Stage the Job API's restricted MinIO credentials in its application
# namespace. The provisioning Job runs in clouddsp-data and later uses a
# short-lived Secret with identical values; validate that ignored source now,
# but never create or retain the temporary Secret here. MinIO root credentials
# stay solely in the data namespace and never reach the API Pod.
require_relative '../../lib/paths'
require 'base64'
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class CloudDSPJobApiMinioSecretStage
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-app'.freeze
  NAME = 'clouddsp-job-api-minio-credentials'.freeze
  USER = 'clouddsp-job-api'.freeze
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  LOCAL_PATH = ROOT.parent.join('.local', 'job-api-minio-credentials.secret.yaml').freeze
  EXAMPLE_PATH = ROOT.join('services', 'job-api', 'job-api-minio-credentials.secret.example.yaml').freeze
  BOOTSTRAP_LOCAL_PATH = ROOT.parent.join('.local', 'job-api-minio-bootstrap-credentials.secret.yaml').freeze
  BOOTSTRAP_EXAMPLE_PATH = ROOT.join('services', 'minio', 'minio-job-api-uploads-bootstrap-credentials.secret.example.yaml').freeze
  KEYS = %w[JOB_API_S3_ACCESS_KEY JOB_API_S3_SECRET_KEY].freeze

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
      ensure_true(!live.nil?, 'Job API MinIO runtime Secret is absent')
      verify_live(live, expected)
      @output.puts 'Job API MinIO runtime Secret verified against ignored local source'
      return 0
    end

    # A matching live Secret can still be evidence of a partial deployment.
    # Fresh bootstrap does not adopt or rotate it behind the API's back.
    ensure_true(live.nil?, 'Job API MinIO runtime Secret already exists; use verify or inspect it')
    if mode == 'plan'
      @output.puts 'Job API MinIO runtime Secret plan: matching local sources ready; creation pending'
      return 0
    end

    # The API server validates this ignored YAML before the sole write. Both
    # client output streams are suppressed because errors may contain values.
    command('Job API MinIO Secret server dry run', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--dry-run=server', '--filename', @local_path.to_s)
    command('Job API MinIO Secret creation', 'kubectl', '--context', CONTEXT,
            '--namespace', NAMESPACE, 'create', '--filename', @local_path.to_s)
    created = live_secret
    ensure_true(!created.nil?, 'Job API MinIO Secret create returned without a live Secret')
    verify_live(created, expected)
    @output.puts 'Job API MinIO runtime Secret created and verified'
    0
  rescue StandardError => exception
    @error.puts "Job API MinIO runtime Secret #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def load_local_sources
    runtime = read_source(@local_path, EXAMPLE_PATH, 'runtime')
    bootstrap = read_source(@bootstrap_local_path, BOOTSTRAP_EXAMPLE_PATH, 'bootstrap')
    ensure_true(runtime == bootstrap, 'Job API MinIO runtime/bootstrap credentials differ')
    runtime
  end

  def read_source(path, example_path, label)
    ensure_true(path.file?, "ignored local Job API MinIO #{label} credential file is absent")
    source = YAML.safe_load(path.read)
    example = YAML.safe_load(example_path.read)
    ensure_true(source.is_a?(Hash) && example.is_a?(Hash), "Job API MinIO #{label} Secret source contract is invalid")
    %w[apiVersion kind metadata type].each do |field|
      ensure_true(source[field] == example[field], "Job API MinIO #{label} Secret source contract differs from example")
    end
    values = source['stringData']
    ensure_true(source['data'].nil? && values.is_a?(Hash) && values.keys.sort == KEYS.sort,
                "Job API MinIO #{label} Secret source keys differ from example")
    ensure_true(values['JOB_API_S3_ACCESS_KEY'] == USER,
                "Job API MinIO #{label} access key differs from reviewed identity")
    secret = values['JOB_API_S3_SECRET_KEY']
    ensure_true(secret.is_a?(String) && secret.length >= 8 && !secret.include?("\n") &&
                secret != example.dig('stringData', 'JOB_API_S3_SECRET_KEY'),
                "Job API MinIO #{label} Secret uses an invalid or placeholder key")
    values
  end

  def live_secret
    output = command('Job API MinIO Secret lookup', 'kubectl', '--context', CONTEXT,
                     '--namespace', NAMESPACE, 'get', "secret/#{NAME}",
                     '--ignore-not-found', '--output=json', '--request-timeout=15s')
    output.empty? ? nil : JSON.parse(output)
  end

  def verify_live(secret, expected)
    ensure_true(secret['kind'] == 'Secret' && secret.dig('metadata', 'name') == NAME &&
                secret.dig('metadata', 'namespace') == NAMESPACE && secret['type'] == 'Opaque',
                'live Job API MinIO Secret identity or type differs')
    example_labels = YAML.safe_load(EXAMPLE_PATH.read).dig('metadata', 'labels')
    ensure_true(example_labels.all? { |key, value| secret.dig('metadata', 'labels', key) == value },
                'live Job API MinIO Secret labels differ')
    data = secret['data']
    ensure_true(data.is_a?(Hash) && data.keys.sort == KEYS.sort &&
                data.all? { |key, encoded| Base64.strict_decode64(encoded) == expected.fetch(key) },
                'live Job API MinIO Secret differs from ignored local source')
  rescue ArgumentError
    raise 'live Job API MinIO Secret has invalid encoded data'
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
  abort 'Usage: job-api-minio-secret-stage.rb plan|bootstrap|verify' unless ARGV.length == 1
  exit CloudDSPJobApiMinioSecretStage.new.run(ARGV.first)
end
