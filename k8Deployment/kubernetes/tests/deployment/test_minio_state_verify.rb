require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/stages/minio/minio-state-verify'

class MinioStateVerifyTest < Minitest::Test
  FakeStatus = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  def setup
    @verifier = MinioStateVerify.new(output: StringIO.new, error: StringIO.new)
  end

  def test_committed_policy_set_is_versioned_and_scoped_to_private_uploads
    policies = @verifier.load_source

    assert_equal MinioStateVerify::POLICY_USERS.keys.sort, policies.keys.sort
    assert policies.values.all? { |entry| entry.fetch(:policy).fetch('Version') == '2012-10-17' }
    assert policies.values.all? do |entry|
      entry.fetch(:policy).fetch('Statement').all? do |statement|
        Array(statement.fetch('Resource')).all? { |resource| resource.start_with?('arn:aws:s3:::clouddsp-uploads') }
      end
    end
    assert_equal 5, MinioStateVerify::POLICY_USERS.values.uniq.length
  end

  def test_policy_normalization_accepts_minio_array_and_principal_shapes
    source = { 'Statement' => [{ 'Principal' => '*', 'Action' => 's3:GetObject', 'Resource' => 'bucket/*' }] }
    live = { 'Statement' => [{ 'Resource' => ['bucket/*'], 'Action' => ['s3:GetObject'],
                               'Principal' => { 'AWS' => ['*'] } }] }

    assert_equal @verifier.send(:normalized_policy, source), @verifier.send(:normalized_policy, live)
  end

  def test_admin_client_keeps_credentials_out_of_argv_and_uses_ephemeral_config
    calls = []
    command = lambda do |*argv, **options|
      calls << [argv, options]
      [JSON.generate('status' => 'success'), '', FakeStatus.new(0)]
    end
    verifier = MinioStateVerify.new(command: command, output: StringIO.new, error: StringIO.new)

    verifier.send(:mc, { user: 'admin-user', password: 'sensitive-password', ip: '10.43.0.1' },
                  'policy', 'info', 'audit', 'policy-name')

    pull_argv, = calls.fetch(0)
    assert_equal ['docker', 'exec', MinioStateVerify::NODE, 'ctr', '-n', 'k8s.io',
                  'images', 'pull', '--plain-http', MinioStateVerify::MC_NODE_IMAGE], pull_argv
    argv, options = calls.fetch(1)
    refute_includes argv.join(' '), 'sensitive-password'
    assert_includes options.fetch(:stdin_data), 'sensitive-password'
    assert_includes argv, '--rm'
    assert_includes argv, '--read-only'
    assert_includes argv, 'type=tmpfs,dst=/mc-config,options=rw'
    assert_includes argv, MinioStateVerify::MC_NODE_IMAGE
  end

  def test_failed_admin_call_reports_only_operation_label
    command = lambda do |*_argv, **_options|
      @calls ||= 0
      @calls += 1
      if @calls == 1
        ['', '', FakeStatus.new(0)]
      else
        ['', 'sensitive-password in client stderr', FakeStatus.new(1)]
      end
    end
    verifier = MinioStateVerify.new(command: command, output: StringIO.new, error: StringIO.new)

    error = assert_raises(RuntimeError) do
      verifier.send(:mc, { user: 'admin-user', password: 'sensitive-password', ip: '10.43.0.1' },
                    'policy', 'info', 'audit', 'policy-name')
    end
    assert_equal 'MinIO admin policy info failed', error.message
  end

  def test_unexpected_notification_filter_stops_verification_without_echoing_configuration
    sample_policy = {
      'Version' => '2012-10-17',
      'Statement' => [{ 'Sid' => 'BrowserReadSharedMidiSamplesOnly', 'Effect' => 'Allow',
                        'Principal' => '*', 'Action' => 's3:GetObject',
                        'Resource' => 'arn:aws:s3:::clouddsp-midi-samples/*' }]
    }
    @verifier.define_singleton_method(:aws) do |_credentials, operation, *arguments, **_options|
      case [operation, arguments]
      when ['list-buckets', []]
        { 'Buckets' => %w[clouddsp-uploads clouddsp-midi-samples].map { |name| { 'Name' => name } } }
      when ['get-bucket-policy', ['--bucket', 'clouddsp-uploads']]
        nil
      when ['get-bucket-policy', ['--bucket', 'clouddsp-midi-samples']]
        { 'Policy' => JSON.generate(sample_policy) }
      when ['get-bucket-notification-configuration', ['--bucket', 'clouddsp-midi-samples']]
        {}
      when ['get-bucket-notification-configuration', ['--bucket', 'clouddsp-uploads']]
        { 'QueueConfigurations' => [{ 'QueueArn' => MinioStateVerify::NOTIFICATION_ARN,
                                      'Events' => ['s3:ObjectCreated:*'],
                                      'Filter' => { 'Key' => { 'FilterRules' => [
                                        { 'Name' => 'prefix', 'Value' => 'uploads/' },
                                        { 'Name' => 'suffix', 'Value' => '.secret' }
                                      ] } } }] }
      else
        raise 'unexpected S3 test call'
      end
    end

    error = assert_raises(RuntimeError) { @verifier.send(:verify_buckets, {}) }
    assert_equal 'private upload notification filter drifted', error.message
    refute_includes error.message, '.secret'
  end

  def test_absent_notification_is_distinct_from_unexpected_notification_state
    response = {}
    @verifier.define_singleton_method(:aws) { |_credentials, *_arguments| response }

    assert_equal :absent, @verifier.send(:upload_notification_state, {})
    response = { 'QueueConfigurations' => [] }
    assert_equal :absent, @verifier.send(:upload_notification_state, {})
    response = { 'TopicConfigurations' => [] }
    assert_raises(RuntimeError) { @verifier.send(:upload_notification_state, {}) }
  end
end
