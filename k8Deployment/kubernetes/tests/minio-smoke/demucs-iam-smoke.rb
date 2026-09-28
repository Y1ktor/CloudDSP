#!/usr/bin/env ruby
# Exercise the live Demucs S3 identity without starting a worker or publishing
# an upload notification. A unique object under stems/ is safe test data: the
# MinIO upload notification watches only uploads/. The root key is read from
# the ignored local source solely to remove this fixed probe object afterward.
require 'open3'
require 'pathname'
require 'securerandom'
require 'tmpdir'
require 'yaml'

class DemucsIamSmoke
  ROOT = Pathname.new(File.expand_path('../../..', __dir__)).freeze
  LOCAL = ROOT.join('.local').freeze
  ENDPOINT = 'http://minio.localhost:8080'.freeze
  BUCKET = 'clouddsp-uploads'.freeze

  def initialize(command: Open3.method(:capture3), output: $stdout, error: $stderr)
    @command = command
    @output = output
    @error = error
  end

  def run
    admin = credentials('minio-root-credentials.secret.yaml', 'MINIO_ROOT_USER', 'MINIO_ROOT_PASSWORD')
    worker = credentials('demucs-minio-credentials.secret.yaml', 'DEMUCS_S3_ACCESS_KEY', 'DEMUCS_S3_SECRET_KEY')
    key = "stems/iam-smoke/#{SecureRandom.uuid}.txt"
    denied_key = "midi/iam-smoke/#{SecureRandom.uuid}.txt"
    stem_created = false
    denied_created = false
    failure = nil

    Dir.mktmpdir('clouddsp-demucs-iam-smoke-') do |directory|
      source = File.join(directory, 'source.txt')
      fetched = File.join(directory, 'fetched.txt')
      File.write(source, "clouddsp-demucs-iam-smoke\n")
      begin
        # The Demucs policy grants PutObject/GetObject only for private stems;
        # it grants no DeleteObject, bucket list, or MIDI-prefix write action.
        success!('stem PutObject', worker, 'put-object', '--bucket', BUCKET, '--key', key, '--body', source)
        stem_created = true
        success!('stem GetObject', worker, 'get-object', '--bucket', BUCKET, '--key', key, fetched)
        ensure_true(File.read(fetched) == File.read(source), 'stem readback differs')
        denied = call(worker, 'put-object', '--bucket', BUCKET, '--key', denied_key, '--body', source)
        denied_created = denied.fetch(:status).success?
        deny!('out-of-prefix PutObject', denied)
        deny!('DeleteObject', call(worker, 'delete-object', '--bucket', BUCKET, '--key', key))
        deny!('bucket listing', call(worker, 'list-objects-v2', '--bucket', BUCKET))
      rescue StandardError => exception
        failure = safe_error(exception)
      ensure
        # Cleanup uses the administrator only for these random test keys. If
        # an unexpected broad grant allowed the denied write, remove it too.
        begin
          success!('stem cleanup', admin, 'delete-object', '--bucket', BUCKET, '--key', key) if stem_created
          success!('denied-key cleanup', admin, 'delete-object', '--bucket', BUCKET, '--key', denied_key) if denied_created
        rescue StandardError => exception
          failure = "#{failure}; #{safe_error(exception)}" if failure
          failure ||= safe_error(exception)
        end
      end
    end

    ensure_true(failure.nil?, failure)
    @output.puts 'Demucs MinIO IAM smoke passed: stem put/get allowed; MIDI write, delete, and listing denied; probe cleaned'
    0
  rescue StandardError => exception
    @error.puts "Demucs MinIO IAM smoke stopped: #{safe_error(exception)}"
    1
  end

  private

  def credentials(filename, access_key, secret_key)
    source = YAML.safe_load(LOCAL.join(filename).read).fetch('stringData')
    values = [source.fetch(access_key), source.fetch(secret_key)]
    ensure_true(values.all? { |value| value.is_a?(String) && !value.empty? }, 'ignored credential source is invalid')
    values
  end

  def call(values, *arguments)
    environment = ENV.to_h.merge(
      'AWS_ACCESS_KEY_ID' => values.fetch(0),
      'AWS_SECRET_ACCESS_KEY' => values.fetch(1),
      'AWS_DEFAULT_REGION' => 'us-east-1',
      'AWS_EC2_METADATA_DISABLED' => 'true',
      'AWS_PAGER' => '',
      'AWS_MAX_ATTEMPTS' => '2',
      'AWS_CONFIG_FILE' => '/dev/null',
      'AWS_SHARED_CREDENTIALS_FILE' => '/dev/null'
    )
    environment.delete('AWS_SESSION_TOKEN')
    _stdout, stderr, status = @command.call(environment, 'aws', '--no-cli-pager',
                                             '--endpoint-url', ENDPOINT, 's3api', *arguments, '--output', 'json')
    { status: status, denied: stderr.include?('AccessDenied') }
  end

  def success!(label, values, *arguments)
    ensure_true(call(values, *arguments).fetch(:status).success?, "#{label} failed")
  end

  def deny!(label, result)
    ensure_true(!result.fetch(:status).success? && result.fetch(:denied), "#{label} was not denied")
  end

  def ensure_true(condition, message)
    raise message unless condition
  end

  def safe_error(exception)
    exception.instance_of?(RuntimeError) ? exception.message : exception.class.to_s
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: demucs-iam-smoke.rb (no arguments)' unless ARGV.empty?
  exit DemucsIamSmoke.new.run
end
