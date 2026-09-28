require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/deploy-local-verify'

class DeployLocalReconcileTest < Minitest::Test
  FakeStatus = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  def test_only_reviewed_external_state_stages_reconcile
    calls = []
    runner = lambda do |*command|
      calls << command
      ['', '', FakeStatus.new(0)]
    end
    output = StringIO.new
    result = CloudDSPLocalVerify.new(mode: 'reconcile', runner: runner, output: output, error: StringIO.new).run

    assert_equal 0, result
    assert_equal CloudDSPLocalVerify::STAGES.length, calls.length
    reconciled = calls.select { |command| command.last == 'reconcile' }
    assert_equal CloudDSPLocalVerify::RECONCILABLE.sort,
                 reconciled.map { |command| File.basename(command[1]) }.sort
    keycloak_gate = calls.find { |command| File.basename(command[1]) == 'keycloak-config-verify.rb' }
    assert_equal 'verify', keycloak_gate.last
    assert_operator calls.index(keycloak_gate), :<,
                    calls.index { |command| File.basename(command[1]) == 'job-api-release.rb' }
    postgresql_secret_gate = calls.find { |command| File.basename(command[1]) == 'postgresql-secret-stage.rb' }
    assert_equal 'verify', postgresql_secret_gate.last
    assert_operator calls.index(postgresql_secret_gate), :<,
                    calls.index { |command| File.basename(command[1]) == 'postgresql-release.rb' }
    rabbitmq_secret_gate = calls.find { |command| File.basename(command[1]) == 'rabbitmq-secret-stage.rb' }
    assert_equal 'verify', rabbitmq_secret_gate.last
    assert_operator calls.index(rabbitmq_secret_gate), :<,
                    calls.index { |command| File.basename(command[1]) == 'rabbitmq-release.rb' }
    minio_secret_gate = calls.find { |command| File.basename(command[1]) == 'minio-root-secret-stage.rb' }
    assert_equal 'verify', minio_secret_gate.last
    assert_operator calls.index(minio_secret_gate), :<,
                    calls.index { |command| File.basename(command[1]) == 'minio-release.rb' }
    minio_amqp_gate = calls.find { |command| File.basename(command[1]) == 'minio-amqp-secret-stage.rb' }
    assert_equal 'verify', minio_amqp_gate.last
    assert_operator calls.index(minio_secret_gate), :<, calls.index(minio_amqp_gate)
    assert_operator calls.index(minio_amqp_gate), :<,
                    calls.index { |command| File.basename(command[1]) == 'minio-release.rb' }
    upload_intake_secret_gate = calls.find { |command| File.basename(command[1]) == 'upload-intake-rabbitmq-secret-stage.rb' }
    assert_equal 'verify', upload_intake_secret_gate.last
    assert_operator calls.index(upload_intake_secret_gate), :<,
                    calls.index { |command| File.basename(command[1]) == 'rabbitmq-source-intake-bootstrap.rb' }
    minio_gate = calls.find { |command| File.basename(command[1]) == 'minio-notification-stage.rb' }
    assert_equal 'reconcile', minio_gate.last
    bucket_gate = calls.find { |command| File.basename(command[1]) == 'minio-buckets-stage.rb' }
    assert_equal 'reconcile', bucket_gate.last
    job_api_iam_gate = calls.find { |command| File.basename(command[1]) == 'minio-job-api-iam-stage.rb' }
    assert_equal 'reconcile', job_api_iam_gate.last
    upload_intake_iam_gate = calls.find { |command| File.basename(command[1]) == 'minio-upload-intake-iam-stage.rb' }
    assert_equal 'reconcile', upload_intake_iam_gate.last
    demucs_iam_gate = calls.find { |command| File.basename(command[1]) == 'minio-demucs-iam-stage.rb' }
    assert_equal 'reconcile', demucs_iam_gate.last
    basic_pitch_iam_gate = calls.find { |command| File.basename(command[1]) == 'minio-basic-pitch-iam-stage.rb' }
    assert_equal 'reconcile', basic_pitch_iam_gate.last
    adtof_iam_gate = calls.find { |command| File.basename(command[1]) == 'minio-adtof-iam-stage.rb' }
    assert_equal 'reconcile', adtof_iam_gate.last
    assert_operator calls.index { |command| File.basename(command[1]) == 'rabbitmq-source-intake-bootstrap.rb' },
                    :<, calls.index(bucket_gate)
    assert_operator calls.index(bucket_gate), :<, calls.index(minio_gate)
    assert_operator calls.index(bucket_gate), :<, calls.index(job_api_iam_gate)
    assert_operator calls.index(job_api_iam_gate), :<, calls.index(minio_gate)
    assert_operator calls.index(job_api_iam_gate), :<, calls.index(upload_intake_iam_gate)
    assert_operator calls.index(upload_intake_iam_gate), :<, calls.index(minio_gate)
    assert_operator calls.index(upload_intake_iam_gate), :<, calls.index(demucs_iam_gate)
    assert_operator calls.index(demucs_iam_gate), :<, calls.index(minio_gate)
    assert_operator calls.index(demucs_iam_gate), :<, calls.index(basic_pitch_iam_gate)
    assert_operator calls.index(basic_pitch_iam_gate), :<, calls.index(minio_gate)
    assert_operator calls.index(basic_pitch_iam_gate), :<, calls.index(adtof_iam_gate)
    assert_operator calls.index(adtof_iam_gate), :<, calls.index(minio_gate)
    assert_operator calls.index(minio_gate), :<,
                    calls.index { |command| File.basename(command[1]) == 'mailpit-release.rb' }
    assert calls.all? { |command| (command & %w[adopt upgrade install delete apply smoke]).empty? }
    assert_includes output.string, 'Helm releases, MinIO IAM, and Keycloak state verified'
  end

  def test_release_failure_prevents_any_bootstrap_write
    calls = []
    runner = lambda do |*command|
      calls << command
      ['', '', FakeStatus.new(File.basename(command[1]) == 'minio-release.rb' ? 1 : 0)]
    end
    errors = StringIO.new
    result = CloudDSPLocalVerify.new(mode: 'reconcile', runner: runner, output: StringIO.new, error: errors).run

    assert_equal 1, result
    assert_equal 'minio-release.rb', File.basename(calls.last[1])
    refute calls.any? { |command| command.last == 'reconcile' }
    assert_includes errors.string, 'MinIO release'
  end

  def test_bootstrap_failure_stops_later_stages_and_hides_child_output
    calls = []
    runner = lambda do |*command|
      calls << command
      if File.basename(command[1]) == 'rabbitmq-processing-topology.rb'
        ['sensitive child output', 'sensitive child error', FakeStatus.new(1)]
      else
        ['', '', FakeStatus.new(0)]
      end
    end
    output = StringIO.new
    errors = StringIO.new
    result = CloudDSPLocalVerify.new(mode: 'reconcile', runner: runner, output: output, error: errors).run

    assert_equal 1, result
    assert_equal 'rabbitmq-processing-topology.rb', File.basename(calls.last[1])
    assert_equal 'reconcile', calls.last.last
    refute_includes output.string + errors.string, 'sensitive child'
    refute_includes output.string, 'RabbitMQ processing topology'
  end
end
