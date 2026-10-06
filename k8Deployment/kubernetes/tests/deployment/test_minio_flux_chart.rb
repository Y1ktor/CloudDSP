# Inspect actual rendering and the entire delivery DAG before allowing Flux to
# touch object storage. Catch claim replacement, public console exposure, hidden
# bootstrap objects and client readiness cycles independently of adapter mocks.
require 'minitest/autorun'
require 'open3'
require 'yaml'
require_relative '../../scripts/lib/paths'

class MinIOFluxChartTest < Minitest::Test
  ROOT = CloudDSPPaths::KUBERNETES_ROOT

  def test_four_object_chart_retains_storage_security_s3_origin_and_image
    output, error, status = Open3.capture3('helm', 'template', 'clouddsp-minio', ROOT.join('helm/minio').to_s, '-n', 'clouddsp-data')
    assert status.success?, error
    actual = YAML.load_stream(output).compact
    expected = %w[minio-statefulset.yaml minio-service.yaml minio-headless-service.yaml minio-s3-ingress.yaml].map { |f| YAML.load_file(ROOT.join('services/minio', f)) }
    expected.each { |o| o.dig('metadata', 'labels')['app.kubernetes.io/managed-by'] = 'Helm' }
    assert_equal expected.sort_by { |o| [o['kind'], o.dig('metadata', 'name')] }, actual.sort_by { |o| [o['kind'], o.dig('metadata', 'name')] }
    assert_equal 4, actual.length
    workload = actual.find { |o| o['kind'] == 'StatefulSet' }
    assert_equal 1, workload.dig('spec', 'replicas')
    assert_equal 'clouddsp-minio-headless', workload.dig('spec', 'serviceName')
    assert_equal({ 'whenDeleted' => 'Retain', 'whenScaled' => 'Retain' }, workload.dig('spec', 'persistentVolumeClaimRetentionPolicy'))
    claim = workload.dig('spec', 'volumeClaimTemplates').first
    assert_equal 'minio-data', claim.dig('metadata', 'name')
    assert_equal '10Gi', claim.dig('spec', 'resources', 'requests', 'storage')
    assert_equal 'local-path', claim.dig('spec', 'storageClassName')
    assert_equal ['ReadWriteOnce'], claim.dig('spec', 'accessModes')
    pod = workload.dig('spec', 'template', 'spec')
    refute pod['automountServiceAccountToken']
    assert_equal 65532, pod.dig('securityContext', 'runAsUser')
    container = pod['containers'].first
    assert_equal YAML.load_file(ROOT.join('images.lock.yaml')).dig('images', 'minio', 'immutableReference'), container['image']
    refute container.dig('securityContext', 'allowPrivilegeEscalation')
    assert_equal ['ALL'], container.dig('securityContext', 'capabilities', 'drop')
    assert_equal %w[MINIO_NOTIFY_AMQP_URL_INTAKE MINIO_ROOT_USER MINIO_ROOT_PASSWORD], container['env'].select { |v| v['valueFrom'] }.map { |v| v['name'] }
    ingress = actual.find { |o| o['kind'] == 'Ingress' }
    assert_equal 'minio.localhost', ingress.dig('spec', 'rules', 0, 'host')
    assert_equal 's3-api', ingress.dig('spec', 'rules', 0, 'http', 'paths', 0, 'backend', 'service', 'port', 'name')
    assert_equal 'http://clouddsp.localhost:8080', container['env'].find { |v| v['name'] == 'MINIO_API_CORS_ALLOW_ORIGIN' }['value']
    refute actual.any? { |o| %w[Secret PersistentVolumeClaim PersistentVolume Job ConfigMap].include?(o['kind']) }
  end

  def test_trusted_delivery_scope_and_acyclic_object_storage_client_order
    root = ROOT.join('gitops/clusters/clouddsp-local')
    assert_includes YAML.load_file(root.join('kustomization.yaml')).fetch('resources'), 'minio'
    assert_includes YAML.load_stream(root.join('flux-system/gotk-sync.yaml').read).first.dig('spec', 'sparseCheckout'), 'k8Deployment/kubernetes/helm/minio'
    %w[job-api upload-intake demucs basic-pitch adtof].each do |client|
      assert_includes YAML.load_file(root.join(client, 'helmrelease.yaml')).dig('spec', 'dependsOn'), { 'name' => 'clouddsp-minio', 'namespace' => 'flux-system' }
    end
    hr = YAML.load_file(root.join('minio/helmrelease.yaml'))
    assert_equal [{ 'name' => 'clouddsp-rabbitmq', 'namespace' => 'flux-system' }], hr.dig('spec', 'dependsOn')
    assert_equal 'clouddsp-data', hr.dig('spec', 'storageNamespace')
    refute hr['spec'].key?('values')
    refute hr['spec'].key?('valuesFrom')
    assert_equal({ 'mode' => 'enabled' }, hr.dig('spec', 'driftDetection'))
    docs = YAML.load_stream(root.join('minio/reconciliation-rbac.yaml').read)
    assert_equal %w[ServiceAccount Role RoleBinding], docs.map { |o| o['kind'] }
    role = docs.find { |o| o['kind'] == 'Role' }
    assert_equal 'clouddsp-data', role.dig('metadata', 'namespace')
    rules = role['rules']
    assert rules.any? { |r| r['resources'] == ['statefulsets'] && r['resourceNames'] == ['clouddsp-minio'] && r['verbs'] == %w[get update patch delete] }
    assert rules.any? { |r| r['resources'] == ['ingresses'] && r['resourceNames'] == ['clouddsp-minio-s3'] }
    assert rules.any? { |r| r['resources'] == %w[pods persistentvolumeclaims] && r['verbs'] == %w[get list watch] }
    refute rules.any? { |r| (r['resources'] & %w[jobs pods/exec persistentvolumes namespaces deployments networkpolicies]).any? }
    assert rules.any? { |r| r['resources'] == ['secrets'] && (r['verbs'] & %w[get list create patch delete]) == %w[get list create patch delete] }
    # Validate every selected dependency: adding a store must not make a cycle
    # through the clients, shared authentication or platform delivery.
    graph = Dir.glob(root.join('*/helmrelease.yaml').to_s).to_h do |path|
      obj = YAML.load_file(path)
      [obj.dig('metadata', 'name'), Array(obj.dig('spec', 'dependsOn')).map { |d| d.fetch('name') }]
    end
    active = []; done = []
    visit = lambda do |name|
      refute_includes active, name, "Flux dependency cycle: #{active.inspect}"
      return if done.include?(name)
      assert graph.key?(name), "Unknown Flux dependency #{name}"
      active << name; graph.fetch(name).each { |child| visit.call(child) }; active.pop; done << name
    end
    graph.each_key { |name| visit.call(name) }
    assert_equal 15, graph.length
  end
end
