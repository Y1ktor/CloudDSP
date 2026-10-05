#!/usr/bin/env ruby
# Reconcile the narrow public-read policy on the existing shared MIDI sample
# bucket. Both bucket names and the private uploads boundary must already be
# present; a missing bucket may mean lost durable data and is never recreated
# as an empty replacement by an existing-cluster reconcile.
#
# Before granting anonymous reads, compare every sample key and size with the
# reviewed asset lock. The policy document contains no credential and is sent
# directly to MinIO's S3 API by this versioned, non-interactive script. A
# different policy, unexpected bucket/object, or existing upload grant stops.
require_relative '../../lib/paths'
require_relative 'minio-state-verify'

class MinioBucketsStage < MinioStateVerify
  ASSET_LOCK = SOURCE.join('midi-sample-assets.lock.json').freeze

  def run(mode)
    ensure_true(%w[plan verify reconcile].include?(mode), 'use plan, verify, or reconcile')
    load_source
    credentials = root_credentials
    expected_objects = sample_objects_from_lock
    state = read_state(credentials, expected_objects)
    if state == :ready
      @output.puts "MinIO buckets #{mode}: both boundaries and all locked sample keys verified; no work pending"
      return 0
    end

    @output.puts 'MinIO buckets: shared-sample read policy absent; exact policy pending'
    return 0 if mode == 'plan'

    ensure_true(mode == 'reconcile', 'shared-sample read policy is incomplete')
    ensure_true(read_state(credentials, expected_objects) == :policy_absent,
                'shared-sample policy changed during preflight')
    # JSON policy is committed, public authorization configuration. Root S3
    # credentials stay in the child process environment, never in argv/logs.
    aws(credentials, 'put-bucket-policy', '--bucket', SAMPLE_BUCKET,
        '--policy', JSON.generate(sample_policy))
    ensure_true(read_state(credentials, expected_objects) == :ready,
                'shared-sample policy write did not produce the reviewed boundary')
    @output.puts 'MinIO buckets reconcile: narrow shared-sample read policy restored and verified'
    0
  rescue StandardError => exception
    @error.puts "MinIO buckets #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def sample_objects_from_lock
    lock = JSON.parse(ASSET_LOCK.read)
    assets = lock.fetch('assets')
    same('source sample bucket', lock['bucket'], SAMPLE_BUCKET)
    same('source sample asset count', lock['asset_count'], 461)
    same('source sample catalog length', assets.length, lock['asset_count'])
    ensure_true(assets.all? do |key, item|
      key.is_a?(String) && key.match?(%r{\A(?:piano/|soundfonts/FluidR3_GM/|drums/)[^\n]+\z}) &&
        !key.split('/').include?('..') && item['bytes'].is_a?(Integer) && item['bytes'].positive? &&
        item['sha256'].is_a?(String) && item['sha256'].match?(/\A[0-9a-f]{64}\z/)
    end, 'source sample asset lock has invalid entries')
    assets.to_h { |key, item| [key, item.fetch('bytes')] }
  end

  def read_state(credentials, expected_objects)
    names = aws(credentials, 'list-buckets').fetch('Buckets').map { |item| item.fetch('Name') }.sort
    # Refuse a missing private bucket rather than hiding potential user-data
    # loss with an empty replacement. An extra bucket also needs ownership
    # review before this script can reason about the server's public boundary.
    same('MinIO bucket set', names, [UPLOAD_BUCKET, SAMPLE_BUCKET].sort)
    same('private uploads bucket policy',
         aws(credentials, 'get-bucket-policy', '--bucket', UPLOAD_BUCKET, missing_policy_ok: true), nil)
    same('shared-sample notifications',
         aws(credentials, 'get-bucket-notification-configuration', '--bucket', SAMPLE_BUCKET), {})
    verify_sample_objects(credentials, expected_objects)
    policy = aws(credentials, 'get-bucket-policy', '--bucket', SAMPLE_BUCKET, missing_policy_ok: true)
    return :policy_absent if policy.nil?

    same('anonymous shared-sample policy', normalized_policy(JSON.parse(policy.fetch('Policy'))),
         normalized_policy(sample_policy))
    :ready
  end

  def verify_sample_objects(credentials, expected)
    actual = {}
    token = nil
    loop do
      arguments = ['list-objects-v2', '--bucket', SAMPLE_BUCKET, '--no-paginate']
      arguments.concat(['--continuation-token', token]) if token
      page = aws(credentials, *arguments)
      Array(page['Contents']).each do |item|
        key = item.fetch('Key')
        ensure_true(!actual.key?(key), 'shared-sample listing repeated an object key')
        actual[key] = item.fetch('Size')
      end
      break unless page['IsTruncated'] == true

      next_token = page['NextContinuationToken']
      ensure_true(next_token.is_a?(String) && !next_token.empty? && next_token != token,
                  'shared-sample listing pagination is invalid')
      token = next_token
    end
    same('shared-sample locked key and size inventory', actual, expected)
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: minio-buckets-stage.rb plan|verify|reconcile' unless ARGV.length == 1
  exit MinioBucketsStage.new.run(ARGV.first)
end
