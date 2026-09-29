require 'minitest/autorun'
require 'stringio'
require_relative '../../scripts/keycloak-realm-stage'

class KeycloakRealmStageTest < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  class FakeVerifier
    attr_accessor :result
    attr_reader :loads, :runs

    def initialize(result: 0)
      @result = result
      @loads = 0
      @runs = 0
    end

    def load_desired
      @loads += 1
      { realm: 'clouddsp' }
    end

    def run
      @runs += 1
      @result
    end
  end

  # The fake records ordering and simulates absence checks. It never invokes
  # Helm, Kubernetes, or the Admin API; the committed Job contracts are still
  # parsed by the real stage before these external operations would begin.
  class FakeCluster
    attr_accessor :existing_job, :failing_prerequisite, :failing_dry_run, :failing_wait
    attr_reader :calls

    def initialize
      @calls = []
    end

    def call(*argv)
      @calls << argv
      if argv.first == 'ruby'
        return result('', 1) if File.basename(argv.fetch(1)) == @failing_prerequisite
        return result('')
      end
      raise "unexpected fake command #{argv.first}" unless argv.first == 'kubectl'

      case argv[5]
      when 'get'
        name = argv.fetch(6)
        return result(name == "job/#{@existing_job}" ? "#{name}\n" : '')
      when 'create'
        filename = File.basename(argv.last)
        return result('', 1) if argv.include?('--dry-run=server') && filename == @failing_dry_run
        return result('')
      when 'wait'
        return result('', 1) if argv.include?("job/#{@failing_wait}")
        return result('')
      end
      raise "unexpected fake kubectl verb #{argv[5]}"
    end

    private

    def result(output, exitstatus = 0)
      [output, 'possibly sensitive client detail', Status.new(exitstatus)]
    end
  end

  def with_stage(realm: :absent, verify_result: 0)
    fake = FakeCluster.new
    verifier = FakeVerifier.new(result: verify_result)
    output = StringIO.new
    error = StringIO.new
    stage = CloudDSPKeycloakRealmStage.new(command: fake.method(:call),
                                            probe: -> { realm.is_a?(Array) ? realm.shift : realm },
                                            verifier: verifier, output: output, error: error)
    yield stage, fake, verifier, output, error
  end

  def test_plan_checks_absence_and_source_contract_without_writing
    with_stage do |stage, fake, verifier, output, _error|
      assert_equal 0, stage.run('plan')
      assert_equal 1, verifier.loads
      assert_equal 0, verifier.runs
      assert_includes output.string, 'six versioned Admin API Jobs pending'
      refute fake.calls.any? { |call| call.include?('create') || call.include?('wait') }
    end
  end

  def test_bootstrap_dry_runs_all_jobs_before_ordered_create_and_wait
    with_stage do |stage, fake, verifier, output, _error|
      assert_equal 0, stage.run('bootstrap')
      creates = fake.calls.select { |call| call[5] == 'create' }
      assert_equal 12, creates.length
      assert creates.take(6).all? { |call| call.include?('--dry-run=server') }
      assert creates.drop(6).none? { |call| call.include?('--dry-run=server') }
      expected_names = CloudDSPKeycloakRealmStage::JOBS.map(&:last)
      assert_equal expected_names, creates.drop(6).map { |call| File.basename(call.last, '-job.yaml') }
      assert_equal expected_names.map { |name| "job/#{name}" },
                   fake.calls.select { |call| call[5] == 'wait' }.map { |call| call.find { |part| part.start_with?('job/') } }
      assert_equal 1, verifier.runs
      assert_includes output.string, 'registration policies bootstrapped'
    end
  end

  def test_complete_existing_realm_only_verifies
    with_stage(realm: :present) do |stage, fake, verifier, output, _error|
      assert_equal 0, stage.run('bootstrap')
      assert_equal 1, verifier.runs
      assert_includes output.string, 'no work pending'
      refute fake.calls.any? { |call| call[5] == 'create' }
    end
  end

  def test_existing_incomplete_realm_blocks_all_job_creation
    with_stage(realm: :present, verify_result: 1) do |stage, fake, verifier, _output, error|
      assert_equal 1, stage.run('bootstrap')
      assert_equal 1, verifier.runs
      assert_includes error.string, 'incomplete or drifted'
      refute fake.calls.any? { |call| call[5] == 'create' }
    end
  end

  def test_existing_job_with_absent_realm_blocks_dry_run_and_creation
    with_stage do |stage, fake, _verifier, _output, error|
      fake.existing_job = 'keycloak-realm-bootstrap'
      assert_equal 1, stage.run('bootstrap')
      assert_includes error.string, 'already exists without complete realm state'
      refute fake.calls.any? { |call| call[5] == 'create' }
    end
  end

  def test_prerequisite_failure_stops_before_realm_probe_or_job_lookup
    with_stage do |stage, fake, _verifier, _output, error|
      fake.failing_prerequisite = 'keycloak-admin-secret-stage.rb'
      assert_equal 1, stage.run('bootstrap')
      assert_includes error.string, 'Keycloak admin Secret verification failed'
      refute fake.calls.any? { |call| call.first == 'kubectl' }
      refute_includes error.string, 'possibly sensitive client detail'
    end
  end

  def test_dry_run_failure_leaves_all_jobs_absent
    with_stage do |stage, fake, verifier, _output, error|
      fake.failing_dry_run = 'keycloak-realm-smtp-config-job.yaml'
      assert_equal 1, stage.run('bootstrap')
      assert_includes error.string, 'Keycloak smtp Job server dry run failed'
      assert fake.calls.select { |call| call[5] == 'create' }.all? { |call| call.include?('--dry-run=server') }
      assert_equal 0, verifier.runs
    end
  end

  def test_realm_appearing_during_preflight_blocks_all_real_creates
    with_stage(realm: %i[absent present]) do |stage, fake, verifier, _output, error|
      assert_equal 1, stage.run('bootstrap')
      assert_includes error.string, 'realm appeared during preflight'
      assert fake.calls.select { |call| call[5] == 'create' }.all? { |call| call.include?('--dry-run=server') }
      assert_equal 0, verifier.runs
    end
  end

  def test_failed_job_stops_later_jobs_for_inspection
    with_stage do |stage, fake, verifier, _output, error|
      fake.failing_wait = 'keycloak-realm-registration-policy'
      assert_equal 1, stage.run('bootstrap')
      created = fake.calls.select { |call| call[5] == 'create' && !call.include?('--dry-run=server') }
      assert_equal 2, created.length
      assert_includes error.string, 'Keycloak registration Job completion failed'
      assert_equal 0, verifier.runs
    end
  end

  def test_verify_uses_durable_config_verifier_without_writes
    with_stage(realm: :present) do |stage, fake, verifier, _output, _error|
      assert_equal 0, stage.run('verify')
      assert_equal 1, verifier.runs
      refute fake.calls.any? { |call| call[5] == 'create' }
    end
  end
end
