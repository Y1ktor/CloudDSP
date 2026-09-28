#!/usr/bin/env ruby
# Compose only PostgreSQL's fresh-cluster path. Preparation creates the
# reviewed cluster/namespaces and mirrors locked images. The Secret stage
# creates and verifies its ignored local credentials before Helm may create
# the StatefulSet and generated PVC. Each child stops the sequence on failure;
# neither a partial cluster nor a partial database is implicitly replaced.
require 'rbconfig'

class CloudDSPBootstrapPostgresql
  SCRIPT_DIRECTORY = File.expand_path(__dir__).freeze
  STEPS = [
    ['fresh foundation and locked images', 'deploy-local-prepare.rb'],
    ['PostgreSQL credential Secret', 'postgresql-secret-stage.rb', 'bootstrap'],
    ['fresh PostgreSQL Helm install', 'postgresql-release.rb', 'install'],
    ['PostgreSQL Helm and PVC verification', 'postgresql-release.rb', 'verify']
  ].freeze

  def initialize(run_command: method(:system), output: $stdout, error: $stderr)
    @run_command = run_command
    @output = output
    @error = error
  end

  def run
    STEPS.each_with_index do |(label, script, *arguments), index|
      @output.puts "CloudDSP bootstrap-postgresql #{index + 1}/#{STEPS.length}: #{label}"
      @output.flush
      next if @run_command.call(RbConfig.ruby, File.join(SCRIPT_DIRECTORY, script), *arguments)

      @error.puts "CloudDSP bootstrap-postgresql stopped at #{label}; inspect that stage before retrying."
      return 1
    end
    @output.puts 'CloudDSP bootstrap-postgresql complete: cluster, images, credentials, and database release ready; remaining services are pending.'
    0
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: deploy-local-postgresql.rb (no arguments)' unless ARGV.empty?
  exit CloudDSPBootstrapPostgresql.new.run
end
