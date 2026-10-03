require 'minitest/autorun'
require 'fileutils'
require 'open3'
require 'tmpdir'

class ReconcileDemucsScalingTest < Minitest::Test
  SCRIPT = File.expand_path('../../scripts/reconcile-demucs-scaling.sh', __dir__)
  KUBERNETES = File.expand_path('../..', __dir__)
  UPGRADE_OPTIONS = %w[--kube-context k3d-clouddsp-local --namespace clouddsp-app --wait --timeout 3m].freeze

  def setup
    @temporary_directory = Dir.mktmpdir('clouddsp-demucs-reconcile')
    @bin = File.join(@temporary_directory, 'bin')
    @log = File.join(@temporary_directory, 'commands.log')
    @counter = File.join(@temporary_directory, 'command-count')
    FileUtils.mkdir_p(@bin)

    # Fake every deployment executable this wrapper might invoke. These
    # integration checks exercise the real shell's ordering and fail-fast
    # behavior without accessing Kubernetes, Docker, or local credentials.
    fake_command = <<~'BASH'
      #!/bin/bash
      set -euo pipefail
      printf '%s\t' "${0##*/}" "$@" >> "$CLOUDDSP_RECONCILE_TEST_LOG"
      printf '\n' >> "$CLOUDDSP_RECONCILE_TEST_LOG"
      count=0
      if [[ -f "$CLOUDDSP_RECONCILE_TEST_COUNTER" ]]; then
        read -r count < "$CLOUDDSP_RECONCILE_TEST_COUNTER"
      fi
      count=$((count + 1))
      printf '%s\n' "$count" > "$CLOUDDSP_RECONCILE_TEST_COUNTER"
      if [[ "$count" == "$CLOUDDSP_RECONCILE_TEST_FAIL_AT" ]]; then
        printf 'Simulated deployment command failure.\n' >&2
        exit 42
      fi
    BASH
    %w[ruby helm kubectl docker k3d].each do |name|
      path = File.join(@bin, name)
      File.write(path, fake_command)
      File.chmod(0o700, path)
    end
  end

  def teardown
    FileUtils.remove_entry(@temporary_directory)
  end

  def run_wrapper(fail_at: 0, arguments: [])
    File.write(@log, '')
    File.write(@counter, "0\n")
    environment = {
      'PATH' => "#{@bin}:#{ENV.fetch('PATH', '')}",
      'CLOUDDSP_RECONCILE_TEST_LOG' => @log,
      'CLOUDDSP_RECONCILE_TEST_COUNTER' => @counter,
      'CLOUDDSP_RECONCILE_TEST_FAIL_AT' => fail_at.to_s
    }
    output, error, status = Open3.capture3(environment, '/bin/bash', SCRIPT, *arguments,
                                          chdir: @temporary_directory)
    commands = File.readlines(@log, chomp: true).map { |line| line.split("\t") }
    # Absolute script paths keep the wrapper independent of the caller's
    # working directory; compare stage names while retaining Helm chart paths.
    commands.each do |command|
      next unless command.first == 'ruby'

      assert_equal File.join(KUBERNETES, 'scripts'), File.dirname(command.fetch(1))
      command[1] = File.basename(command[1])
    end
    [status, commands, output, error]
  end

  def expected_commands
    [
      %w[ruby application-identity-stage.rb database keda-demucs verify],
      %w[ruby application-identity-stage.rb rabbitmq keda-scaler verify],
      %w[ruby scaling-auth-release.rb verify-prerequisites],
      %w[ruby demucs-release.rb verify],
      ['helm', 'upgrade', 'clouddsp-scaling-auth', File.join(KUBERNETES, 'helm', 'scaling-auth'), *UPGRADE_OPTIONS],
      ['helm', 'upgrade', 'clouddsp-demucs', File.join(KUBERNETES, 'helm', 'demucs'), *UPGRADE_OPTIONS],
      %w[ruby scaling-auth-release.rb verify-prerequisites],
      %w[ruby demucs-release.rb verify]
    ]
  end

  def test_existing_releases_are_reconciled_in_order_from_an_unrelated_directory
    status, commands, _output, error = run_wrapper

    assert status.success?, error
    assert_equal expected_commands, commands
    refute commands.any? { |command| command.first == 'kubectl' }
    commands.select { |command| command.first == 'helm' }.each do |command|
      refute_includes command, '--install'
      refute_includes command, '--take-ownership'
      refute_includes command, '--force-conflicts'
    end
  end

  def test_identity_or_release_preflight_failure_prevents_every_upgrade
    (1..4).each do |failure_index|
      status, commands = run_wrapper(fail_at: failure_index)

      refute status.success?, "preflight #{failure_index} must fail"
      assert_equal expected_commands.first(failure_index), commands
      refute commands.any? { |command| command.first == 'helm' }
    end
  end

  def test_failed_scaling_auth_upgrade_prevents_demucs_upgrade
    status, commands = run_wrapper(fail_at: 5)

    refute status.success?
    assert_equal expected_commands.first(5), commands
  end

  def test_failed_demucs_upgrade_prevents_success_verification
    status, commands = run_wrapper(fail_at: 6)

    refute status.success?
    assert_equal expected_commands.first(6), commands
  end

  def test_post_upgrade_verification_failure_is_returned_to_the_operator
    status, commands = run_wrapper(fail_at: 8)

    refute status.success?
    assert_equal expected_commands, commands
  end

  def test_unexpected_arguments_fail_without_deployment_commands
    status, commands = run_wrapper(arguments: ['unexpected'])

    assert_equal 64, status.exitstatus
    assert_empty commands
  end
end
