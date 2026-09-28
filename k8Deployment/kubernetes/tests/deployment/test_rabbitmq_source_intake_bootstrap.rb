require 'base64'
require 'json'
require 'minitest/autorun'
require 'tmpdir'
require 'yaml'
require_relative '../../scripts/rabbitmq-source-intake-bootstrap'

class RabbitmqSourceIntakeBootstrapTest < Minitest::Test
  MINIO_VALUES = {
    'RABBITMQ_MINIO_EVENTS_USERNAME' => 'clouddsp-minio-events',
    'RABBITMQ_MINIO_EVENTS_PASSWORD' => 'minio-test-password',
    'MINIO_NOTIFY_AMQP_URL_INTAKE' => 'amqp://clouddsp-minio-events:minio-test-password@clouddsp-rabbitmq.clouddsp-data.svc:5672/%2Fclouddsp'
  }.freeze
  UPLOAD_VALUES = {
    'RABBITMQ_UPLOAD_INTAKE_USERNAME' => 'clouddsp-upload-intake',
    'RABBITMQ_UPLOAD_INTAKE_PASSWORD' => 'upload-test-password'
  }.freeze

  class FakeCluster
    attr_reader :calls, :users, :permissions, :temporary_secret

    def initialize(stage, ready: true)
      @stage = stage
      @ready = ready
      @config = ready ? stage.config : nil
      @job = nil
      @temporary_secret = false
      @users = ready ? expected_users : []
      @permissions = ready ? expected_permissions : {}
      @calls = []
    end

    def call(*argv)
      @calls << argv
      verb, name = argv[5], argv[6]
      if verb == 'exec'
        return broker_response(argv)
      elsif verb == 'get'
        return @config ? JSON.generate(@config) : '' if name == "configmap/#{@stage.config_name}"
        return @job ? JSON.generate(@job) : '' if name == "job/#{@stage.job_name}"
        return @temporary_secret ? "secret/#{RabbitmqSourceIntakeBootstrap::TEMP_SECRET}" : '' if name == "secret/#{RabbitmqSourceIntakeBootstrap::TEMP_SECRET}"
        return 'secret/clouddsp-rabbitmq-credentials' if name == 'secret/clouddsp-rabbitmq-credentials'
        return JSON.generate({ 'status' => { 'readyReplicas' => 1 } }) if name == 'statefulset/clouddsp-rabbitmq'
        if name == "secret/#{RabbitmqSourceIntakeBootstrap::MINIO_SECRET}"
          return encoded_secret(MINIO_VALUES)
        end
        if name == "secret/#{RabbitmqSourceIntakeBootstrap::UPLOAD_SECRET}"
          return encoded_secret(UPLOAD_VALUES)
        end
      elsif verb == 'create'
        return 'validated' if argv.include?('--dry-run=server')

        path = argv.last
        if path.end_with?('upload-intake-rabbitmq-bootstrap-credentials.secret.yaml')
          @temporary_secret = true
        elsif path == @stage.config_path.to_s
          @config = @stage.config
        elsif path == @stage.job_path.to_s
          @ready = true
          @users = expected_users
          @permissions = expected_permissions
          @job = { 'status' => { 'conditions' => [{ 'type' => 'Complete', 'status' => 'True' }] } }
        else
          raise "unexpected create source: #{path}"
        end
        return 'created'
      elsif verb == 'wait'
        return 'complete'
      elsif verb == 'delete'
        @temporary_secret = false
        return 'deleted'
      end
      raise "unexpected fake command: #{argv.inspect}"
    end

    private

    def broker_response(argv)
      kind = %w[list_vhosts list_exchanges list_queues list_bindings list_users list_user_permissions].find { |item| argv.include?(item) }
      data = case kind
             when 'list_vhosts' then [{ 'name' => '/clouddsp' }]
             when 'list_exchanges', 'list_queues', 'list_bindings'
               @ready ? @stage.definition.fetch(kind.delete_prefix('list_')) : []
             when 'list_users' then @users
             when 'list_user_permissions' then @permissions.fetch(argv[argv.index(kind) + 1], [])
             else raise "unexpected broker command: #{argv.inspect}"
             end
      JSON.generate(data)
    end

    def encoded_secret(values)
      JSON.generate({ 'data' => values.transform_values { |value| Base64.strict_encode64(value) } })
    end

    def expected_users
      RabbitmqSourceIntakeBootstrap::EXPECTED_PERMISSIONS.keys.map { |name| { 'user' => name, 'tags' => [] } }
    end

    def expected_permissions
      RabbitmqSourceIntakeBootstrap::EXPECTED_PERMISSIONS.transform_values { |permission| [permission.dup] }
    end
  end

  def setup
    @stage = RabbitmqSourceIntakeBootstrap.new.load_stage
  end

  def test_complete_reconcile_authenticates_both_users_without_writing
    with_secrets do |directory|
      fake = FakeCluster.new(@stage)
      checked = []
      out, err = capture_io do
        runner(fake, directory, lambda { |name, password| checked << [name, password]; true }).run('reconcile')
      end

      assert_empty(err)
      assert_includes(out, 'no work pending')
      assert_equal([['clouddsp-minio-events', MINIO_VALUES.fetch('RABBITMQ_MINIO_EVENTS_PASSWORD')],
                    ['clouddsp-upload-intake', UPLOAD_VALUES.fetch('RABBITMQ_UPLOAD_INTAKE_PASSWORD')]], checked)
      refute(fake.calls.any? { |argv| argv.include?('create') || argv.include?('delete') })
    end
  end

  def test_fresh_plan_reports_pending_without_writing
    with_secrets do |directory|
      fake = FakeCluster.new(@stage, ready: false)
      out, err = capture_io { runner(fake, directory).run('plan') }

      assert_empty(err)
      assert_includes(out, 'versioned Job pending')
      refute(fake.calls.any? { |argv| argv.include?('create') })
    end
  end

  def test_fresh_reconcile_creates_temporary_secret_and_removes_it_after_verification
    with_secrets do |directory|
      fake = FakeCluster.new(@stage, ready: false)
      out, err = capture_io { runner(fake, directory).run('reconcile') }

      assert_empty(err)
      assert_includes(out, 'temporary Secret removed')
      refute(fake.temporary_secret)
      writes = fake.calls.select { |argv| %w[create delete].include?(argv[5]) && !argv.include?('--dry-run=server') }
      assert_equal(%w[create create create delete], writes.map { |argv| argv[5] })
      assert(writes[0].last.end_with?('upload-intake-rabbitmq-bootstrap-credentials.secret.yaml'))
      assert_equal(@stage.config_path.to_s, writes[1].last)
      assert_equal(@stage.job_path.to_s, writes[2].last)
    end
  end

  def test_missing_user_blocks_before_writes
    with_secrets do |directory|
      fake = FakeCluster.new(@stage)
      fake.users.pop
      _out, err = capture_io { assert_raises(SystemExit) { runner(fake, directory).run('reconcile') } }

      assert_includes(err, 'topology and users are partially present')
      refute(fake.calls.any? { |argv| argv.include?('create') })
    end
  end

  def test_permission_drift_blocks_before_writes
    with_secrets do |directory|
      fake = FakeCluster.new(@stage)
      fake.permissions.fetch('clouddsp-minio-events').first['write'] = '.*'
      _out, err = capture_io { assert_raises(SystemExit) { runner(fake, directory).run('reconcile') } }

      assert_includes(err, 'broker permissions drifted')
      refute(fake.calls.any? { |argv| argv.include?('create') })
    end
  end

  def test_password_authentication_failure_blocks_ready_state
    with_secrets do |directory|
      fake = FakeCluster.new(@stage)
      _out, err = capture_io do
        assert_raises(SystemExit) { runner(fake, directory, ->(_name, _password) { false }).run('verify') }
      end

      assert_includes(err, 'RabbitMQ authentication failed')
      refute(fake.calls.any? { |argv| argv.include?('create') })
    end
  end

  def test_ignored_bootstrap_secret_mismatch_blocks_before_writes
    with_secrets(bootstrap_values: UPLOAD_VALUES.merge('RABBITMQ_UPLOAD_INTAKE_PASSWORD' => 'wrong-test-password')) do |directory|
      fake = FakeCluster.new(@stage, ready: false)
      _out, err = capture_io { assert_raises(SystemExit) { runner(fake, directory).run('reconcile') } }

      assert_includes(err, 'runtime/bootstrap credentials differ')
      refute(fake.calls.any? { |argv| argv.include?('create') })
    end
  end

  private

  def runner(fake, directory, authenticator = ->(_name, _password) { true })
    RabbitmqSourceIntakeBootstrap.new(runner: fake.method(:call), authenticator: authenticator,
                                      local_directory: directory)
  end

  def with_secrets(bootstrap_values: UPLOAD_VALUES)
    Dir.mktmpdir do |directory|
      [
        ['minio-source-intake-rabbitmq-credentials.secret.yaml', 'clouddsp-minio-source-intake-rabbitmq-credentials', 'clouddsp-data', MINIO_VALUES],
        ['upload-intake-rabbitmq-credentials.secret.yaml', 'clouddsp-upload-intake-rabbitmq-credentials', 'clouddsp-app', UPLOAD_VALUES],
        ['upload-intake-rabbitmq-bootstrap-credentials.secret.yaml', 'clouddsp-upload-intake-rabbitmq-bootstrap-credentials', 'clouddsp-data', bootstrap_values]
      ].each do |filename, name, namespace, values|
        document = { 'kind' => 'Secret', 'metadata' => { 'name' => name, 'namespace' => namespace },
                     'type' => 'Opaque', 'stringData' => values }
        File.write(File.join(directory, filename), YAML.dump(document))
      end
      yield directory
    end
  end
end
