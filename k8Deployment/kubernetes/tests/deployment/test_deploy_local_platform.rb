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
      ['Job API PostgreSQL database and migrations', 'job-api-postgresql-stage.rb', 'reconcile'],
      ['Job API PostgreSQL verification', 'job-api-postgresql-stage.rb', 'verify'],
      ['RabbitMQ processing topology', 'rabbitmq-processing-topology.rb', 'reconcile'],
      ['RabbitMQ processing topology verification', 'rabbitmq-processing-topology.rb', 'verify'],
      ['fresh Job API Helm install', 'job-api-release.rb', 'install'],
      ['Job API Helm verification', 'job-api-release.rb', 'verify']
    ].map { |_label, script, *arguments| [script, *arguments] }

    assert_equal 0, run_platform
    assert_equal expected, @calls
    assert_equal 1, @calls.count(['deploy-local-prepare.rb'])
    assert_equal 1, @calls.count(%w[rabbitmq-release.rb install])
    assert_equal 1, @calls.count(%w[minio-release.rb install])
    assert_operator @calls.index(%w[postgresql-release.rb verify]), :<,
                    @calls.index(%w[rabbitmq-release.rb install])
    assert_operator @calls.index(%w[minio-adtof-iam-stage.rb verify]), :<,
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
                    @calls.index(%w[job-api-postgresql-stage.rb reconcile])
    assert_operator @calls.index(%w[job-api-postgresql-stage.rb verify]), :<,
                    @calls.index(%w[rabbitmq-processing-topology.rb reconcile])
    assert_operator @calls.index(%w[rabbitmq-processing-topology.rb verify]), :<,
                    @calls.index(%w[job-api-release.rb install])
    assert @calls.all? { |call| (call & %w[adopt upgrade smoke delete apply]).empty? }
    assert_includes @output.string, 'application bootstrap remains pending'
    assert_empty @error.string
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
