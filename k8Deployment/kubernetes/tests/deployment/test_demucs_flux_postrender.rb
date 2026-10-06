# Exercise Helm output through Kustomize's YAML label transformation, which
# Flux uses for origin labels. The previous Basic Pitch handoff exposed extra blank lines in
# the folded PostgreSQL query; exact spec parity must survive this boundary.
require 'minitest/autorun'
require 'open3'
require 'tmpdir'
require 'yaml'
require_relative '../../scripts/lib/paths'

class DemucsFluxPostrenderTest < Minitest::Test
  ROOT = CloudDSPPaths::KUBERNETES_ROOT

  def test_origin_label_postrender_preserves_both_triggers_and_entire_worker_spec
    output, error, status = Open3.capture3('helm', 'template', 'clouddsp-demucs',
      ROOT.join('helm/demucs').to_s, '--namespace', 'clouddsp-app')
    assert status.success?, error
    expected = YAML.load_stream(output).compact
    labels = { 'helm.toolkit.fluxcd.io/name' => 'clouddsp-demucs',
               'helm.toolkit.fluxcd.io/namespace' => 'flux-system' }
    Dir.mktmpdir('demucs-origin-labels') do |directory|
      File.write(File.join(directory, 'resources.yaml'), output)
      # Limit labels to resource metadata: selectors and Pod templates must
      # keep their original identity, as in the live Helm postrenderer.
      File.write(File.join(directory, 'kustomization.yaml'), YAML.dump(
        'apiVersion' => 'kustomize.config.k8s.io/v1beta1', 'kind' => 'Kustomization',
        'resources' => ['resources.yaml'],
        'labels' => [{ 'pairs' => labels, 'includeSelectors' => false, 'includeTemplates' => false }]))
      rendered, error, status = Open3.capture3('kubectl', 'kustomize', directory)
      assert status.success?, error
      expected.each { |object| object.fetch('metadata').fetch('labels').merge!(labels) }
      order = ->(objects) { objects.sort_by { |object| [object['kind'], object.dig('metadata', 'name')] } }
      assert_equal order.call(expected), order.call(YAML.load_stream(rendered).compact)
      scaler = expected.find { |object| object['kind'] == 'ScaledObject' }
      source = YAML.load_file(ROOT.join('services/demucs/demucs-scaledobject.yaml'))
      assert_equal source.dig('spec', 'triggers'), scaler.dig('spec', 'triggers')
    end
  end
end
