#!/usr/bin/env ruby
# Exercise the live Basic Pitch S3 identity with disposable private objects.
# stems/ and midi/ do not trigger the MinIO notification, which watches only
# uploads/. This smoke does not start a worker or publish processing messages.
require 'open3'
require 'pathname'
require 'securerandom'
require 'tmpdir'
require 'yaml'

class BasicPitchIamSmoke
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
    worker = credentials('basic-pitch-minio-credentials.secret.yaml', 'BASIC_PITCH_S3_ACCESS_KEY', 'BASIC_PITCH_S3_SECRET_KEY')
    token = SecureRandom.uuid
    stem_key = "stems/iam-smoke/#{token}.txt"
    midi_key = "midi/iam-smoke/#{token}.txt"
    denied_key = "stems/iam-smoke/#{token}-denied.txt"
    failure = nil

    Dir.mktmpdir('clouddsp-basic-pitch-iam-smoke-') do |directory|
      source = File.join(directory, 'source.txt')
      stem_readback = File.join(directory, 'stem.txt')
      midi_readback = File.join(directory, 'midi.txt')
      File.write(source, "clouddsp-basic-pitch-iam-smoke\n")
      begin
        # The administrator plants only this random stem fixture. The worker
        # must read it but must not be able to write any private stem.
        success!('stem fixture PutObject', admin, 'put-object', '--bucket', BUCKET, '--key', stem_key, '--body', source)
        success!('stem GetObject', worker, 'get-object', '--bucket', BUCKET, '--key', stem_key, stem_readback)
        ensure_true(File.read(stem_readback) == File.read(source), 'stem readback differs')

        # Basic Pitch can write and verify private MIDI, but it has no
        # DeleteObject or ListBucket grant. Check failures are AccessDenied,
        # rather than a transport error or a missing fixture.
        success!('MIDI PutObject', worker, 'put-object', '--bucket', BUCKET, '--key', midi_key, '--body', source)
        success!('MIDI GetObject', worker, 'get-object', '--bucket', BUCKET, '--key', midi_key, midi_readback)
        ensure_true(File.read(midi_readback) == File.read(source), 'MIDI readback differs')
        denied = call(worker, 'put-object', '--bucket', BUCKET, '--key', denied_key, '--body', source)
        deny!('stem PutObject', denied)
        deny!('MIDI DeleteObject', call(worker, 'delete-object', '--bucket', BUCKET, '--key', midi_key))
        deny!('bucket listing', call(worker, 'list-objects-v2', '--bucket', BUCKET))
      rescue StandardError => exception
        failure = safe_error(exception)
      ensure
        # Delete all three unique keys even if a write response was lost or
        # a forbidden write unexpectedly succeeded. S3 delete is idempotent;
        # each attempt runs even if an earlier cleanup encounters an error.
        [stem_key, midi_key, denied_key].each do |key|
          begin
            success!('probe cleanup', admin, 'delete-object', '--bucket', BUCKET, '--key', key)
          rescue StandardError => exception
            failure = [failure, safe_error(exception)].compact.join('; ')
          end
        end
      end
    end

    ensure_true(failure.nil?, failure)
    @output.puts 'Basic Pitch MinIO IAM smoke passed: stem read and MIDI put/get allowed; stem write, delete, and listing denied; probes cleaned'
    0
  rescue StandardError => exception
    @error.puts "Basic Pitch MinIO IAM smoke stopped: #{safe_error(exception)}"
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
  abort 'Usage: basic-pitch-iam-smoke.rb (no arguments)' unless ARGV.empty?
  exit BasicPitchIamSmoke.new.run
end
