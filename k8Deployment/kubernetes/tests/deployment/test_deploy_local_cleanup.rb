require 'minitest/autorun'
require 'open3'
require 'tmpdir'

class DeployLocalCleanupTest < Minitest::Test
  ENTRYPOINT = File.expand_path('../../scripts/deploy-local.sh', __dir__)

  def with_fake_k3d
    Dir.mktmpdir('clouddsp-cleanup-test-') do |directory|
      # The fake tools model only the fixed targets. Any different k3d or
      # Docker operation fails, so the test catches a widened deletion scope.
      docker = File.join(directory, 'docker')
      File.write(docker, "#!/bin/sh\n[ \"$1\" = info ] || exit 9\n")
      File.chmod(0o755, docker)

      k3d = File.join(directory, 'k3d')
      File.write(k3d, <<~'SCRIPT')
        #!/bin/sh
        set -eu
        case "$1 $2 $3" in
          'cluster list clouddsp-local')
            if [ -f "$FAKE_CLUSTER" ]; then
              printf 'clouddsp-local 1/1 2/2 true\n'
            fi ;;
          'registry list clouddsp-registry.localhost')
            if [ -f "$FAKE_REGISTRY" ]; then
              printf 'clouddsp-registry.localhost registry running\n'
            fi ;;
          'cluster delete clouddsp-local')
            printf 'cluster\n' >> "$FAKE_CALLS"
            rm -- "$FAKE_CLUSTER" ;;
          'registry delete clouddsp-registry.localhost')
            printf 'registry\n' >> "$FAKE_CALLS"
            rm -- "$FAKE_REGISTRY" ;;
          *) exit 9 ;;
        esac
      SCRIPT
      File.chmod(0o755, k3d)

      environment = {
        'PATH' => "#{directory}:#{ENV.fetch('PATH')}",
        'FAKE_CLUSTER' => File.join(directory, 'cluster-present'),
        'FAKE_REGISTRY' => File.join(directory, 'registry-present'),
        'FAKE_CALLS' => File.join(directory, 'calls')
      }
      yield environment
    end
  end

  def test_root_cleanup_deletes_only_cluster_then_registry
    with_fake_k3d do |environment|
      File.write(environment.fetch('FAKE_CLUSTER'), '')
      File.write(environment.fetch('FAKE_REGISTRY'), '')

      output, error, status = Open3.capture3(environment, ENTRYPOINT, 'cleanup')

      assert status.success?, error
      assert_includes output, 'cleanup completed'
      assert_equal %w[cluster registry], File.readlines(environment.fetch('FAKE_CALLS'), chomp: true)
      refute File.exist?(environment.fetch('FAKE_CLUSTER'))
      refute File.exist?(environment.fetch('FAKE_REGISTRY'))
    end
  end

  def test_root_cleanup_is_successful_when_both_targets_are_absent
    with_fake_k3d do |environment|
      output, error, status = Open3.capture3(environment, ENTRYPOINT, 'cleanup')

      assert status.success?, error
      assert_includes output, 'already absent'
      refute File.exist?(environment.fetch('FAKE_CALLS'))
    end
  end
end
