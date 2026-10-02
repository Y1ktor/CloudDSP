#!/usr/bin/env ruby
# Compose the reviewed fresh-cluster platform and the two application-state
# runners that already have guarded write paths. Reuse each partial bootstrap's
# ordered child stages exactly once: an absent-cluster guard runs before local
# credential initialization, preparation creates the cluster and image mirror,
# then PostgreSQL, RabbitMQ, and MinIO receive their own Secrets,
# releases, and external state. Keycloak's isolated database, Mailpit, and the
# identity-provider release and realm precede Job API database/migrations. The
# Job API schema passes through v006 before worker database roles are created;
# v007 and later depend on those roles. Processing topology is then
# imported into the broker before the Job API Helm release starts.
#
# The one-command bootstrap continues through restricted application
# identities, KEDA authentication, and each fresh application Helm release.
# Every child runs its own absent-state guard and verifier. A failed child
# leaves the partial cluster for inspection; this coordinator never adopts or
# retries it.
require 'rbconfig'
require_relative 'deploy-local-postgresql'
require_relative 'deploy-local-rabbitmq'
require_relative 'deploy-local-minio'

class CloudDSPBootstrapPlatform
  SCRIPT_DIRECTORY = File.expand_path(__dir__).freeze
  STEPS = [
    # The initializer may write owner-only local files. Check the target
    # cluster is absent first, so invoking fresh bootstrap against a running
    # cluster cannot generate credentials unrelated to its durable state.
    # `prepare` repeats this read-only foundation plan before its first write.
    ['absent-cluster guard', 'deploy-local-foundation.rb', 'plan'],
    ['local credential initialization', 'credential-init.rb'],
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
    ['Keycloak realm and client bootstrap', 'keycloak-realm-stage.rb', 'bootstrap'],
    ['Keycloak realm and client verification', 'keycloak-realm-stage.rb', 'verify'],
    ['Job API PostgreSQL runtime credential Secret', 'job-api-database-secret-stage.rb', 'bootstrap'],
    ['Job API PostgreSQL database bootstrap', 'job-api-database-bootstrap.rb', 'reconcile'],
    ['Job API prerequisite schema migrations through v006', 'job-api-migrations.rb', 'reconcile-prerequisites'],
    ['Basic Pitch PostgreSQL role bootstrap', 'worker-database-stage.rb', 'basic-pitch', 'bootstrap'],
    ['ADTOF PostgreSQL role bootstrap', 'worker-database-stage.rb', 'adtof', 'bootstrap'],
    ['Job API remaining schema migrations', 'job-api-migrations.rb', 'reconcile'],
    ['Job API PostgreSQL verification', 'job-api-postgresql-stage.rb', 'verify'],
    ['upload-intake PostgreSQL role and runtime Secret', 'application-identity-stage.rb', 'database', 'upload-intake', 'bootstrap'],
    ['upload-intake PostgreSQL identity verification', 'application-identity-stage.rb', 'database', 'upload-intake', 'verify'],
    ['dispatcher PostgreSQL role and runtime Secret', 'application-identity-stage.rb', 'database', 'dispatcher', 'bootstrap'],
    ['dispatcher PostgreSQL identity verification', 'application-identity-stage.rb', 'database', 'dispatcher', 'verify'],
    ['Demucs PostgreSQL role and runtime Secret', 'application-identity-stage.rb', 'database', 'demucs', 'bootstrap'],
    ['Demucs PostgreSQL identity verification', 'application-identity-stage.rb', 'database', 'demucs', 'verify'],
    ['Demucs KEDA PostgreSQL scaler role and Secret', 'application-identity-stage.rb', 'database', 'keda-demucs', 'bootstrap'],
    ['Demucs KEDA PostgreSQL scaler identity verification', 'application-identity-stage.rb', 'database', 'keda-demucs', 'verify'],
    ['RabbitMQ processing topology', 'rabbitmq-processing-topology.rb', 'reconcile'],
    ['RabbitMQ processing topology verification', 'rabbitmq-processing-topology.rb', 'verify'],
    ['dispatcher RabbitMQ publisher user and Secret', 'application-identity-stage.rb', 'rabbitmq', 'dispatcher', 'bootstrap'],
    ['dispatcher RabbitMQ publisher verification', 'application-identity-stage.rb', 'rabbitmq', 'dispatcher', 'verify'],
    ['Demucs RabbitMQ consumer user and Secret', 'application-identity-stage.rb', 'rabbitmq', 'demucs', 'bootstrap'],
    ['Demucs RabbitMQ consumer verification', 'application-identity-stage.rb', 'rabbitmq', 'demucs', 'verify'],
    ['Basic Pitch RabbitMQ consumer user and Secret', 'application-identity-stage.rb', 'rabbitmq', 'basic-pitch', 'bootstrap'],
    ['Basic Pitch RabbitMQ consumer verification', 'application-identity-stage.rb', 'rabbitmq', 'basic-pitch', 'verify'],
    ['ADTOF RabbitMQ consumer user and Secret', 'application-identity-stage.rb', 'rabbitmq', 'adtof', 'bootstrap'],
    ['ADTOF RabbitMQ consumer verification', 'application-identity-stage.rb', 'rabbitmq', 'adtof', 'verify'],
    ['KEDA RabbitMQ scaler user and Secret', 'application-identity-stage.rb', 'rabbitmq', 'keda-scaler', 'bootstrap'],
    ['KEDA RabbitMQ scaler identity verification', 'application-identity-stage.rb', 'rabbitmq', 'keda-scaler', 'verify'],
    ['fresh Job API Helm install', 'job-api-release.rb', 'install'],
    ['Job API Helm verification', 'job-api-release.rb', 'verify'],
    ['fresh KEDA Helm install', 'keda-release-stage.rb', 'install'],
    ['KEDA Helm and controller verification', 'keda-release-stage.rb', 'verify'],
    ['fresh KEDA scaling-auth Helm install', 'scaling-auth-release.rb', 'install'],
    ['KEDA scaling-auth prerequisite verification', 'scaling-auth-release.rb', 'verify-prerequisites'],
    ['fresh upload-intake Helm install', 'upload-intake-release.rb', 'install'],
    ['upload-intake Helm verification', 'upload-intake-release.rb', 'verify'],
    ['fresh legacy dispatcher Helm install', 'dispatcher-release.rb', 'install'],
    ['legacy dispatcher Helm verification', 'dispatcher-release.rb', 'verify'],
    ['fresh generic dispatcher Helm install', 'generic-dispatcher-release.rb', 'install'],
    ['generic dispatcher Helm verification', 'generic-dispatcher-release.rb', 'verify'],
    ['fresh Demucs Helm install', 'demucs-release.rb', 'install'],
    ['Demucs Helm verification', 'demucs-release.rb', 'verify'],
    ['fresh Basic Pitch Helm install', 'basic-pitch-release.rb', 'install'],
    ['Basic Pitch Helm verification', 'basic-pitch-release.rb', 'verify'],
    ['fresh ADTOF Helm install', 'adtof-release.rb', 'install'],
    ['ADTOF Helm verification', 'adtof-release.rb', 'verify'],
    ['fresh frontend Helm install', 'frontend-release.rb', 'install'],
    ['frontend Helm, route, and static asset verification', 'frontend-release.rb', 'verify']
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
    @output.puts 'CloudDSP bootstrap-platform complete: local credentials, foundation, locked images, data services, identity, application database and broker roles, KEDA authentication, and all application Helm releases are ready.'
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
