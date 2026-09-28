require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/minio-notification-stage'

class MinioNotificationStageTest < Minitest::Test
  class FakeStage < MinioNotificationStage
    attr_reader :steps
    attr_accessor :states, :existing_job

    def initialize(states:, existing_job: '')
      super(output: StringIO.new, error: StringIO.new)
      @states = states
      @existing_job = existing_job
      @steps = []
    end

    def load_source
      {}
    end

    def root_credentials
      {}
    end

    def verify_configmaps(_policies)
      @steps << :configmaps
    end

    def verify_runtime_keys
      @steps << :runtime_keys
    end

    def verify_bucket_boundaries(_credentials)
      @steps << :buckets
    end

    def verify_iam(_credentials, _policies)
      @steps << :iam
    end

    def validate_source_job
      @steps << :source_job
    end

    def verify_prerequisites
      @steps << :prerequisites
    end

    def upload_notification_state(_credentials)
      state = @states.shift
      raise state if state.is_a?(Exception)

      @steps << [state, :notification]
      state
    end

    def job_name
      @steps << :job_lookup
      @existing_job
    end

    def kubectl_write(*arguments)
      @steps << arguments
    end
  end

  def test_reviewed_job_keeps_exact_pinned_notification_and_ephemeral_credentials
    MinioNotificationStage.new.send(:validate_source_job)
  end

  def test_ready_rule_never_creates_a_job
    runner = FakeStage.new(states: [:ready])

    assert_equal 0, runner.run('reconcile')
    refute_includes runner.steps, :job_lookup
    refute runner.steps.any? { |step| step.is_a?(Array) && step.first == 'create' }
  end

  def test_absent_rule_creates_only_reviewed_job_then_requires_durable_state
    runner = FakeStage.new(states: %i[absent absent ready])

    assert_equal 0, runner.run('reconcile')
    assert_equal [:prerequisites, [:absent, :notification], :job_lookup,
                  ['create', '--dry-run=server', '--filename', MinioNotificationStage::JOB_PATH.to_s],
                  ['create', '--filename', MinioNotificationStage::JOB_PATH.to_s],
                  ['wait', '--for=condition=complete', 'job/minio-source-intake-notification-bootstrap', '--timeout=210s'],
                  [:ready, :notification]], runner.steps.last(7)
  end

  def test_verify_fails_on_absence_without_writing
    runner = FakeStage.new(states: [:absent])

    assert_equal 1, runner.run('verify')
    refute_includes runner.steps, :prerequisites
    refute runner.steps.any? { |step| step.is_a?(Array) && step.first == 'create' }
  end

  def test_existing_job_or_notification_drift_blocks_write
    job = FakeStage.new(states: [:absent], existing_job: 'job/minio-source-intake-notification-bootstrap')
    drift = FakeStage.new(states: [RuntimeError.new('private upload notification target drifted')])

    assert_equal 1, job.run('reconcile')
    assert_equal 1, drift.run('reconcile')
    refute_includes job.steps, :prerequisites
    refute_includes drift.steps, :job_lookup
  end
end
