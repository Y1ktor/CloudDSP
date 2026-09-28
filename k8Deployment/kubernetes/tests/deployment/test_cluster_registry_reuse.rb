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
      File.write(docker, "#!/bin/sh\n[ \"$1\" = info ] || exit 9\n")
      File.chmod(0o755, docker)

      kubectl = File.join(directory, 'kubectl')
      File.write(kubectl, "#!/bin/sh\n[ \"$1\" = --context ] || exit 9\n")
      File.chmod(0o755, kubectl)

      k3d = File.join(directory, 'k3d')
      File.write(k3d, <<~'SCRIPT')
        #!/bin/sh
        set -eu
        case "$1 $2 $3" in
          'cluster list clouddsp-local')
            if [ -f "$FAKE_CLUSTER" ]; then printf 'clouddsp-local 1/1 2/2 true\n'; fi ;;
          'registry list clouddsp-registry.localhost')
            if [ -f "$FAKE_REGISTRY" ]; then printf 'clouddsp-registry.localhost registry running\n'; fi ;;
          'cluster create --config')
            cp -- "$4" "$FAKE_CAPTURED_CONFIG"
            touch "$FAKE_CLUSTER" ;;
          *) exit 9 ;;
        esac
      SCRIPT
      File.chmod(0o755, k3d)

      environment = {
        'PATH' => "#{directory}:#{ENV.fetch('PATH')}",
        'FAKE_CLUSTER' => File.join(directory, 'cluster-present'),
        'FAKE_REGISTRY' => File.join(directory, 'registry-present'),
        'FAKE_CAPTURED_CONFIG' => File.join(directory, 'received-k3d.yaml')
      }
      yield environment
    end
  end

  def test_fresh_creation_uses_reviewed_registry_create_configuration
    with_fake_tools do |environment|
      _output, error, status = Open3.capture3(environment, ENTRYPOINT, 'create')

      assert status.success?, error
      registry = YAML.load_file(environment.fetch('FAKE_CAPTURED_CONFIG')).fetch('registries')
      assert_equal 'clouddsp-registry.localhost', registry.fetch('create').fetch('name')
      refute registry.key?('use')
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
    end
  end
end
