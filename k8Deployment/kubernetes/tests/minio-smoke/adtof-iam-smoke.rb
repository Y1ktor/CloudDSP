#!/usr/bin/env ruby
# Probe the live ADTOF S3 identity with unique private keys. MinIO emits
# processing notifications only for uploads/, so these stems/ and midi/
# objects do not start the worker pipeline or publish broker messages.
require 'open3'
require 'pathname'
require 'securerandom'
require 'tmpdir'
require 'yaml'

class AdtofIamSmoke
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
    worker = credentials('adtof-minio-credentials.secret.yaml', 'ADTOF_S3_ACCESS_KEY', 'ADTOF_S3_SECRET_KEY')
    token = SecureRandom.uuid
    drum_key = "stems/iam-smoke/#{token}/drums.wav"
    other_stem_key = "stems/iam-smoke/#{token}/vocals.wav"
    midi_key = "midi/iam-smoke/#{token}/drums.mid"
    tempo_key = "midi/iam-smoke/#{token}/drums_bpm.json"
    denied_midi_key = "midi/iam-smoke/#{token}/vocals.mid"
    keys = [drum_key, other_stem_key, midi_key, tempo_key, denied_midi_key]
    failure = nil

    Dir.mktmpdir('clouddsp-adtof-iam-smoke-') do |directory|
      source = File.join(directory, 'source.txt')
      fetched = File.join(directory, 'fetched.txt')
      File.write(source, "clouddsp-adtof-iam-smoke\n")
      begin
        # Plant both stem names as root so a denied non-drum read cannot be
        # confused with a missing object. The worker receives no root key.
        success!('drum fixture PutObject', admin, 'put-object', '--bucket', BUCKET, '--key', drum_key, '--body', source)
        success!('other stem fixture PutObject', admin, 'put-object', '--bucket', BUCKET, '--key', other_stem_key, '--body', source)
        success!('drum GetObject', worker, 'get-object', '--bucket', BUCKET, '--key', drum_key, fetched)
        ensure_true(File.read(fetched) == File.read(source), 'drum readback differs')
        deny!('non-drum GetObject', call(worker, 'get-object', '--bucket', BUCKET, '--key', other_stem_key, fetched))

        # Only the two fixed drums output names may be written and verified.
        [midi_key, tempo_key].each do |key|
          success!('drum output PutObject', worker, 'put-object', '--bucket', BUCKET, '--key', key, '--body', source)
          success!('drum output GetObject', worker, 'get-object', '--bucket', BUCKET, '--key', key, fetched)
          ensure_true(File.read(fetched) == File.read(source), 'drum output readback differs')
        end
        deny!('stem PutObject', call(worker, 'put-object', '--bucket', BUCKET, '--key', drum_key, '--body', source))
        deny!('other MIDI PutObject', call(worker, 'put-object', '--bucket', BUCKET, '--key', denied_midi_key, '--body', source))
        deny!('MIDI DeleteObject', call(worker, 'delete-object', '--bucket', BUCKET, '--key', midi_key))
        deny!('bucket listing', call(worker, 'list-objects-v2', '--bucket', BUCKET))
      rescue StandardError => exception
        failure = safe_error(exception)
      ensure
        # S3 deletes are idempotent. Attempt every exact probe key even when
        # an earlier response was lost or a forbidden write unexpectedly
        # succeeded, and report any cleanup failure without exposing keys.
        keys.each do |key|
          begin
            success!('probe cleanup', admin, 'delete-object', '--bucket', BUCKET, '--key', key)
          rescue StandardError => exception
            failure = [failure, safe_error(exception)].compact.join('; ')
          end
        end
      end
    end

    ensure_true(failure.nil?, failure)
    @output.puts 'ADTOF MinIO IAM smoke passed: drums read and fixed MIDI/tempo put/get allowed; other stem read, writes, delete, and listing denied; probes cleaned'
    0
  rescue StandardError => exception
    @error.puts "ADTOF MinIO IAM smoke stopped: #{safe_error(exception)}"
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
  abort 'Usage: adtof-iam-smoke.rb (no arguments)' unless ARGV.empty?
  exit AdtofIamSmoke.new.run
end
