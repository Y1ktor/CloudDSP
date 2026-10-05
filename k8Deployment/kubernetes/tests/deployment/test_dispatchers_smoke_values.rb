# Protect the two-manifest staging boundary: unknown identity/overrides must
# fail before a partial pause is written; restoration preserves original text.
require 'minitest/autorun'
require_relative '../../scripts/gitops/dispatchers-smoke-values'

class DispatchersSmokeValuesTest < Minitest::Test
  def texts
    DispatcherSmokeValues::CONFIG.to_h { |component, config| [component, config.fetch(:manifest).read] }
  end

  def test_both_publishers_pause_and_restore_only_their_reviewed_values_line
    normal = texts
    paused = DispatcherSmokeValues.prepared_changes(normal, 'pause')
    assert_equal %w[dispatcher generic-dispatcher], paused.keys
    paused.each do |component, text|
      expected = YAML.safe_load(normal.fetch(component))
      expected.dig('spec', 'chart', 'spec', 'valuesFiles') << DispatcherSmokeValues::CONFIG.fetch(component).fetch(:pause)
      assert_equal expected, YAML.safe_load(text)
    end
    assert_equal normal, DispatcherSmokeValues.prepared_changes(paused, 'restore')
    assert_equal paused, DispatcherSmokeValues.prepared_changes(paused, 'pause')
  end

  def test_one_invalid_publisher_rejects_the_whole_preparation
    %w[dispatcher generic-dispatcher].each do |component|
      %w[targetNamespace storageNamespace].each do |field|
        input = texts
        input[component] = input.fetch(component).sub("#{field}: clouddsp-app", "#{field}: other")
        assert_raises(RuntimeError) { DispatcherSmokeValues.prepared_changes(input, 'pause') }
      end
      input = texts
      input[component] = input.fetch(component).sub('valuesFiles:', "valuesFiles:\n        - ./other.yaml")
      assert_raises(RuntimeError) { DispatcherSmokeValues.prepared_changes(input, 'pause') }
      input = texts
      input[component] = input.fetch(component) + "  values:\n    replicas: 1\n"
      assert_raises(RuntimeError) { DispatcherSmokeValues.prepared_changes(input, 'pause') }
    end
    assert_raises(KeyError) { DispatcherSmokeValues.prepared_changes({ 'frontend' => texts.fetch('dispatcher') }, 'pause') }
    assert_raises(RuntimeError) { DispatcherSmokeValues.prepared_changes({}, 'pause') }
    assert_raises(RuntimeError) { DispatcherSmokeValues.prepared_changes(texts, 'delete') }
  end
end
