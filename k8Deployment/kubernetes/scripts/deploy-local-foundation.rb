#!/usr/bin/env ruby
# Create only the foundation of a fresh local CloudDSP cluster. This stage
# deliberately stops before Helm releases, Secrets, and application bootstrap.
# `bootstrap` refuses an existing cluster or leftover registry so rerunning it
# cannot conceal partially deployed resources or overwrite PVC-backed data.
#
# The versioned k3d configuration owns the node and registry topology. The
# versioned Namespace manifest creates the project's three API scopes. K3s
# retains ownership of kube-system and packaged CoreDNS/Traefik/storage.
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class CloudDSPFoundation
  ROOT = Pathname.new(File.expand_path('..', __dir__)).freeze
  SCRIPT_DIRECTORY = ROOT.join('scripts').freeze
  CLUSTER_CONFIG = ROOT.join('cluster', 'k3d.yaml').freeze
  NAMESPACE_CONFIG = ROOT.join('cluster', 'namespaces.yaml').freeze
  CLUSTER = 'clouddsp-local'.freeze
  CONTEXT = 'k3d-clouddsp-local'.freeze
  REGISTRY = 'clouddsp-registry.localhost'.freeze
  REGISTRY_URL = 'http://clouddsp-registry.localhost:5001/v2/'.freeze
  NODE_ROLES = {
    'k3d-clouddsp-local-server-0' => 'control-plane',
    'k3d-clouddsp-local-agent-0' => 'cpu-worker',
    'k3d-clouddsp-local-agent-1' => 'cpu-worker'
  }.freeze
  SYSTEM_DEPLOYMENTS = %w[coredns traefik local-path-provisioner].freeze

  def initialize(command: Open3.method(:capture3), output: $stdout, error: $stderr)
    @command = command
    @output = output
    @error = error
  end

  def run(mode)
    ensure_true(%w[plan verify bootstrap].include?(mode), 'use plan, verify, or bootstrap')
    namespaces = load_source
    check_prerequisites
    if mode == 'verify'
      verify_foundation(namespaces)
      @output.puts 'CloudDSP foundation verify: cluster, registry, nodes, system controllers, and namespaces ready'
      return 0
    end

    # A fresh cluster may attach to the registry retained by normal cleanup.
    # Existing Kubernetes nodes still stop bootstrap, because their resources
    # need the reviewed reconcile path rather than a second cluster creation.
    ensure_true(!cluster_exists?, 'target k3d cluster already exists; use verify or existing-cluster reconcile')
    retained_registry = registry_exists?
    command('retained registry HTTP readiness', 'curl', '--fail', '--silent', '--show-error', REGISTRY_URL) if retained_registry
    if mode == 'plan'
      @output.puts "CloudDSP foundation plan: target cluster absent; #{retained_registry ? 'retained registry reusable' : 'new registry required'}"
      return 0
    end

    # cluster.sh uses the same reviewed k3d.yaml and is the sole creator of
    # Docker/K3s resources. Never call it before the strict absence checks.
    command('versioned k3d cluster creation', SCRIPT_DIRECTORY.join('cluster.sh').to_s, 'create')
    ensure_true(cluster_exists?, 'cluster creation returned without target k3d cluster')
    ensure_true(registry_exists?, 'cluster creation returned without target registry')
    command('Kubernetes node readiness', 'kubectl', '--context', CONTEXT, 'wait',
            '--for=condition=Ready', 'nodes', '--all', '--timeout=180s')
    command('namespace API dry run', 'kubectl', '--context', CONTEXT, 'create',
            '--dry-run=server', '--filename', NAMESPACE_CONFIG.to_s)
    command('versioned namespace creation', 'kubectl', '--context', CONTEXT, 'create',
            '--filename', NAMESPACE_CONFIG.to_s)
    # K3s installs Traefik through an asynchronous Helm job. Node readiness
    # does not mean its Deployment has been created yet.
    SYSTEM_DEPLOYMENTS.each do |name|
      command("kube-system #{name} creation", 'kubectl', '--context', CONTEXT, '-n', 'kube-system',
              'wait', '--for=create', "deployment/#{name}", '--timeout=120s')
    end
    verify_foundation(namespaces)
    @output.puts 'CloudDSP foundation bootstrap: fresh k3d cluster and three project namespaces ready'
    0
  rescue StandardError => exception
    @error.puts "CloudDSP foundation #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def load_source
    cluster = YAML.load_file(CLUSTER_CONFIG.to_s)
    same('k3d configuration identity',
         [cluster['apiVersion'], cluster['kind'], cluster.dig('metadata', 'name'),
          cluster['image'], cluster['servers'], cluster['agents']],
         ['k3d.io/v1alpha5', 'Simple', CLUSTER, 'docker.io/rancher/k3s:v1.35.5-k3s1', 1, 2])
    same('k3d API loopback', cluster['kubeAPI'], { 'hostIP' => '127.0.0.1', 'hostPort' => '6550' })
    same('k3d registry', cluster.dig('registries', 'create'),
         { 'name' => REGISTRY, 'host' => '127.0.0.1', 'hostPort' => '5001' })
    same('k3d ingress ports', cluster.fetch('ports').map { |entry| entry['port'] }.sort,
         %w[127.0.0.1:8080:80 127.0.0.1:8443:443].sort)
    ensure_true(cluster.fetch('ports').all? { |entry| entry['nodeFilters'] == ['loadbalancer'] },
                'k3d ingress node filter changed')
    same('k3d wait policy', cluster.dig('options', 'k3d', 'wait'), true)
    same('k3d creation timeout', cluster.dig('options', 'k3d', 'timeout'), '120s')
    same('k3d load balancer policy', cluster.dig('options', 'k3d', 'disableLoadbalancer'), false)
    same('k3d node role labels', cluster.dig('options', 'k3s', 'nodeLabels'),
         [{ 'label' => 'clouddsp.io/role=control-plane', 'nodeFilters' => ['server:0'] },
          { 'label' => 'clouddsp.io/role=cpu-worker', 'nodeFilters' => ['agent:*'] }])

    documents = YAML.load_stream(NAMESPACE_CONFIG.read)
    same('source project namespace document count', documents.length, 3)
    namespaces = documents.to_h do |document|
      ensure_true(document['apiVersion'] == 'v1' && document['kind'] == 'Namespace',
                  'source namespace manifest contains another resource kind')
      [document.dig('metadata', 'name'), document.dig('metadata', 'labels')]
    end
    same('source project namespace set', namespaces.keys.sort,
         %w[clouddsp-app clouddsp-data clouddsp-system])
    namespaces.each do |name, labels|
      ensure_true(labels.is_a?(Hash) && labels['app.kubernetes.io/part-of'] == 'clouddsp' &&
                  labels['app.kubernetes.io/managed-by'] == 'kubectl' &&
                  labels['clouddsp.io/environment'] == 'local' &&
                  labels['clouddsp.io/scope'] == name.delete_prefix('clouddsp-'),
                  "source namespace #{name} labels changed")
    end
    namespaces
  end

  def check_prerequisites
    %w[docker k3d kubectl curl].each do |name|
      available = ENV.fetch('PATH', '').split(File::PATH_SEPARATOR).any? do |directory|
        File.executable?(File.join(directory, name))
      end
      ensure_true(available, "required local command #{name} is unavailable")
    end
    command('Docker daemon readiness', 'docker', 'info', '--format', '{{.ServerVersion}}')
  end

  def command(label, *arguments)
    output, _stderr, status = @command.call(*arguments)
    ensure_true(status.success?, "#{label} failed")
    output
  end

  def cluster_exists?
    # A named lookup exits nonzero on a fresh Docker daemon. List all clusters
    # so absence is an ordinary empty result during the first bootstrap.
    command('target k3d cluster lookup', 'k3d', 'cluster', 'list', '--no-headers')
      .lines.any? { |line| line.split.first == CLUSTER }
  end

  def registry_exists?
    command('target k3d registry lookup', 'k3d', 'registry', 'list', '--no-headers')
      .lines.any? { |line| line.split.first == REGISTRY }
  end

  def verify_foundation(namespaces)
    ensure_true(cluster_exists?, 'target k3d cluster is absent')
    ensure_true(registry_exists?, 'target k3d registry is absent')
    command('local registry HTTP readiness', 'curl', '--fail', '--silent', '--show-error', REGISTRY_URL)
    nodes = JSON.parse(command('Kubernetes node lookup', 'kubectl', '--context', CONTEXT,
                               'get', 'nodes', '-o', 'json', '--request-timeout=15s'))
    items = nodes.fetch('items')
    same('k3d node set', items.map { |node| node.dig('metadata', 'name') }.sort, NODE_ROLES.keys.sort)
    items.each do |node|
      name = node.dig('metadata', 'name')
      same("#{name} role", node.dig('metadata', 'labels', 'clouddsp.io/role'), NODE_ROLES.fetch(name))
      ensure_true(node.dig('status', 'conditions').any? do |condition|
        condition['type'] == 'Ready' && condition['status'] == 'True'
      end, "#{name} is not Ready")
    end
    live = JSON.parse(command('project namespace lookup', 'kubectl', '--context', CONTEXT,
                              'get', 'namespaces', '-o', 'json', '--request-timeout=15s'))
    index = live.fetch('items').to_h { |item| [item.dig('metadata', 'name'), item] }
    namespaces.each do |name, labels|
      item = index[name]
      ensure_true(item.is_a?(Hash), "project namespace #{name} is absent")
      same("#{name} phase", item.dig('status', 'phase'), 'Active')
      labels.each { |key, value| same("#{name} label #{key}", item.dig('metadata', 'labels', key), value) }
    end
    SYSTEM_DEPLOYMENTS.each do |name|
      command("kube-system #{name} rollout", 'kubectl', '--context', CONTEXT, '-n', 'kube-system',
              'rollout', 'status', "deployment/#{name}", '--timeout=120s')
    end
  end

  def same(label, actual, expected)
    ensure_true(actual == expected, "#{label} drifted")
  end

  def ensure_true(condition, message)
    raise message unless condition
  end

  def safe_error(error)
    error.instance_of?(RuntimeError) ? error.message : error.class.to_s
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: deploy-local-foundation.rb plan|verify|bootstrap' unless ARGV.length == 1
  exit CloudDSPFoundation.new.run(ARGV.first)
end
