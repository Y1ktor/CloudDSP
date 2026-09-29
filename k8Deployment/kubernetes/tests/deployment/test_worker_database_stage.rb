require 'minitest/autorun'
require 'stringio'
require 'tmpdir'
require 'yaml'
require_relative '../../scripts/worker-database-stage'

class WorkerDatabaseStageTest < Minitest::Test
  def with_sources(worker)
    Dir.mktmpdir('clouddsp-worker-db-') do |directory|
      %W[#{worker}-database-credentials #{worker}-database-bootstrap-credentials].each do |name|
        example = CloudDSPWorkerDatabaseStage::ROOT.join('services', worker, "#{name}.secret.example.yaml")
        source = YAML.load_file(example)
        password_key = source.fetch('stringData').keys.find { |key| key.end_with?('_DB_PASSWORD') }
        source.fetch('stringData')[password_key] = 'test-only-password-1234'
        File.write(File.join(directory, "#{name}.secret.yaml"), YAML.dump(source))
      end
      yield directory
    end
  end

  def test_both_versioned_worker_jobs_keep_the_restricted_bootstrap_contract
    CloudDSPWorkerDatabaseStage::WORKERS.each_key do |worker|
      stage = CloudDSPWorkerDatabaseStage.new(worker)
      stage.send(:validate_job)
    end
  end

  def test_mismatched_namespace_credentials_stop_before_cluster_access
    with_sources('basic-pitch') do |directory|
      path = File.join(directory, 'basic-pitch-database-bootstrap-credentials.secret.yaml')
      source = YAML.load_file(path)
      source.fetch('stringData')['BASIC_PITCH_DB_PASSWORD'] = 'different-test-password'
      File.write(path, YAML.dump(source))
      calls = []
      runner = lambda { |*args| calls << args; raise 'cluster access is forbidden' }
      output = StringIO.new
      error = StringIO.new
      stage = CloudDSPWorkerDatabaseStage.new('basic-pitch', command: runner, local: directory,
                                              output: output, error: error)

      assert_equal 1, stage.run('bootstrap')
      assert_empty calls
      assert_empty output.string
      assert_includes error.string, 'runtime and temporary credentials differ'
      refute_includes error.string, 'different-test-password'
    end
  end

  def test_placeholder_password_stops_before_cluster_access
    with_sources('adtof') do |directory|
      %w[adtof-database-credentials adtof-database-bootstrap-credentials].each do |name|
        path = File.join(directory, "#{name}.secret.yaml")
        source = YAML.load_file(path)
        source.fetch('stringData')['ADTOF_DB_PASSWORD'] = 'REPLACE_WITH_PASSWORD'
        File.write(path, YAML.dump(source))
      end
      error = StringIO.new
      stage = CloudDSPWorkerDatabaseStage.new('adtof', command: ->(*_args) { flunk 'unexpected cluster access' },
                                              local: directory, error: error)

      assert_equal 1, stage.run('plan')
      assert_includes error.string, 'placeholder'
    end
  end
end
