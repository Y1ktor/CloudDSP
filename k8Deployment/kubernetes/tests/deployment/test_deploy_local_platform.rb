require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/deploy-local-platform'

class DeployLocalPlatformTest < Minitest::Test
  def setup
    @calls = []
    @output = StringIO.new
    @error = StringIO.new
  end

  def run_platform(failing_call = nil)
    runner = lambda do |ruby, script, *arguments|
      assert_equal RbConfig.ruby, ruby
      call = [File.basename(script), *arguments]
      @calls << call
      call != failing_call
    end
    CloudDSPBootstrapPlatform.new(run_command: runner, output: @output, error: @error).run
  end

  def test_existing_guarded_children_run_once_in_dependency_order
    expected = [
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
    ].map { |_label, script, *arguments| [script, *arguments] }

    assert_equal 0, run_platform
    assert_equal expected, @calls
    assert_equal 1, @calls.count(%w[deploy-local-foundation.rb plan])
    assert_equal 1, @calls.count(['credential-init.rb'])
    assert_equal 1, @calls.count(['deploy-local-prepare.rb'])
    assert_operator @calls.index(%w[deploy-local-foundation.rb plan]), :<,
                    @calls.index(['credential-init.rb'])
    assert_operator @calls.index(['credential-init.rb']), :<,
                    @calls.index(['deploy-local-prepare.rb'])
    assert_equal 1, @calls.count(%w[rabbitmq-release.rb install])
    assert_equal 1, @calls.count(%w[minio-release.rb install])
    assert_operator @calls.index(%w[postgresql-release.rb verify]), :<,
                    @calls.index(%w[rabbitmq-release.rb install])
    assert_operator @calls.index(%w[minio-notification-stage.rb verify]), :<,
                    @calls.index(%w[keycloak-database-stage.rb bootstrap])
    assert_operator @calls.index(%w[keycloak-database-stage.rb verify]), :<,
                    @calls.index(%w[mailpit-release.rb install])
    assert_operator @calls.index(%w[mailpit-release.rb verify]), :<,
                    @calls.index(%w[keycloak-admin-secret-stage.rb bootstrap])
    assert_operator @calls.index(%w[keycloak-admin-secret-stage.rb bootstrap]), :<,
                    @calls.index(%w[keycloak-release.rb install])
    assert_operator @calls.index(%w[keycloak-release.rb verify]), :<,
                    @calls.index(%w[keycloak-realm-stage.rb bootstrap])
    assert_operator @calls.index(%w[keycloak-realm-stage.rb verify]), :<,
                    @calls.index(%w[job-api-database-secret-stage.rb bootstrap])
    assert_operator @calls.index(%w[job-api-database-secret-stage.rb bootstrap]), :<,
                    @calls.index(%w[job-api-database-bootstrap.rb reconcile])
    assert_operator @calls.index(%w[job-api-migrations.rb reconcile-prerequisites]), :<,
                    @calls.index(%w[worker-database-stage.rb basic-pitch bootstrap])
    assert_operator @calls.index(%w[worker-database-stage.rb adtof bootstrap]), :<,
                    @calls.index(%w[job-api-migrations.rb reconcile])
    assert_operator @calls.index(%w[job-api-postgresql-stage.rb verify]), :<,
                    @calls.index(%w[rabbitmq-processing-topology.rb reconcile])
    assert_operator @calls.index(%w[rabbitmq-processing-topology.rb verify]), :<,
                    @calls.index(%w[job-api-release.rb install])
    assert_operator @calls.index(%w[job-api-release.rb verify]), :<,
                    @calls.index(%w[keda-release-stage.rb install])
    assert_operator @calls.index(%w[keda-release-stage.rb install]), :<,
                    @calls.index(%w[keda-release-stage.rb verify])
    assert_operator @calls.index(%w[scaling-auth-release.rb install]), :<,
                    @calls.index(%w[demucs-release.rb install])
    assert_operator @calls.index(%w[basic-pitch-release.rb install]), :<,
                    @calls.index(%w[adtof-release.rb install])
    assert_operator @calls.index(%w[frontend-release.rb install]), :<,
                    @calls.index(%w[frontend-release.rb verify])
    assert @calls.all? { |call| (call & %w[adopt upgrade smoke delete apply]).empty? }
    assert_includes @output.string, 'all application Helm releases are ready'
    assert_empty @error.string
  end

  def test_existing_cluster_guard_stops_before_local_credentials_are_initialized
    assert_equal 1, run_platform(%w[deploy-local-foundation.rb plan])
    assert_equal [%w[deploy-local-foundation.rb plan]], @calls
    refute_includes @output.string, 'local credential initialization'
    assert_includes @error.string, 'absent-cluster guard'
  end

  def test_every_failure_stops_before_the_next_stage_or_success_claim
    CloudDSPBootstrapPlatform::STEPS.each_with_index do |(_label, script, *arguments), index|
      @calls.clear
      @output.truncate(0)
      @output.rewind
      @error.truncate(0)
      @error.rewind

      assert_equal 1, run_platform([script, *arguments]), "stage #{index + 1} should stop"
      assert_equal index + 1, @calls.length
      assert_includes @error.string, 'inspect that stage before retrying'
      refute_includes @output.string, 'bootstrap-platform complete'
    end
  end

  def test_list_shows_exact_stage_labels_without_running_children
    runner = CloudDSPBootstrapPlatform.new(run_command: ->(*_arguments) { flunk 'stage list contacted a child' },
                                           output: @output, error: @error)

    assert_equal 0, runner.list
    assert_equal CloudDSPBootstrapPlatform::STEPS.each_with_index.map { |(label, *_rest), index|
      format('%02d. %s', index + 1, label)
    }, @output.string.lines.map(&:chomp)
    assert_empty @error.string
  end
end
