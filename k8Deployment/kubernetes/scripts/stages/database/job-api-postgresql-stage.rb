#!/usr/bin/env ruby
# Order the two Job API PostgreSQL bootstrap stages without taking ownership
# of their Kubernetes Jobs or SQL. Each runner keeps its own drift checks and
# durable completion evidence; this command only establishes their dependency.
require_relative '../../lib/paths'
require_relative 'job-api-database-bootstrap'
require_relative 'job-api-migrations'

class JobApiPostgresqlStage
  MODES = %w[plan verify reconcile].freeze

  def initialize(database_stage: JobApiDatabaseBootstrap.new, migration_stage: JobApiMigrations.new)
    @database_stage = database_stage
    @migration_stage = migration_stage
  end

  def run(mode)
    raise ArgumentError, 'use plan, verify, or reconcile' unless MODES.include?(mode)

    # PostgreSQL cannot expose the migration ledger before its dedicated
    # database exists. A fresh-cluster plan reports that dependency without
    # creating resources or attempting a connection to a missing database.
    database_state = @database_stage.run(mode)
    if mode == 'plan' && database_state == :absent
      puts 'Job API PostgreSQL stage: migrations wait for database bootstrap'
      puts 'Job API PostgreSQL stage plan passed'
      return :pending
    end

    # Reconcile reaches this point only after the bootstrap Job completed and
    # the database runner rechecked its owner, role, schema, and grants. Verify
    # also stops at that runner if the database is missing or drifted.
    raise "unexpected database bootstrap state: #{database_state.inspect}" unless database_state == :ready

    @migration_stage.run(mode)
    puts "Job API PostgreSQL stage #{mode} passed"
    :ready
  rescue StandardError => error
    warn "Job API PostgreSQL stage #{mode} stopped: #{error.message}"
    exit 1
  end
end

JobApiPostgresqlStage.new.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
