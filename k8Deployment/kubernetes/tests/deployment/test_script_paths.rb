require 'minitest/autorun'
require 'fileutils'
require 'json'
require 'open3'
require 'tmpdir'
require_relative '../../scripts/lib/paths'
require_relative '../../scripts/orchestration/deploy-local-platform'
require_relative '../../scripts/orchestration/deploy-local-verify'

class ScriptPathsTest < Minitest::Test
  ENTRYPOINT = CloudDSPPaths::SCRIPT_ROOT.join('deploy-local.sh').to_s

  def test_registry_resolves_every_coordinated_stage_to_an_existing_file
    expected_root = File.expand_path('../..', __dir__)
    assert_equal expected_root, CloudDSPPaths::KUBERNETES_ROOT.to_s
    # Basenames remain the stage identity; the resolver selects their new
    # responsibility directory without changing dependency order or modes.
    names = CloudDSPBootstrapPlatform::STEPS.map { |_label, name, *_arguments| name }
    names += CloudDSPLocalVerify::STAGES.map do |stage|
      stage.command[1] if stage.command.first == 'ruby'
    end.compact
    names.uniq.each do |name|
      path = CloudDSPPaths.script(name)
      assert path.file?, "Missing coordinated stage #{name}"
      assert path.absolute?, "Child command is relative: #{name}"
    end
    registered = CloudDSPPaths::SCRIPTS.values.map(&:to_s).sort
    commands = Dir.glob(CloudDSPPaths::SCRIPT_ROOT.join('**', '*.{rb,sh,py}').to_s)
                  .reject { |path| path == ENTRYPOINT || File.basename(path).match?(/\Apaths\.(?:rb|sh|py)\z/) }.sort
    assert_equal commands, registered, 'Every internal command must have one authoritative location'
  end

  def test_unknown_script_names_fail_before_any_command_is_constructed
    %w[missing.rb ../deploy-local.sh].each do |name|
      assert_raises(KeyError) { CloudDSPPaths.script(name) }
    end
  end

  def test_public_command_routes_to_current_absolute_child_paths_from_any_directory
    Dir.mktmpdir('clouddsp-script-routing') do |temporary|
      bin = File.join(temporary, 'bin')
      log = File.join(temporary, 'argv.json')
      FileUtils.mkdir_p(bin)
      # Only a fake Ruby receives the selected child. No bootstrap, Secret
      # creation, image operation, Helm call, or cluster API can run here.
      ruby = File.join(bin, 'ruby')
      File.write(ruby, <<~RUBY)
        #!#{RbConfig.ruby}
        require 'json'
        File.write(ENV.fetch('CLOUDDSP_SCRIPT_ROUTING_LOG'), JSON.generate(ARGV))
      RUBY
      File.chmod(0o700, ruby)
      routes = {
        'secrets-init' => ['credential-init.rb'],
        'stages' => ['deploy-local-platform.rb', 'list'],
        'plan' => ['deploy-local-plan.rb'],
        'prepare' => ['deploy-local-prepare.rb'],
        'bootstrap-mailpit' => ['deploy-local-mailpit.rb'],
        'bootstrap-postgresql' => ['deploy-local-postgresql.rb'],
        'bootstrap-rabbitmq' => ['deploy-local-rabbitmq.rb'],
        'bootstrap-minio' => ['deploy-local-minio.rb'],
        'bootstrap-platform' => ['deploy-local-platform.rb'],
        'verify' => ['deploy-local-verify.rb'],
        'reconcile' => ['deploy-local-reconcile.rb']
      }
      environment = { 'PATH' => "#{bin}:#{ENV.fetch('PATH', '')}", 'CLOUDDSP_SCRIPT_ROUTING_LOG' => log }
      routes.each do |mode, (name, *arguments)|
        _output, error, status = Open3.capture3(environment, '/bin/bash', ENTRYPOINT, mode, chdir: temporary)
        assert status.success?, error
        assert_equal [CloudDSPPaths.script(name).to_s, *arguments], JSON.parse(File.read(log))
      end
      _output, error, status = Open3.capture3(environment, '/bin/bash', ENTRYPOINT, 'secrets-init',
                                            '--input', '/private/owner-input.yaml', chdir: temporary)
      assert status.success?, error
      assert_equal [CloudDSPPaths.script('credential-init.rb').to_s, '--input', '/private/owner-input.yaml'],
                   JSON.parse(File.read(log))
    end
  end
end
