require 'minitest/autorun'
require_relative '../../scripts/job-api-postgresql-stage'

class JobApiPostgresqlStageTest < Minitest::Test
  class FakeStage
    def initialize(name, calls, result: :ready, failure: nil)
      @name = name
      @calls = calls
      @result = result
      @failure = failure
    end

    def run(mode)
      @calls << [@name, mode]
      raise @failure if @failure

      @result
    end
  end

  def test_ready_plan_inspects_database_before_migrations
    calls = []
    stage = build_stage(calls)

    result = nil
    capture_io { result = stage.run('plan') }

    assert_equal(:ready, result)
    assert_equal([[:database, 'plan'], [:migrations, 'plan']], calls)
  end

  def test_fresh_plan_defers_migration_inspection
    calls = []
    stage = build_stage(calls, database_result: :absent)

    result = nil
    out, = capture_io { result = stage.run('plan') }

    assert_equal(:pending, result)
    assert_equal([[:database, 'plan']], calls)
    assert_includes(out, 'migrations wait for database bootstrap')
  end

  def test_reconcile_runs_migrations_only_after_bootstrap_is_ready
    calls = []
    stage = build_stage(calls)

    capture_io { stage.run('reconcile') }

    assert_equal([[:database, 'reconcile'], [:migrations, 'reconcile']], calls)
  end

  def test_bootstrap_failure_stops_before_migrations
    calls = []
    stage = build_stage(calls, database_failure: 'database drifted')

    _out, err = capture_io { assert_raises(SystemExit) { stage.run('reconcile') } }

    assert_equal([[:database, 'reconcile']], calls)
    assert_includes(err, 'database drifted')
  end

  def test_unexpected_bootstrap_state_stops_before_migrations
    calls = []
    stage = build_stage(calls, database_result: :absent)

    _out, err = capture_io { assert_raises(SystemExit) { stage.run('reconcile') } }

    assert_equal([[:database, 'reconcile']], calls)
    assert_includes(err, 'unexpected database bootstrap state')
  end

  def test_migration_failure_stops_the_combined_stage
    calls = []
    stage = build_stage(calls, migration_failure: 'ledger drifted')

    _out, err = capture_io { assert_raises(SystemExit) { stage.run('verify') } }

    assert_equal([[:database, 'verify'], [:migrations, 'verify']], calls)
    assert_includes(err, 'ledger drifted')
  end

  def test_invalid_mode_runs_neither_stage
    calls = []
    stage = build_stage(calls)

    _out, err = capture_io { assert_raises(SystemExit) { stage.run('adopt') } }

    assert_empty(calls)
    assert_includes(err, 'use plan, verify, or reconcile')
  end

  private

  def build_stage(calls, database_result: :ready, database_failure: nil, migration_failure: nil)
    JobApiPostgresqlStage.new(
      database_stage: FakeStage.new(:database, calls, result: database_result, failure: database_failure),
      migration_stage: FakeStage.new(:migrations, calls, failure: migration_failure)
    )
  end
end
