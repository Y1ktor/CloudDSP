# Render the actual identity chart and inspect delivery order and permissions.
# Detect a changed issuer, database reference, Pod template or management-port
# exposure before applying configuration to an existing authentication service.
require 'minitest/autorun'
require 'open3'
require 'yaml'
require_relative '../../scripts/lib/paths'
require_relative '../../scripts/stages/keycloak/keycloak-config-verify'

class KeycloakFluxChartTest < Minitest::Test
  ROOT = CloudDSPPaths::KUBERNETES_ROOT

  def test_exact_three_objects_preserve_database_issuer_and_security
    output, error, status = Open3.capture3('helm', 'template', 'clouddsp-keycloak', ROOT.join('helm/keycloak').to_s, '-n', 'clouddsp-data')
    assert status.success?, error
    actual = YAML.load_stream(output).compact
    expected = %w[keycloak-deployment.yaml keycloak-service.yaml keycloak-ingress.yaml].map { |f| YAML.load_file(ROOT.join('services/keycloak', f)) }
    expected.each { |o| o.dig('metadata', 'labels')['app.kubernetes.io/managed-by'] = 'Helm' }
    assert_equal expected.sort_by { |o| [o['kind'], o.dig('metadata', 'name')] }, actual.sort_by { |o| [o['kind'], o.dig('metadata', 'name')] }
    assert_equal 3, actual.length
    workload = actual.find { |o| o['kind'] == 'Deployment' }
    assert_equal 1, workload.dig('spec', 'replicas')
    assert_equal({ 'type' => 'Recreate' }, workload.dig('spec', 'strategy'))
    pod = workload.dig('spec', 'template', 'spec')
    refute pod['automountServiceAccountToken']
    assert pod.dig('securityContext', 'runAsNonRoot')
    container = pod['containers'].first
    assert_equal YAML.load_file(ROOT.join('images.lock.yaml')).dig('images', 'keycloak', 'immutableReference'), container['image']
    assert_equal ['start'], container['args']
    refute container.dig('securityContext', 'allowPrivilegeEscalation')
    assert_equal ['ALL'], container.dig('securityContext', 'capabilities', 'drop')
    env = container.fetch('env').to_h { |e| [e.fetch('name'), e] }
    assert_equal 'postgres', env.fetch('KC_DB').fetch('value')
    assert_equal 'jdbc:postgresql://clouddsp-postgresql:5432/$(KEYCLOAK_DB_NAME)', env.fetch('KC_DB_URL').fetch('value')
    assert_equal 'http://keycloak.localhost:8080', env.fetch('KC_HOSTNAME').fetch('value')
    assert_equal 'S256', KeycloakConfigVerify.new.load_desired.dig(:react, 'attributes', 'pkce.code.challenge.method')
    assert_equal %w[KEYCLOAK_DB_NAME KC_DB_USERNAME KC_DB_PASSWORD KC_BOOTSTRAP_ADMIN_USERNAME KC_BOOTSTRAP_ADMIN_PASSWORD], container['env'].select { |v| v['valueFrom'] }.map { |v| v['name'] }
    service = actual.find { |o| o['kind'] == 'Service' }
    assert_equal [8080], service.dig('spec', 'ports').map { |p| p['port'] }
    assert_equal 'keycloak.localhost', actual.find { |o| o['kind'] == 'Ingress' }.dig('spec', 'rules', 0, 'host')
    refute actual.any? { |o| %w[StatefulSet Secret PersistentVolumeClaim PersistentVolume Job ConfigMap].include?(o['kind']) }
  end

  def test_named_delivery_scope_and_identity_client_order
    root = ROOT.join('gitops/clusters/clouddsp-local')
    assert_includes YAML.load_file(root.join('kustomization.yaml')).fetch('resources'), 'keycloak'
    assert_includes YAML.load_stream(root.join('flux-system/gotk-sync.yaml').read).first.dig('spec', 'sparseCheckout'), 'k8Deployment/kubernetes/helm/keycloak'
    hr = YAML.load_file(root.join('keycloak/helmrelease.yaml'))
    assert_equal [{ 'name' => 'clouddsp-postgresql', 'namespace' => 'flux-system' }, { 'name' => 'clouddsp-mailpit', 'namespace' => 'flux-system' }], hr.dig('spec', 'dependsOn')
    assert_equal 'clouddsp-data', hr.dig('spec', 'storageNamespace')
    assert_equal '7m', hr.dig('spec', 'timeout')
    assert_equal({ 'mode' => 'enabled' }, hr.dig('spec', 'driftDetection'))
    refute hr['spec'].key?('values')
    refute hr['spec'].key?('valuesFrom')
    %w[frontend job-api].each do |client|
      assert_includes YAML.load_file(root.join(client, 'helmrelease.yaml')).dig('spec', 'dependsOn'), { 'name' => 'clouddsp-keycloak', 'namespace' => 'flux-system' }
    end
    docs = YAML.load_stream(root.join('keycloak/reconciliation-rbac.yaml').read)
    assert_equal %w[ServiceAccount Role RoleBinding], docs.map { |o| o['kind'] }
    role = docs.find { |o| o['kind'] == 'Role' }
    assert_equal 'clouddsp-data', role.dig('metadata', 'namespace')
    assert role['rules'].any? { |r| r['resources'] == ['deployments'] && r['resourceNames'] == ['clouddsp-keycloak'] && r['verbs'] == %w[get update patch delete] }
    assert role['rules'].any? { |r| r['resources'] == ['services'] && r['resourceNames'] == ['clouddsp-keycloak'] }
    assert role['rules'].any? { |r| r['resources'] == ['ingresses'] && r['resourceNames'] == ['clouddsp-keycloak'] }
    assert role['rules'].any? { |r| r['apiGroups'] == [''] && r['resources'] == ['pods'] && r['verbs'] == %w[get list watch] }
    assert role['rules'].any? { |r| r['apiGroups'] == ['apps'] && r['resources'] == ['replicasets'] && r['verbs'] == %w[get list watch] }
    refute role['rules'].any? { |r| (r['resources'] & %w[jobs pods/exec statefulsets persistentvolumeclaims persistentvolumes namespaces networkpolicies]).any? }
    assert role['rules'].any? { |r| r['resources'] == ['secrets'] && r['verbs'] == %w[get list watch create update patch delete] }
  end
end
