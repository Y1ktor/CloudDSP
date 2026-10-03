require 'json'
require 'minitest/autorun'
require 'stringio'
require 'yaml'

require_relative '../../scripts/stages/minio/minio-job-api-iam-stage'

class MinioJobApiIamStageTest < Minitest::Test
  class FakeStage < MinioJobApiIamStage
    attr_accessor :fail_job, :temporary_matches
    attr_reader :writes, :resources, :policies, :user_policies

    def initialize
      super(output: StringIO.new, error: StringIO.new)
      @writes = []
      @resources = {}
      @policies = []
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

    def mc_optional(_credentials, _missing_code, kind, _operation, _alias, name)
      present = kind == 'user' ? !@user_policies.empty? : @policies.include?(name)
      present ? { 'status' => 'success' } : nil
    end

    def verify_temporary_secret(secret)
      raise 'temporary Secret is absent or different' unless secret && @temporary_matches
    end

    def verify_configmaps(expected)
      expected.each_value do |policy|
        raise 'policy ConfigMap missing' unless resource('configmap', policy.fetch(:name))
      end
    end

    def verify_jobs_if_present
      true
    end

    def verify_policy(_credentials, name, _expected)
      raise 'IAM policy missing' unless @policies.include?(name)
    end

    def verify_user(_credentials, expected_names)
      raise 'IAM user policy set differs' unless @user_policies.sort == expected_names.sort
    end

    def kubectl_write(*arguments)
      @writes << arguments
      return if arguments.include?('--dry-run=server')

      if arguments.first == 'create'
        path = arguments.fetch(2)
        if path == TEMP_SOURCE.to_s
          @resources[['secret', TEMP_SECRET]] = { 'kind' => 'Secret' }
        else
          document = YAML.load_file(path)
          kind = document.fetch('kind').downcase
          name = document.fetch('metadata').fetch('name')
          @resources[[kind, name]] = document
        end
      elsif arguments.first == 'wait'
        name = arguments.fetch(2).delete_prefix('job/')
        raise 'simulated Job failure' if name == @fail_job

        policy = POLICIES.fetch(JOBS.index(name))
        @policies << policy
        @user_policies << policy
      elsif arguments.first == 'delete'
        @resources.delete(['secret', TEMP_SECRET])
      end
    end
  end

  def test_versioned_job_manifests_keep_expected_commands_and_secret_boundaries
    stage = FakeStage.new
    source = stage.load_source
    policies = FakeStage::POLICIES.to_h { |name| [name, source.fetch(name)] }

    stage.send(:validate_jobs, policies)
    assert_equal %w[clouddsp-job-api-uploads-v002 clouddsp-job-api-artifact-read-v003], policies.keys
  end

  def test_bootstrap_orders_two_jobs_and_removes_temporary_secret_last
    stage = FakeStage.new

    assert_equal 0, stage.run('plan')
    assert_empty stage.writes
    assert_equal 0, stage.run('bootstrap')
    writes = stage.writes.reject { |args| args.include?('--dry-run=server') }
    assert_equal 'create', writes.first.first
    assert_equal FakeStage::TEMP_SOURCE.to_s, writes.first.last
    assert_equal [FakeStage::JOBS.first, FakeStage::JOBS.last],
                 writes.select { |args| args.first == 'wait' }.map { |args| args.fetch(2).delete_prefix('job/') }
    assert_equal ['delete', "secret/#{FakeStage::TEMP_SECRET}", '--wait=true'], writes.last
    assert_nil stage.resource('secret', FakeStage::TEMP_SECRET)
    assert_equal 0, stage.run('verify')
  end

  def test_existing_iam_or_kubernetes_resource_blocks_all_writes
    with_user = FakeStage.new
    with_user.user_policies << FakeStage::POLICIES.first
    with_config = FakeStage.new
    with_config.resources[['configmap', 'clouddsp-job-api-uploads-policy-v002']] = { 'kind' => 'ConfigMap' }
    [with_user, with_config].each do |stage|
      assert_equal 1, stage.run('bootstrap')
      assert_empty stage.writes
    end
  end

  def test_second_job_failure_preserves_partial_state_and_temporary_secret
    stage = FakeStage.new
    stage.fail_job = FakeStage::JOBS.last

    assert_equal 1, stage.run('bootstrap')
    assert stage.resource('secret', FakeStage::TEMP_SECRET)
    assert_equal [FakeStage::POLICIES.first], stage.policies
    refute stage.writes.any? { |args| args.first == 'delete' }
    assert_equal 1, stage.run('bootstrap')
  end

  def test_reconcile_removes_only_a_matching_temporary_secret_after_complete_iam
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
    stage = MinioJobApiIamStage.new(output: StringIO.new, error: StringIO.new)
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
