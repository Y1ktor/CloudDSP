#!/usr/bin/env ruby
# Compose the reviewed fresh-cluster platform and the two application-state
# runners that already have guarded write paths. Reuse each partial bootstrap's
# ordered child stages exactly once: preparation creates the cluster and image
# mirror, then PostgreSQL, RabbitMQ, and MinIO receive their own Secrets,
# releases, and external state. Keycloak's isolated database, Mailpit, and the
# identity-provider release precede Job API database/migrations. The Job API
# PostgreSQL runtime Secret must exist before its role bootstrap Job can verify
# matching credentials and run schema migrations;
# processing topology is then imported into the broker.
#
# This is a partial bootstrap. Keycloak's realm, remaining service roles and
# Secrets, KEDA, and application Helm releases need independent fresh runners before a
# full browser-to-worker bootstrap can be enabled. A failed child leaves the
# partial cluster for inspection; this coordinator never adopts or retries it.
require 'rbconfig'
require_relative 'deploy-local-postgresql'
require_relative 'deploy-local-rabbitmq'
require_relative 'deploy-local-minio'

class CloudDSPBootstrapPlatform
  SCRIPT_DIRECTORY = File.expand_path(__dir__).freeze
  STEPS = [
    *CloudDSPBootstrapPostgresql::STEPS,
    *CloudDSPBootstrapRabbitmq::STEPS.drop(1),
    *CloudDSPBootstrapMinio::STEPS.drop(1),
    ['Keycloak PostgreSQL database and credentials', 'keycloak-database-stage.rb', 'bootstrap'],
    ['Keycloak PostgreSQL database verification', 'keycloak-database-stage.rb', 'verify'],
    ['fresh Mailpit Helm install', 'mailpit-release.rb', 'install'],
    ['Mailpit Helm verification', 'mailpit-release.rb', 'verify'],
    ['Keycloak bootstrap-admin credential Secret', 'keycloak-admin-secret-stage.rb', 'bootstrap'],
    ['fresh Keycloak Helm install', 'keycloak-release.rb', 'install'],
    ['Keycloak Helm verification', 'keycloak-release.rb', 'verify'],
    ['Job API PostgreSQL runtime credential Secret', 'job-api-database-secret-stage.rb', 'bootstrap'],
    ['Job API PostgreSQL database and migrations', 'job-api-postgresql-stage.rb', 'reconcile'],
    ['Job API PostgreSQL verification', 'job-api-postgresql-stage.rb', 'verify'],
    ['RabbitMQ processing topology', 'rabbitmq-processing-topology.rb', 'reconcile'],
    ['RabbitMQ processing topology verification', 'rabbitmq-processing-topology.rb', 'verify']
  ].freeze

  def initialize(run_command: method(:system), output: $stdout, error: $stderr)
    @run_command = run_command
    @output = output
    @error = error
  end

  def run
    STEPS.each_with_index do |(label, script, *arguments), index|
      @output.puts "CloudDSP bootstrap-platform #{index + 1}/#{STEPS.length}: #{label}"
      @output.flush
      next if @run_command.call(RbConfig.ruby, File.join(SCRIPT_DIRECTORY, script), *arguments)

      @error.puts "CloudDSP bootstrap-platform stopped at #{label}; inspect that stage before retrying."
      return 1
    end
    @output.puts 'CloudDSP bootstrap-platform complete: foundation, images, PostgreSQL, RabbitMQ, MinIO, Keycloak database and release, Mailpit, Job API schema, and processing topology ready; application bootstrap remains pending.'
    0
  end

  # Show the exact current composition without contacting Kubernetes. Keep
  # this derived from STEPS so the documented stage names cannot drift from
  # the command that would execute them on a clean cluster.
  def list
    STEPS.each_with_index { |(label, _script, *_arguments), index| @output.puts format('%02d. %s', index + 1, label) }
    0
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: deploy-local-platform.rb [list]' unless ARGV.empty? || ARGV == ['list']
  runner = CloudDSPBootstrapPlatform.new
  exit(ARGV == ['list'] ? runner.list : runner.run)
end
