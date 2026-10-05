# Validate real Helm rendering and the versioned stateful delivery boundary.
# Prevent probe fixes or Flux packaging from changing storage, security,
# networking or secret ownership as an accidental side effect.
require 'minitest/autorun'
require 'open3'
require 'yaml'
require_relative '../../scripts/lib/paths'

class RabbitMQFluxChartTest < Minitest::Test
  ROOT = CloudDSPPaths::KUBERNETES_ROOT

  def test_real_chart_matches_the_five_source_objects_and_the_locked_image
    output, error, status = Open3.capture3('helm', 'template', 'clouddsp-rabbitmq', ROOT.join('helm/rabbitmq').to_s, '-n', 'clouddsp-data')
    assert status.success?, error
    rendered = YAML.load_stream(output).compact
    source = %w[rabbitmq-statefulset.yaml rabbitmq-service.yaml rabbitmq-headless-service.yaml rabbitmq-management-service.yaml rabbitmq-ingress-network-policy.yaml].map { |f| YAML.load_file(ROOT.join('services/rabbitmq', f)) }
    source.each { |o| o.dig('metadata', 'labels')['app.kubernetes.io/managed-by'] = 'Helm' }
    assert_equal source.sort_by { |o| [o['kind'], o.dig('metadata', 'name')] }, rendered.sort_by { |o| [o['kind'], o.dig('metadata', 'name')] }
    broker = rendered.find { |o| o['kind'] == 'StatefulSet' }
    container = broker.dig('spec', 'template', 'spec', 'containers').first
    lock = YAML.load_file(ROOT.join('images.lock.yaml')).dig('images', 'rabbitmq', 'immutableReference')
    assert_equal lock, container['image']
    %w[startupProbe readinessProbe].each do |probe|
      assert_equal({ 'tcpSocket' => { 'port' => 'amqp' }, 'periodSeconds' => probe == 'startupProbe' ? 5 : 10,
                     'failureThreshold' => probe == 'startupProbe' ? 36 : 3, 'timeoutSeconds' => 5 }, container[probe])
    end
    refute container.key?('livenessProbe')
    assert_equal({ 'whenDeleted' => 'Retain', 'whenScaled' => 'Retain' }, broker.dig('spec', 'persistentVolumeClaimRetentionPolicy'))
    assert_equal 'rabbitmq-data', broker.dig('spec', 'volumeClaimTemplates', 0, 'metadata', 'name')
    assert_equal '5Gi', broker.dig('spec', 'volumeClaimTemplates', 0, 'spec', 'resources', 'requests', 'storage')
    refute broker.dig('spec', 'template', 'spec', 'automountServiceAccountToken')
    assert container.dig('securityContext', 'capabilities', 'drop').include?('ALL')
    refute rendered.any? { |o| %w[Secret PersistentVolumeClaim Job].include?(o['kind']) }
  end

  def test_flux_scope_orders_broker_clients_without_owning_data_bootstrap
    root = ROOT.join('gitops/clusters/clouddsp-local')
    assert_equal 'disabled', YAML.load_file(root.join('rabbitmq/helmrelease.yaml')).dig('spec', 'upgrade', 'serverSideApply')
    assert YAML.load_file(root.join('kustomization.yaml')).fetch('resources').include?('rabbitmq')
    assert YAML.load_stream(root.join('flux-system/gotk-sync.yaml').read).first.dig('spec', 'sparseCheckout').include?('k8Deployment/kubernetes/helm/rabbitmq')
    %w[upload-intake generic-dispatcher dispatcher scaling-auth].each do |component|
      dependencies = YAML.load_file(root.join(component, 'helmrelease.yaml')).dig('spec', 'dependsOn')
      assert_includes dependencies, { 'name' => 'clouddsp-rabbitmq', 'namespace' => 'flux-system' }
    end
    %w[adtof basic-pitch demucs].each do |worker|
      assert_equal [{ 'name' => 'clouddsp-scaling-auth', 'namespace' => 'flux-system' }], YAML.load_file(root.join(worker, 'helmrelease.yaml')).dig('spec', 'dependsOn')
    end
    rules = YAML.load_stream(root.join('rabbitmq/reconciliation-rbac.yaml').read).find { |o| o['kind'] == 'Role' }.fetch('rules')
    assert rules.any? { |r| r['resources'].include?('persistentvolumeclaims') && r['verbs'] == %w[get list watch] }
    refute rules.any? { |r| (r['resources'] & %w[jobs persistentvolumes]).any? }
  end
end
