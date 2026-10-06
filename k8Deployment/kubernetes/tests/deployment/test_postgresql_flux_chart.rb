# Render the real database chart and inspect its delivery boundary. These checks
# catch accidental claim/security changes and an incomplete database dependency
# graph before Flux can apply either configuration to authoritative storage.
require 'minitest/autorun'
require 'open3'
require 'yaml'
require_relative '../../scripts/lib/paths'

class PostgreSQLFluxChartTest < Minitest::Test
  ROOT = CloudDSPPaths::KUBERNETES_ROOT

  def test_exact_three_object_chart_retains_storage_security_and_image
    output, error, status = Open3.capture3('helm', 'template', 'clouddsp-postgresql', ROOT.join('helm/postgresql').to_s, '-n', 'clouddsp-data')
    assert status.success?, error
    actual = YAML.load_stream(output).compact
    expected = %w[postgresql-statefulset.yaml postgresql-service.yaml postgresql-headless-service.yaml].map { |f| YAML.load_file(ROOT.join('services/postgresql', f)) }
    expected.each { |o| o.dig('metadata', 'labels')['app.kubernetes.io/managed-by'] = 'Helm' }
    assert_equal expected.sort_by { |o| [o['kind'], o.dig('metadata', 'name')] }, actual.sort_by { |o| [o['kind'], o.dig('metadata', 'name')] }
    workload = actual.find { |o| o['kind'] == 'StatefulSet' }
    assert_equal 1, workload.dig('spec', 'replicas')
    assert_equal 'clouddsp-postgresql-headless', workload.dig('spec', 'serviceName')
    assert_equal({ 'whenDeleted' => 'Retain', 'whenScaled' => 'Retain' }, workload.dig('spec', 'persistentVolumeClaimRetentionPolicy'))
    claim = workload.dig('spec', 'volumeClaimTemplates').first
    assert_equal 'postgres-data', claim.dig('metadata', 'name')
    assert_equal '5Gi', claim.dig('spec', 'resources', 'requests', 'storage')
    assert_equal 'local-path', claim.dig('spec', 'storageClassName')
    assert_equal ['ReadWriteOnce'], claim.dig('spec', 'accessModes')
    pod = workload.dig('spec', 'template', 'spec')
    refute pod['automountServiceAccountToken']
    assert_equal 999, pod.dig('securityContext', 'runAsUser')
    container = pod['containers'].first
    assert_equal YAML.load_file(ROOT.join('images.lock.yaml')).dig('images', 'postgresql', 'immutableReference'), container['image']
    refute container.dig('securityContext', 'allowPrivilegeEscalation')
    assert_equal ['ALL'], container.dig('securityContext', 'capabilities', 'drop')
    assert_equal %w[POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD], container['env'].select { |v| v['valueFrom'] }.map { |v| v['name'] }
    refute actual.any? { |o| %w[Secret PersistentVolumeClaim PersistentVolume Job ConfigMap].include?(o['kind']) }
  end

  def test_delivery_scope_and_database_client_order
    root = ROOT.join('gitops/clusters/clouddsp-local')
    assert_includes YAML.load_file(root.join('kustomization.yaml')).fetch('resources'), 'postgresql'
    assert_includes YAML.load_stream(root.join('flux-system/gotk-sync.yaml').read).first.dig('spec', 'sparseCheckout'), 'k8Deployment/kubernetes/helm/postgresql'
    %w[job-api upload-intake dispatcher generic-dispatcher scaling-auth].each do |component|
      assert_includes YAML.load_file(root.join(component, 'helmrelease.yaml')).dig('spec', 'dependsOn'), { 'name' => 'clouddsp-postgresql', 'namespace' => 'flux-system' }
    end
    %w[adtof basic-pitch demucs].each do |component|
      assert_includes YAML.load_file(root.join(component, 'helmrelease.yaml')).dig('spec', 'dependsOn'), { 'name' => 'clouddsp-scaling-auth', 'namespace' => 'flux-system' }
    end
    hr = YAML.load_file(root.join('postgresql/helmrelease.yaml'))
    assert_equal 'clouddsp-data', hr.dig('spec', 'storageNamespace')
    refute hr['spec'].key?('dependsOn') # Database must not wait for its own clients.
    refute hr['spec'].key?('values')
    refute hr['spec'].key?('valuesFrom')
    assert_equal({ 'mode' => 'enabled' }, hr.dig('spec', 'driftDetection'))
    docs = YAML.load_stream(root.join('postgresql/reconciliation-rbac.yaml').read)
    assert_equal %w[ServiceAccount Role RoleBinding], docs.map { |o| o['kind'] }
    role = docs.find { |o| o['kind'] == 'Role' }
    assert_equal 'clouddsp-data', role.dig('metadata', 'namespace')
    rules = role['rules']
    assert rules.any? { |r| r['resources'] == ['statefulsets'] && r['resourceNames'] == ['clouddsp-postgresql'] && r['verbs'] == %w[get update patch delete] }
    assert rules.any? { |r| r['resources'] == ['services'] && r['resourceNames'] == %w[clouddsp-postgresql clouddsp-postgresql-headless] }
    assert rules.any? { |r| r['resources'] == %w[pods persistentvolumeclaims] && r['verbs'] == %w[get list watch] }
    refute rules.any? { |r| (r['resources'] & %w[jobs pods/exec persistentvolumes namespaces deployments networkpolicies]).any? }
    assert rules.any? { |r| r['resources'] == ['secrets'] && (r['verbs'] & %w[get list create patch delete]) == %w[get list create patch delete] }
  end
end
