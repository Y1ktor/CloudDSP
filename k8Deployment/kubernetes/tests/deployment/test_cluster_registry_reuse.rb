require 'minitest/autorun'
require 'open3'
require 'tmpdir'
require 'yaml'

class ClusterRegistryReuseTest < Minitest::Test
  ENTRYPOINT = File.expand_path('../../scripts/cluster.sh', __dir__)

  def with_fake_tools
    Dir.mktmpdir('clouddsp-registry-reuse-') do |directory|
      # Only these exact discovery and create commands are modeled. The fake
      # k3d copies the config it receives before cluster.sh removes its
      # temporary variant, allowing us to inspect the requested registry mode.
      docker = File.join(directory, 'docker')
      File.write(docker, <<~'SCRIPT')
        #!/bin/sh
        set -eu
        case "$1 ${2:-}" in
          'info ') ;;
          'info --format')
            if [ "${FAKE_DOCKER_REGISTRY_SECURE:-0}" = 1 ]; then
              printf '{"clouddsp-registry.localhost:5001":{"Secure":true}}\n'
            else
              printf '{"clouddsp-registry.localhost:5001":{"Secure":false}}\n'
            fi ;;
          'inspect clouddsp-registry.localhost')
            [ -f "$FAKE_REGISTRY" ] || exit 1
            case "$4" in
              *k3d.cluster*)
                if [ -f "$FAKE_OWNER" ]; then printf 'clouddsp-local\n'; else printf '\n'; fi ;;
              *Mounts*) printf 'fake-registry-volume\n' ;;
              *) exit 9 ;;
            esac ;;
          'container inspect')
            case "$3" in
              clouddsp-registry.localhost-migration-backup) [ -f "$FAKE_BACKUP" ] ;;
              clouddsp-registry.localhost) [ -f "$FAKE_REGISTRY" ] ;;
              k3d-clouddsp-registry.localhost) [ -f "$FAKE_PREFIX_REGISTRY" ] ;;
              *) exit 9 ;;
            esac ;;
          'stop clouddsp-registry.localhost') [ -f "$FAKE_REGISTRY" ] ;;
          'start clouddsp-registry.localhost') [ -f "$FAKE_REGISTRY" ] ;;
          'rename clouddsp-registry.localhost')
            [ "$3" = clouddsp-registry.localhost-migration-backup ] || exit 9
            mv "$FAKE_REGISTRY" "$FAKE_BACKUP"
            mv "$FAKE_OWNER" "$FAKE_BACKUP_OWNER" ;;
          'rename k3d-clouddsp-registry.localhost')
            [ "$3" = clouddsp-registry.localhost ] || exit 9
            mv "$FAKE_PREFIX_REGISTRY" "$FAKE_REGISTRY" ;;
          'rename clouddsp-registry.localhost-migration-backup')
            [ "$3" = clouddsp-registry.localhost ] || exit 9
            mv "$FAKE_BACKUP" "$FAKE_REGISTRY"
            mv "$FAKE_BACKUP_OWNER" "$FAKE_OWNER" ;;
          'rm clouddsp-registry.localhost-migration-backup')
            rm "$FAKE_BACKUP" "$FAKE_BACKUP_OWNER" ;;
          *) exit 9 ;;
        esac
      SCRIPT
      File.chmod(0o755, docker)

      curl = File.join(directory, 'curl')
      File.write(curl, <<~'SCRIPT')
        #!/bin/sh
        [ -f "$FAKE_REGISTRY" ] || exit 1
        printf '{"repositories":["retention-probe"]}\n'
      SCRIPT
      File.chmod(0o755, curl)

      kubectl = File.join(directory, 'kubectl')
      File.write(kubectl, "#!/bin/sh\n[ \"$1\" = --context ] || exit 9\n")
      File.chmod(0o755, kubectl)

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
            if [ -f "$FAKE_CLUSTER" ]; then printf 'clouddsp-local 1/1 2/2 true\n'; fi ;;
          'registry list clouddsp-registry.localhost')
            if [ -f "$FAKE_REGISTRY" ]; then printf 'clouddsp-registry.localhost registry running\n'; fi ;;
          'registry list --no-headers')
            if [ -f "$FAKE_REGISTRY" ]; then printf 'clouddsp-registry.localhost registry running\n'; fi ;;
          'registry create clouddsp-registry.localhost')
            touch "$FAKE_PREFIX_REGISTRY"
            printf 'registry-create\n' >> "$FAKE_CALLS" ;;
          'cluster create --config')
            cp -- "$4" "$FAKE_CAPTURED_CONFIG"
            printf 'cluster-create\n' >> "$FAKE_CALLS"
            touch "$FAKE_CLUSTER" ;;
          *) exit 9 ;;
        esac
      SCRIPT
      File.chmod(0o755, k3d)

      environment = {
        'PATH' => "#{directory}:#{ENV.fetch('PATH')}",
        'FAKE_CLUSTER' => File.join(directory, 'cluster-present'),
        'FAKE_REGISTRY' => File.join(directory, 'registry-present'),
        'FAKE_PREFIX_REGISTRY' => File.join(directory, 'prefixed-registry-present'),
        'FAKE_OWNER' => File.join(directory, 'legacy-owner-present'),
        'FAKE_BACKUP' => File.join(directory, 'registry-backup-present'),
        'FAKE_BACKUP_OWNER' => File.join(directory, 'registry-backup-owner-present'),
        'FAKE_CALLS' => File.join(directory, 'calls'),
        'FAKE_CAPTURED_CONFIG' => File.join(directory, 'received-k3d.yaml')
      }
      yield environment
    end
  end

  def test_fresh_creation_uses_standalone_registry_and_reviewed_cluster_configuration
    with_fake_tools do |environment|
      _output, error, status = Open3.capture3(environment, ENTRYPOINT, 'create')

      assert status.success?, error
      registry = YAML.load_file(environment.fetch('FAKE_CAPTURED_CONFIG')).fetch('registries')
      assert_equal ['clouddsp-registry.localhost:5001'], registry.fetch('use')
      refute registry.key?('create')
      assert File.exist?(environment.fetch('FAKE_REGISTRY'))
      assert_equal %w[registry-create cluster-create], File.readlines(environment.fetch('FAKE_CALLS'), chomp: true)
    end
  end

  def test_recreation_attaches_to_retained_registry_without_creating_it
    with_fake_tools do |environment|
      File.write(environment.fetch('FAKE_REGISTRY'), '')

      _output, error, status = Open3.capture3(environment, ENTRYPOINT, 'create')

      assert status.success?, error
      registry = YAML.load_file(environment.fetch('FAKE_CAPTURED_CONFIG')).fetch('registries')
      assert_equal ['clouddsp-registry.localhost:5001'], registry.fetch('use')
      refute registry.key?('create')
      assert File.exist?(environment.fetch('FAKE_REGISTRY'))
      assert_equal ['cluster-create'], File.readlines(environment.fetch('FAKE_CALLS'), chomp: true)
    end
  end

  def test_legacy_retained_registry_is_migrated_with_its_image_volume
    with_fake_tools do |environment|
      File.write(environment.fetch('FAKE_REGISTRY'), '')
      File.write(environment.fetch('FAKE_OWNER'), '')

      _output, error, status = Open3.capture3(environment, ENTRYPOINT, 'create')

      assert status.success?, error
      assert_equal %w[registry-create cluster-create], File.readlines(environment.fetch('FAKE_CALLS'), chomp: true)
      assert File.exist?(environment.fetch('FAKE_REGISTRY'))
      refute File.exist?(environment.fetch('FAKE_BACKUP'))
      refute File.exist?(environment.fetch('FAKE_OWNER'))
      assert_equal ['clouddsp-registry.localhost:5001'],
                   YAML.load_file(environment.fetch('FAKE_CAPTURED_CONFIG')).fetch('registries').fetch('use')
    end
  end

  def test_secure_docker_registry_setting_stops_before_creation
    with_fake_tools do |environment|
      environment['FAKE_DOCKER_REGISTRY_SECURE'] = '1'

      _output, error, status = Open3.capture3(environment, ENTRYPOINT, 'create')

      refute status.success?
      assert_includes error, 'insecure-registries'
      refute File.exist?(environment.fetch('FAKE_CALLS'))
    end
  end
end
