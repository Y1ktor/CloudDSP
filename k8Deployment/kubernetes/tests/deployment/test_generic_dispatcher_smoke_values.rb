# Verify pause/restore preserves every non-maintenance HelmRelease setting.
# The editor stages desired Git configuration only; no cluster API is faked.
require 'minitest/autorun'
require_relative '../../scripts/gitops/generic-dispatcher-smoke-values'

class GenericDispatcherSmokeValuesTest < Minitest::Test
  def text
    GenericDispatcherSmokeValues::MANIFEST.read
  end

  def test_only_pause_file_changes_and_restore_is_exact
    paused = GenericDispatcherSmokeValues.changed_text(text, 'pause')
    expected = YAML.safe_load(text)
    expected.dig('spec', 'chart', 'spec', 'valuesFiles') << GenericDispatcherFluxOwnership::PAUSE_FILE
    assert_equal expected, YAML.safe_load(paused)
    assert_equal paused, GenericDispatcherSmokeValues.changed_text(paused, 'pause')
    assert_equal text, GenericDispatcherSmokeValues.changed_text(paused, 'restore')
    assert_equal text, GenericDispatcherSmokeValues.changed_text(text, 'restore')
  end

  def test_unknown_state_or_identity_cannot_be_overwritten
    assert_raises(RuntimeError) { GenericDispatcherSmokeValues.changed_text(text, 'delete') }
    assert_raises(RuntimeError) { GenericDispatcherSmokeValues.changed_text(text.sub('targetNamespace: clouddsp-app', 'targetNamespace: other'), 'pause') }
    assert_raises(RuntimeError) { GenericDispatcherSmokeValues.changed_text(text.sub('valuesFiles:', "valuesFiles:\n        - ./other.yaml"), 'pause') }
    assert_raises(RuntimeError) { GenericDispatcherSmokeValues.changed_text(text.sub(GenericDispatcherSmokeValues::INACTIVE, ''), 'pause') }
  end
end
