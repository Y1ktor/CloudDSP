require 'json'
require 'minitest/autorun'
require 'stringio'
require 'tmpdir'
require 'yaml'

require_relative '../../scripts/minio-adtof-iam-stage'

class MinioAdtofIamStageTest < Minitest::Test
  # This fake models only the resources owned by this stage. It keeps the
  # ordering and failure behavior visible without touching the retained k3d
  # cluster's already-provisioned MinIO user.
  class FakeStage < MinioAdtofIamStage
    attr_accessor :fail_job, :temporary_matches
    attr_reader :writes, :resources, :policy_present, :user_policies

    def initialize
      super(output: StringIO.new, error: StringIO.new)
      @writes = []
      @resources = {}
      @policy_present = false
      @user_policies = []
      @temporary_matches = true
    end

    def verify_prerequisites
      true
    end

    def root_credentials
      {}
    end

    def resource(kind, name)
      @resources[[kind, name]]
    end

    def mc_optional(_credentials, _missing_code, kind, _operation, _alias, _name)
      (kind == 'user' ? !@user_policies.empty? : @policy_present) ? { 'status' => 'success' } : nil
    end

    def verify_temporary_secret(secret)
      raise 'temporary Secret is absent or different' unless secret && @temporary_matches
    end

    def verify_configmap(policy)
      raise 'policy ConfigMap missing' unless resource('configmap', policy.fetch(:name))
    end

    def verify_job_if_present
      true
    end

    def verify_iam_subset(_credentials, _policy)
      raise 'IAM policy or user missing' unless @policy_present && @user_policies == [POLICY]
    end

    def kubectl_write(*arguments)
      @writes << arguments
      return if arguments.include?('--dry-run=server')

      case arguments.first
      when 'create'
        path = arguments.fetch(2)
        if path == TEMP_SOURCE.to_s
          @resources[['secret', TEMP_SECRET]] = { 'kind' => 'Secret' }
        else
          document = YAML.safe_load(File.read(path), aliases: true)
          @resources[[document.fetch('kind').downcase, document.fetch('metadata').fetch('name')]] = document
        end
      when 'wait'
        raise 'simulated Job failure' if @fail_job

        @policy_present = true
        @user_policies = [POLICY]
      when 'delete'
        @resources.delete(['secret', TEMP_SECRET])
      end
    end
  end

  def test_versioned_job_keeps_expected_commands_secret_boundary_and_policy
    stage = FakeStage.new
    policy = stage.load_source.fetch(FakeStage::POLICY)

    stage.send(:validate_job, policy)
    assert_equal 'clouddsp-adtof-artifacts-policy-v001', policy.fetch(:name)
  end

  def test_job_preflight_rejects_regressed_policy_association_memory
    stage = FakeStage.new
    policy = stage.load_source.fetch(FakeStage::POLICY)
    source = File.read(stage.send(:job_path))
    assert_includes source, 'memory: 256Mi'

    Dir.mktmpdir('clouddsp-adtof-iam-job-test-') do |directory|
      path = File.join(directory, 'job.yaml')
      File.write(path, source.sub('memory: 256Mi', 'memory: 128Mi'))
      stage.define_singleton_method(:job_path) { path }
      error = assert_raises(RuntimeError) { stage.send(:validate_job, policy) }
      assert_includes error.message, 'policy association memory limit'
    end
  end

  def test_bootstrap_orders_one_job_and_removes_temporary_secret_last
    stage = FakeStage.new

    assert_equal 0, stage.run('plan')
    assert_empty stage.writes
    assert_equal 0, stage.run('bootstrap')
    writes = stage.writes.reject { |args| args.include?('--dry-run=server') }
    assert_equal ['create', '--filename', FakeStage::TEMP_SOURCE.to_s], writes.first
    assert_equal ["job/#{FakeStage::JOB}"], writes.select { |args| args.first == 'wait' }.map { |args| args.fetch(2) }
    assert_equal ['delete', "secret/#{FakeStage::TEMP_SECRET}", '--wait=true'], writes.last
    assert_nil stage.resource('secret', FakeStage::TEMP_SECRET)
    assert_equal 0, stage.run('verify')
  end

  def test_existing_iam_or_kubernetes_resource_blocks_all_writes
    with_user = FakeStage.new
    with_user.user_policies << FakeStage::POLICY
    with_config = FakeStage.new
    with_config.resources[['configmap', 'clouddsp-adtof-artifacts-policy-v001']] = { 'kind' => 'ConfigMap' }
    [with_user, with_config].each do |stage|
      assert_equal 1, stage.run('bootstrap')
      assert_empty stage.writes
    end
  end

  def test_failed_job_preserves_partial_state_and_temporary_secret
    stage = FakeStage.new
    stage.fail_job = true

    assert_equal 1, stage.run('bootstrap')
    assert stage.resource('secret', FakeStage::TEMP_SECRET)
    refute stage.writes.any? { |args| args.first == 'delete' }
    assert_equal 1, stage.run('bootstrap')
  end

  def test_reconcile_removes_only_matching_temporary_secret_after_complete_iam
    stage = FakeStage.new
    assert_equal 0, stage.run('bootstrap')
    stage.resources[['secret', FakeStage::TEMP_SECRET]] = { 'kind' => 'Secret' }
    stage.temporary_matches = false
    before = stage.writes.length
    assert_equal 1, stage.run('reconcile')
    assert_equal before, stage.writes.length

    stage.temporary_matches = true
    assert_equal 0, stage.run('reconcile')
    assert_equal ['delete', "secret/#{FakeStage::TEMP_SECRET}", '--wait=true'], stage.writes.last
    assert_nil stage.resource('secret', FakeStage::TEMP_SECRET)
  end

  def test_unknown_admin_error_is_never_classified_as_absent
    stage = MinioAdtofIamStage.new(output: StringIO.new, error: StringIO.new)
    status = Struct.new(:success?).new(false)
    stage.define_singleton_method(:mc_raw) do |_credentials, *_arguments|
      [JSON.generate('status' => 'error', 'error' => { 'cause' => { 'error' => { 'Code' => 'AccessDenied' } } }), status]
    end

    error = assert_raises(RuntimeError) do
      stage.send(:mc_optional, {}, 'XMinioAdminNoSuchUser', 'user', 'info', 'audit', 'missing')
    end
    assert_equal 'MinIO admin user info failed', error.message
  end
end
