require 'minitest/autorun'
require 'stringio'
require 'yaml'

require_relative '../../scripts/releases/keda-release-stage'

class KedaReleaseStageTest < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  def setup
    @calls = []
    @output = StringIO.new
    @error = StringIO.new
    @installed = false
    @orphan_crd = false
    @drift_values = false
  end

  def runner(*arguments)
    @calls << arguments
    if arguments.first == 'bash'
      @installed = true
      output = ''
    else
      output = fake_read(arguments)
    end
    [output, '', Status.new(0)]
  end

  def fake_read(arguments)
    if arguments.first == 'helm' && arguments.include?('list')
      releases = @installed ? [{ 'name' => 'keda', 'namespace' => 'keda',
                                 'chart' => 'keda-2.20.2', 'status' => 'deployed' }] : []
      return JSON.generate(releases)
    end
    if arguments.first == 'helm' && arguments.include?('get')
      values = YAML.load_file(KedaReleaseStage::ROOT.join('helm', 'keda', 'values.yaml').to_s)
      values['watchNamespace'] = 'wrong-namespace' if @drift_values
      return YAML.dump(values)
    end
    if arguments.first == 'kubectl' && arguments.include?('namespace')
      return @installed ? "namespace/keda\n" : ''
    end
    if arguments.first == 'kubectl' && arguments.include?('crds')
      names = @installed ? KedaReleaseStage::CRDS : (@orphan_crd ? [KedaReleaseStage::CRDS.first] : [])
      return JSON.generate('items' => names.map { |name| { 'metadata' => { 'name' => name } } })
    end
    if arguments.first == 'kubectl' && arguments.include?('apiservice')
      return @installed ? "apiservice.apiregistration.k8s.io/#{KedaReleaseStage::METRICS_API}\n" : ''
    end
    return '' if arguments.first == 'kubectl' && arguments.include?('rollout')

    flunk "unexpected command #{arguments.inspect}"
  end

  def stage
    KedaReleaseStage.new(command: method(:runner), output: @output, error: @error)
  end

  def test_fresh_install_checks_absence_then_verifies_all_controllers_and_crds
    assert_equal 0, stage.run('install')
    install_index = @calls.index { |command| command.first == 'bash' }
    refute_nil install_index
    assert @calls.take(install_index).any? { |command| command.include?('crds') }
    assert_equal 3, @calls.count { |command| command.include?('rollout') }
    assert_includes @output.string, 'KEDA release install:'
    assert_empty @error.string
  end

  def test_plan_is_read_only_when_keda_is_absent
    assert_equal 0, stage.run('plan')
    refute @calls.any? { |command| command.first == 'bash' }
    assert_includes @output.string, 'install pending'
  end

  def test_existing_release_blocks_install_but_passes_read_only_verify
    @installed = true
    assert_equal 1, stage.run('install')
    refute @calls.any? { |command| command.first == 'bash' }
    assert_includes @error.string, 'already exists'
    @calls.clear
    @error.truncate(0)
    @error.rewind
    assert_equal 0, stage.run('verify')
    assert_equal 3, @calls.count { |command| command.include?('rollout') }
  end

  def test_orphan_crd_blocks_fresh_install_before_helm_write
    @orphan_crd = true
    assert_equal 1, stage.run('install')
    refute @calls.any? { |command| command.first == 'bash' }
    assert_includes @error.string, 'CRDs already exist'
  end

  def test_values_drift_fails_verification
    @installed = true
    @drift_values = true
    assert_equal 1, stage.run('verify')
    assert_includes @error.string, 'values differ'
  end
end
