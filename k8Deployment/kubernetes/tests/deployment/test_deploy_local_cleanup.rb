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
      File.write(docker, <<~'SCRIPT')
        #!/bin/sh
        set -eu
        case "$1 ${2:-}" in
          'info ') ;;
          'network inspect')
            case "$3" in
              clouddsp-registry-hold)
                [ -f "$FAKE_HOLD" ] || exit 1
                case "${5:-}" in
                  *Labels*) printf 'registry-hold\n' ;;
                  *Containers*)
                    if [ -f "$FAKE_CONNECTED" ]; then printf 'clouddsp-registry.localhost\n'; fi ;;
                esac ;;
              k3d-clouddsp-local)
                [ -f "$FAKE_CLUSTER_NETWORK" ] || exit 1
                case "${5:-}" in
                  *Labels*) printf 'k3d\n' ;;
                  *Containers*)
                    if [ -f "$FAKE_CLUSTER_ATTACHED" ]; then printf 'clouddsp-registry.localhost\n'; fi ;;
                esac ;;
              *) exit 9 ;;
            esac ;;
          'network create')
            [ "$7" = clouddsp-registry-hold ] || exit 9
            touch "$FAKE_HOLD"
            printf 'network-create\n' >> "$FAKE_CALLS" ;;
          'network connect')
            [ "$3 $4" = 'clouddsp-registry-hold clouddsp-registry.localhost' ] || exit 9
            touch "$FAKE_CONNECTED"
            printf 'network-connect\n' >> "$FAKE_CALLS" ;;
          'network disconnect')
            [ "$3 $4" = 'k3d-clouddsp-local clouddsp-registry.localhost' ] || exit 9
            rm -- "$FAKE_CLUSTER_ATTACHED"
            printf 'network-disconnect\n' >> "$FAKE_CALLS" ;;
          'network rm')
            case "$3" in
              clouddsp-registry-hold)
                rm -- "$FAKE_HOLD" "$FAKE_CONNECTED"
                printf 'network-remove\n' >> "$FAKE_CALLS" ;;
              k3d-clouddsp-local)
                [ ! -f "$FAKE_CLUSTER_ATTACHED" ] || exit 9
                rm -- "$FAKE_CLUSTER_NETWORK"
                printf 'cluster-network-remove\n' >> "$FAKE_CALLS" ;;
              *) exit 9 ;;
            esac ;;
          'inspect clouddsp-registry.localhost')
            [ -f "$FAKE_REGISTRY" ] || exit 9
            printf 'clouddsp-registry-volume\n' ;;
          'volume inspect')
            [ "$3" = clouddsp-registry-volume ] || exit 9
            [ -f "$FAKE_REGISTRY_VOLUME" ] ;;
          'volume rm')
            [ "$3" = clouddsp-registry-volume ] || exit 9
            rm -- "$FAKE_REGISTRY_VOLUME"
            printf 'volume-remove\n' >> "$FAKE_CALLS" ;;
          *) exit 9 ;;
        esac
      SCRIPT
      File.chmod(0o755, docker)

      k3d = File.join(directory, 'k3d')
      File.write(k3d, <<~'SCRIPT')
        #!/bin/sh
        set -eu
        case "$1 $2 $3" in
          'cluster list clouddsp-local')
            if [ -f "$FAKE_CLUSTER" ]; then
              printf 'clouddsp-local 1/1 2/2 true\n'
            else
              printf 'clouddsp-local 0/0 0/0 false\n'
            fi ;;
          'cluster list --no-headers')
            if [ -f "$FAKE_CLUSTER" ]; then
              printf 'clouddsp-local 1/1 2/2 true\n'
            fi ;;
          'registry list clouddsp-registry.localhost'|'registry list --no-headers')
            if [ -f "$FAKE_REGISTRY" ]; then
              printf 'clouddsp-registry.localhost registry running\n'
            fi ;;
          'cluster delete clouddsp-local')
            printf 'cluster\n' >> "$FAKE_CALLS"
            rm -- "$FAKE_CLUSTER"
            if [ ! -f "$FAKE_CLUSTER_ATTACHED" ]; then rm -f -- "$FAKE_CLUSTER_NETWORK"; fi
            # Model k3d v5.9: a registry on only the cluster/default networks
            # is deleted as part of cluster deletion.
            if [ ! -f "$FAKE_CONNECTED" ]; then rm -f -- "$FAKE_REGISTRY"; fi ;;
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
        'FAKE_HOLD' => File.join(directory, 'retention-network-present'),
        'FAKE_CONNECTED' => File.join(directory, 'registry-connected'),
        'FAKE_CLUSTER_NETWORK' => File.join(directory, 'cluster-network-present'),
        'FAKE_CLUSTER_ATTACHED' => File.join(directory, 'registry-attached-to-cluster'),
        'FAKE_REGISTRY_VOLUME' => File.join(directory, 'registry-volume-present'),
        'FAKE_CALLS' => File.join(directory, 'calls')
      }
      yield environment
    end
  end

  def test_root_cleanup_deletes_cluster_and_retains_registry
    with_fake_k3d do |environment|
      File.write(environment.fetch('FAKE_CLUSTER'), '')
      File.write(environment.fetch('FAKE_REGISTRY'), '')
      File.write(environment.fetch('FAKE_CLUSTER_NETWORK'), '')
      File.write(environment.fetch('FAKE_CLUSTER_ATTACHED'), '')

      output, error, status = Open3.capture3(environment, ENTRYPOINT, 'cleanup')

      assert status.success?, error
      assert_includes output, 'cleanup completed'
      assert_equal %w[network-create network-connect network-disconnect cluster], File.readlines(environment.fetch('FAKE_CALLS'), chomp: true)
      refute File.exist?(environment.fetch('FAKE_CLUSTER'))
      assert File.exist?(environment.fetch('FAKE_REGISTRY'))
      assert File.exist?(environment.fetch('FAKE_HOLD'))
      refute File.exist?(environment.fetch('FAKE_CLUSTER_NETWORK'))
    end
  end

  def test_root_cleanup_is_successful_when_cluster_is_absent
    with_fake_k3d do |environment|
      output, error, status = Open3.capture3(environment, ENTRYPOINT, 'cleanup')

      assert status.success?, error
      assert_includes output, 'already absent'
      refute File.exist?(environment.fetch('FAKE_CALLS'))
    end
  end

  def test_cleanup_repairs_orphan_cluster_network_after_prior_deletion
    with_fake_k3d do |environment|
      File.write(environment.fetch('FAKE_REGISTRY'), '')
      File.write(environment.fetch('FAKE_CLUSTER_NETWORK'), '')
      File.write(environment.fetch('FAKE_CLUSTER_ATTACHED'), '')

      output, error, status = Open3.capture3(environment, ENTRYPOINT, 'cleanup')

      assert status.success?, error
      assert_includes output, 'already absent'
      assert_equal %w[network-disconnect cluster-network-remove], File.readlines(environment.fetch('FAKE_CALLS'), chomp: true)
      refute File.exist?(environment.fetch('FAKE_CLUSTER_NETWORK'))
    end
  end

  def test_registry_purge_refuses_an_active_cluster
    with_fake_k3d do |environment|
      File.write(environment.fetch('FAKE_CLUSTER'), '')
      File.write(environment.fetch('FAKE_REGISTRY'), '')

      _output, error, status = Open3.capture3(environment, ENTRYPOINT, 'purge-registry')

      refute status.success?
      assert_includes error, 'cluster still exists'
      assert File.exist?(environment.fetch('FAKE_REGISTRY'))
      refute File.exist?(environment.fetch('FAKE_CALLS'))
    end
  end

  def test_registry_purge_deletes_only_the_fixed_registry_after_cleanup
    with_fake_k3d do |environment|
      File.write(environment.fetch('FAKE_REGISTRY'), '')
      File.write(environment.fetch('FAKE_HOLD'), '')
      File.write(environment.fetch('FAKE_CONNECTED'), '')
      File.write(environment.fetch('FAKE_REGISTRY_VOLUME'), '')

      output, error, status = Open3.capture3(environment, ENTRYPOINT, 'purge-registry')

      assert status.success?, error
      assert_includes output, 'purge completed'
      assert_equal %w[registry volume-remove network-remove], File.readlines(environment.fetch('FAKE_CALLS'), chomp: true)
      refute File.exist?(environment.fetch('FAKE_REGISTRY'))
      refute File.exist?(environment.fetch('FAKE_REGISTRY_VOLUME'))
      refute File.exist?(environment.fetch('FAKE_HOLD'))
    end
  end
end
