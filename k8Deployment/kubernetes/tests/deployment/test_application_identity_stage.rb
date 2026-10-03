require 'minitest/autorun'
require 'fileutils'
require 'json'
require 'pathname'
require 'tmpdir'
require 'yaml'

require_relative '../../scripts/stages/credentials/application-identity-stage'

class ApplicationIdentityStageTest < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  def setup
    @temporary_directory = Dir.mktmpdir('clouddsp-identity-stage')
  end

  def teardown
    FileUtils.remove_entry(@temporary_directory)
  end

  def every_identity
    CloudDSPApplicationIdentityStage::DATABASE_IDENTITIES.map { |name, _config| ['database', name] } +
      CloudDSPApplicationIdentityStage::RABBITMQ_IDENTITIES.map { |name, _config| ['rabbitmq', name] }
  end

  def prepare_stage(kind, identity, command: nil)
    config = (kind == 'database' ? CloudDSPApplicationIdentityStage::DATABASE_IDENTITIES :
      CloudDSPApplicationIdentityStage::RABBITMQ_IDENTITIES).fetch(identity)
    runtime_template = YAML.safe_load(CloudDSPApplicationIdentityStage::ROOT.join(config.fetch(:runtime_example)).read)
    values = runtime_template.fetch('stringData').transform_values.with_index do |value, index|
      value.start_with?('REPLACE_') ? "not-a-real-password-#{index}-for-tests" : value
    end
    source = Marshal.load(Marshal.dump(runtime_template))
    source['stringData'] = values
    path = File.join(@temporary_directory, config.fetch(:runtime_local))
    File.write(path, YAML.dump(source))
    stage = CloudDSPApplicationIdentityStage.new(kind, identity, local_directory: @temporary_directory,
                                                  command: command || method(:successful_command))
    stage.instance_variable_set(:@runtime_example, runtime_template)
    stage.instance_variable_set(:@bootstrap_example,
      YAML.safe_load(CloudDSPApplicationIdentityStage::ROOT.join(config.fetch(:bootstrap_example)).read))
    stage.instance_variable_set(:@runtime_path, Pathname.new(path))
    [stage, values]
  end

  def successful_command(*_arguments, stdin_data: nil)
    ['', '', Status.new(0)]
  end

  def test_all_committed_runtime_contracts_can_build_their_temporary_job_secrets
    every_identity.each do |kind, identity|
      stage, values = prepare_stage(kind, identity)
      runtime = stage.send(:read_runtime_values)
      stage.instance_variable_set(:@runtime_values, runtime)
      bootstrap = stage.send(:build_bootstrap_manifest)
      stage.send(:verify_job_sources)

      assert_equal values, runtime, "#{kind}/#{identity} runtime values"
      assert_equal stage.instance_variable_get(:@config).fetch(:bootstrap_name), bootstrap.dig('metadata', 'name')
      refute bootstrap.fetch('stringData').values.any? { |value| value.start_with?('REPLACE_') }
      assert_equal 'clouddsp-data', bootstrap.dig('metadata', 'namespace')
    end
  end

  def test_database_audit_checks_required_and_forbidden_privileges
    config = CloudDSPApplicationIdentityStage::DATABASE_IDENTITIES.fetch('dispatcher')
    required = config.fetch(:required).each_index.to_h { |index| ["required_#{index}", true] }
    forbidden = config.fetch(:forbidden).each_index.to_h { |index| ["forbidden_#{index}", false] }
    facts = {
      'rolname' => config.fetch(:role), 'rolcanlogin' => true, 'rolsuper' => false,
      'rolcreatedb' => false, 'rolcreaterole' => false, 'rolreplication' => false,
      'rolinherit' => false, 'rolbypassrls' => false, 'can_connect' => true,
      'can_use_schema' => true, 'can_create_schema' => false
    }.merge(required, forbidden)
    command = lambda do |*arguments, stdin_data: nil|
      assert_nil stdin_data
      refute arguments.join(' ').include?('real-password')
      ["#{JSON.generate(facts)}\n", '', Status.new(0)]
    end
    stage, = prepare_stage('database', 'dispatcher', command: command)

    assert_equal :ready, stage.send(:database_state)

    facts['forbidden_0'] = true
    assert_equal :partial, stage.send(:database_state)

    facts['forbidden_0'] = false
    facts['can_create_schema'] = true
    assert_equal :partial, stage.send(:database_state)
  end

  def test_demucs_database_audit_matches_its_reviewed_direct_job_privileges
    config = CloudDSPApplicationIdentityStage::DATABASE_IDENTITIES.fetch('demucs')

    assert_includes config.fetch(:required), [:column, 'public.jobs', 'status', 'UPDATE']
    assert_includes config.fetch(:required), [:column, 'public.jobs', 'revision', 'UPDATE']
    assert_includes config.fetch(:required), [:column, 'public.jobs', 'stems', 'UPDATE']
    assert_includes config.fetch(:required), [:column, 'public.jobs', 'error_message', 'UPDATE']
    assert_includes config.fetch(:forbidden), [:column, 'public.jobs', 'input_object_key', 'UPDATE']
    refute config.fetch(:required).any? { |entry| entry[1].include?('clouddsp_lock_demucs_job_for_claim') }
  end

  def test_rabbitmq_audit_requires_exact_user_tags_and_permissions
    config = CloudDSPApplicationIdentityStage::RABBITMQ_IDENTITIES.fetch('dispatcher')
    command = lambda do |*arguments, stdin_data: nil|
      assert_nil stdin_data
      output = if arguments.include?('list_users')
                 [{ 'user' => config.fetch(:username), 'tags' => config.fetch(:tags) }]
               else
                 [config.fetch(:permissions)]
               end
      [JSON.generate(output), '', Status.new(0)]
    end
    stage, = prepare_stage('rabbitmq', 'dispatcher', command: command)

    assert_equal :ready, stage.send(:rabbitmq_state)
  end

  def test_complete_verification_compares_live_runtime_secret_without_retaining_temporary_secret
    stage, values = prepare_stage('database', 'dispatcher')
    stage.instance_variable_set(:@runtime_values, values)
    example = stage.instance_variable_get(:@runtime_example)
    live = Marshal.load(Marshal.dump(example))
    live['data'] = values.transform_values { |value| Base64.strict_encode64(value) }
    live.delete('stringData')
    live['metadata']['namespace'] = 'clouddsp-app'
    stage.define_singleton_method(:live_secret) do |namespace, _name|
      namespace == 'clouddsp-app' ? live : nil
    end
    stage.define_singleton_method(:database_state) { :ready }
    stage.define_singleton_method(:authenticate_database) { true }

    assert_nil stage.send(:verify_complete_state)

    live['data'][values.keys.last] = Base64.strict_encode64('a-different-password')
    error = assert_raises(RuntimeError) { stage.send(:verify_complete_state) }
    assert_includes error.message, 'differs from ignored local source'
  end
end
