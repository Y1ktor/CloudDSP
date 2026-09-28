require 'json'
require 'minitest/autorun'
require 'yaml'
require_relative '../../scripts/job-api-migrations'

class JobApiMigrationsTest < Minitest::Test
  # Exercise the stage's decisions without a database or Kubernetes writes.
  # The fake returns the same narrow metadata and ledger projections as the
  # real commands; it records whether a prohibited create was attempted.
  class FakeCluster
    attr_reader :calls, :rows

    def initialize(migrations, applied:, changed_description: false, changed_sql: false,
                   missing_config: false, pending_job: false)
      @migrations = migrations
      @rows = migrations.take(applied).map { |item| { 'migration_id' => item.id, 'description' => item.description } }
      @rows.first['description'] = 'unexpected description' if changed_description
      @pending_job = pending_job
      @calls = []
      @configs = migrations.to_h { |item| [item.config.dig('metadata', 'name'), item.config] }
      @configs.delete(migrations.last.config.dig('metadata', 'name')) if missing_config
      if changed_sql
        name = migrations.first.config.dig('metadata', 'name')
        @configs[name] = Marshal.load(Marshal.dump(@configs.fetch(name)))
        @configs.fetch(name).fetch('data').values.first << '\n-- unexpected live edit'
      end
    end

    def call(*argv)
      @calls << argv
      if argv.include?('exec')
        return "present\n" if argv.last == JobApiMigrations::TABLE_QUERY
        return @rows.map(&:to_json).join("\n") if argv.last == JobApiMigrations::LEDGER_QUERY
      end
      case argv[5]
      when 'get'
        name = argv[6]
        return @configs.key?(name.delete_prefix('configmap/')) ? JSON.generate(@configs.fetch(name.delete_prefix('configmap/'))) : '' if name.start_with?('configmap/')
        return @pending_job ? JSON.generate({ 'kind' => 'Job' }) : '' if name.start_with?('job/')
        return "secret/clouddsp-job-api-database-credentials\n" if name.start_with?('secret/')
        return JSON.generate({ 'status' => { 'readyReplicas' => 1 } }) if name.start_with?('statefulset/')
      when 'create'
        return 'validated' if argv.include?('--dry-run=server')
        if argv.last.end_with?('-configmap.yaml')
          migration = @migrations.find { |item| item.config_path == argv.last }
          @configs[migration.config.dig('metadata', 'name')] = migration.config
          return 'created'
        end
        if argv.last.end_with?('-job.yaml')
          migration = @migrations.find { |item| item.job_path == argv.last }
          @rows << { 'migration_id' => migration.id, 'description' => migration.description }
          return 'created'
        end
      when 'wait'
        return 'complete'
      end
      raise "unexpected fake command: #{argv.inspect}"
    end
  end

  def setup
    @stage = JobApiMigrations.new
    @migrations = @stage.load_sources
  end

  def test_versioned_sources_are_a_contiguous_nine_migration_sequence
    assert_equal((1..9).to_a, @migrations.map(&:number))
    assert_equal('v009_tempo_resolution', @migrations.last.id)
  end

  def test_reconcile_is_a_no_op_when_the_complete_ledger_matches
    fake = FakeCluster.new(@migrations, applied: 9)
    capture_io { JobApiMigrations.new(runner: fake.method(:call)).run('reconcile') }
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  def test_one_missing_migration_uses_its_versioned_job_then_checks_the_ledger
    fake = FakeCluster.new(@migrations, applied: 8)
    capture_io { JobApiMigrations.new(runner: fake.method(:call)).run('reconcile') }
    assert_equal(9, fake.rows.length)
    created = fake.calls.select { |argv| argv.include?('create') && !argv.include?('--dry-run=server') }
    assert_equal([@migrations.last.job_path], created.map(&:last))
  end

  def test_missing_sql_configmap_is_created_before_the_migration_job
    fake = FakeCluster.new(@migrations, applied: 8, missing_config: true)
    capture_io { JobApiMigrations.new(runner: fake.method(:call)).run('reconcile') }
    created = fake.calls.select { |argv| argv.include?('create') && !argv.include?('--dry-run=server') }
    assert_equal([@migrations.last.config_path, @migrations.last.job_path], created.map(&:last))
    assert_equal(9, fake.rows.length)
  end

  def test_changed_ledger_description_stops_before_any_mutation
    fake = FakeCluster.new(@migrations, applied: 9, changed_description: true)
    _out, err = capture_io do
      assert_raises(SystemExit) { JobApiMigrations.new(runner: fake.method(:call)).run('reconcile') }
    end
    assert_includes(err, 'schema ledger differs')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  def test_missing_predecessor_stops_before_any_mutation
    fake = FakeCluster.new(@migrations, applied: 9)
    fake.rows.delete_at(3)
    _out, err = capture_io do
      assert_raises(SystemExit) { JobApiMigrations.new(runner: fake.method(:call)).run('reconcile') }
    end
    assert_includes(err, 'schema ledger differs from v004_downstream_outbox_events')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  def test_changed_immutable_sql_stops_before_any_mutation
    fake = FakeCluster.new(@migrations, applied: 9, changed_sql: true)
    _out, err = capture_io do
      assert_raises(SystemExit) { JobApiMigrations.new(runner: fake.method(:call)).run('reconcile') }
    end
    assert_includes(err, 'live immutable SQL ConfigMap differs')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end

  def test_pending_fixed_name_job_stops_before_any_mutation
    fake = FakeCluster.new(@migrations, applied: 8, pending_job: true)
    _out, err = capture_io do
      assert_raises(SystemExit) { JobApiMigrations.new(runner: fake.method(:call)).run('reconcile') }
    end
    assert_includes(err, 'Job exists without a ledger row')
    refute(fake.calls.any? { |argv| argv.include?('create') })
  end
end
