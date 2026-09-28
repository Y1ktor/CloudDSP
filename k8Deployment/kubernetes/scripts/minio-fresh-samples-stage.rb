#!/usr/bin/env ruby
# Fill only the newly created shared MIDI sample bucket. The source mirror
# hashes every upstream download against its reviewed lock before uploading
# and publishes anonymous GetObject only after all expected objects are there.
# This fresh path refuses pre-existing sample objects or a public policy; the
# existing-cluster bucket reconciler remains a separate, non-restoring path.
require 'rbconfig'
require_relative 'minio-state-verify'

class MinioFreshSamplesStage < MinioStateVerify
  MIRROR = SOURCE.join('midi_sample_mirror.py').freeze
  BUCKET_STAGE = ROOT.join('scripts', 'minio-buckets-stage.rb').freeze
  RELEASE_STAGE = ROOT.join('scripts', 'minio-release.rb').freeze

  def run(mode)
    ensure_true(%w[plan bootstrap verify].include?(mode), 'use plan, bootstrap, or verify')
    load_source
    verify_release
    if mode == 'verify'
      verify_mirrored_state
      @output.puts 'MinIO shared samples: locked catalog and narrow browser policy verified'
      return 0
    end

    credentials = root_credentials
    verify_empty_private_sample_bucket(credentials)
    if mode == 'plan'
      @output.puts 'MinIO shared samples plan: empty private bucket ready for locked mirror'
      return 0
    end

    # The Python client checks the same empty/private boundary again before
    # uploading because downloading and hashing 461 upstream files takes time.
    # Its stdout/stderr may include an upstream URL, so this runner reports
    # only a fixed stage label on failure.
    _stdout, _stderr, status = @command.call('python3', MIRROR.to_s, '--fresh-bootstrap')
    ensure_true(status.success?, 'locked MIDI sample mirror failed; inspect partial sample state')
    verify_mirrored_state
    @output.puts 'MinIO shared samples bootstrap: 461 locked objects and browser policy verified'
    0
  rescue StandardError => exception
    @error.puts "MinIO shared samples #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def verify_release
    _stdout, _stderr, status = @command.call(RbConfig.ruby, RELEASE_STAGE.to_s, 'verify')
    ensure_true(status.success?, 'MinIO Helm release verification failed')
  end

  def verify_mirrored_state
    _stdout, _stderr, status = @command.call(RbConfig.ruby, BUCKET_STAGE.to_s, 'verify')
    ensure_true(status.success?, 'locked MIDI sample objects or browser policy failed verification')
  end

  def verify_empty_private_sample_bucket(credentials)
    names = aws(credentials, 'list-buckets').fetch('Buckets').map { |item| item.fetch('Name') }.sort
    same('fresh MinIO bucket set', names, [UPLOAD_BUCKET, SAMPLE_BUCKET].sort)
    same('private uploads bucket policy',
         aws(credentials, 'get-bucket-policy', '--bucket', UPLOAD_BUCKET, missing_policy_ok: true), nil)
    same('private sample bucket policy',
         aws(credentials, 'get-bucket-policy', '--bucket', SAMPLE_BUCKET, missing_policy_ok: true), nil)
    same('fresh sample bucket notifications',
         aws(credentials, 'get-bucket-notification-configuration', '--bucket', SAMPLE_BUCKET), {})
    sample_objects = aws(credentials, 'list-objects-v2', '--bucket', SAMPLE_BUCKET, '--no-paginate')
    ensure_true(Array(sample_objects['Contents']).empty? && sample_objects['IsTruncated'] != true,
                'fresh sample bucket already contains objects')
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: minio-fresh-samples-stage.rb plan|bootstrap|verify' unless ARGV.length == 1
  exit MinioFreshSamplesStage.new.run(ARGV.first)
end
