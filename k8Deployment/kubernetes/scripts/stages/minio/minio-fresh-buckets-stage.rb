#!/usr/bin/env ruby
# Create the two fixed MinIO bucket boundaries only during a fresh bootstrap.
# The existing-cluster bucket reconciler deliberately refuses missing buckets:
# an absent bucket there might represent lost durable data. This stage requires
# an empty bucket inventory and a healthy Helm-owned MinIO release before its
# first write, then stops on a partial result for human inspection.
require_relative '../../lib/paths'
require 'rbconfig'
require_relative 'minio-state-verify'

class MinioFreshBucketsStage < MinioStateVerify
  def run(mode)
    ensure_true(%w[plan bootstrap verify].include?(mode), 'use plan, bootstrap, or verify')
    load_source
    verify_release
    credentials = root_credentials
    if mode == 'verify'
      verify_created_boundaries(credentials)
      @output.puts 'MinIO fresh bucket boundaries verified; sample assets and IAM are separate stages'
      return 0
    end

    # Both buckets must be absent. One present bucket is ambiguous partial or
    # recovered state; never fill its missing partner as a purported restore.
    same('fresh MinIO bucket inventory', bucket_names(credentials), [])
    if mode == 'plan'
      @output.puts 'MinIO fresh bucket plan: empty server verified; two private buckets pending'
      return 0
    end

    same('fresh MinIO bucket inventory before creation', bucket_names(credentials), [])
    # S3 buckets are private by default. The shared sample bucket gains its
    # narrow anonymous GetObject grant only after locked assets are mirrored.
    [UPLOAD_BUCKET, SAMPLE_BUCKET].each do |name|
      aws(credentials, 'create-bucket', '--bucket', name)
    end
    verify_created_boundaries(credentials)
    @output.puts 'MinIO fresh bucket bootstrap: two private buckets created and verified'
    0
  rescue StandardError => exception
    @error.puts "MinIO fresh buckets #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def verify_release
    _output, _stderr, status = @command.call(RbConfig.ruby, CloudDSPPaths.script('minio-release.rb').to_s, 'verify')
    ensure_true(status.success?, 'MinIO Helm release verification failed')
  end

  def bucket_names(credentials)
    names = aws(credentials, 'list-buckets').fetch('Buckets').map { |item| item.fetch('Name') }
    ensure_true(names.all? { |name| name.is_a?(String) } && names.uniq.length == names.length,
                'MinIO bucket inventory is invalid')
    names.sort
  end

  def verify_created_boundaries(credentials)
    same('MinIO bucket inventory', bucket_names(credentials), [UPLOAD_BUCKET, SAMPLE_BUCKET].sort)
    [UPLOAD_BUCKET, SAMPLE_BUCKET].each do |name|
      # Verify existence through the bucket endpoint too, rather than relying
      # solely on a server-wide listing that could be stale after creation.
      aws(credentials, 'head-bucket', '--bucket', name)
    end
    same('private uploads bucket policy',
         aws(credentials, 'get-bucket-policy', '--bucket', UPLOAD_BUCKET, missing_policy_ok: true), nil)
    sample_policy_response = aws(credentials, 'get-bucket-policy', '--bucket', SAMPLE_BUCKET,
                                missing_policy_ok: true)
    if sample_policy_response
      same('shared sample bucket policy', normalized_policy(JSON.parse(sample_policy_response.fetch('Policy'))),
           normalized_policy(sample_policy))
    end
    same('shared sample bucket notifications',
         aws(credentials, 'get-bucket-notification-configuration', '--bucket', SAMPLE_BUCKET), {})
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: minio-fresh-buckets-stage.rb plan|bootstrap|verify' unless ARGV.length == 1
  exit MinioFreshBucketsStage.new.run(ARGV.first)
end
