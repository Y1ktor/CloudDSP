#!/usr/bin/env ruby
# Read-only root verification for the already-adopted local k3d deployment.
#
# The preflight checks context, ownership, lock files, and Secret identities.
# PostgreSQL and RabbitMQ Secret gates compare live values with ignored local
# sources in memory, without printing them or changing either resource.
# Release verifiers then compare their own rendered/source/live resources;
# external-state runners check the PostgreSQL, RabbitMQ, MinIO, and Keycloak
# work implemented so far. Keep those narrow verifiers runnable for diagnosis.
# This command never invokes an adopt, smoke, or bootstrap mode. Its companion
# root reconcile permits only the five external-state runners listed below.

require 'open3'
require 'pathname'
require 'rbconfig'

class CloudDSPLocalVerify
  SCRIPT_DIRECTORY = Pathname.new(__dir__).freeze
  CONTEXT = 'k3d-clouddsp-local'.freeze
  RECONCILABLE = %w[
    job-api-postgresql-stage.rb
    rabbitmq-processing-topology.rb
    rabbitmq-source-intake-bootstrap.rb
    minio-buckets-stage.rb
    minio-notification-stage.rb
  ].freeze
  Stage = Struct.new(:name, :command, keyword_init: true)

  # Dependency order is explicit: a failed prerequisite prevents later checks
  # from reporting secondary symptoms as if they were independent failures.
  # The external-state list is intentionally limited to versioned runners that
  # already have a read-only verify mode. The MinIO bucket stage restores only
  # an absent shared-sample policy, and the notification stage restores only
  # an absent upload rule. Bucket creation, MinIO IAM, and Keycloak state need
  # separate reviewed write paths before full reconciliation.
  STAGES = [
    Stage.new(name: 'preflight', command: ['ruby', 'deploy-local-plan.rb']),
    Stage.new(name: 'PostgreSQL credential Secret', command: ['ruby', 'postgresql-secret-stage.rb', 'verify']),
    Stage.new(name: 'PostgreSQL release', command: ['ruby', 'postgresql-release.rb', 'verify']),
    Stage.new(name: 'RabbitMQ credential Secret', command: ['ruby', 'rabbitmq-secret-stage.rb', 'verify']),
    Stage.new(name: 'RabbitMQ release', command: ['ruby', 'rabbitmq-release.rb', 'verify']),
    Stage.new(name: 'MinIO release', command: ['ruby', 'minio-release.rb', 'verify']),
    Stage.new(name: 'Job API PostgreSQL bootstrap and migrations', command: ['ruby', 'job-api-postgresql-stage.rb', 'verify']),
    Stage.new(name: 'RabbitMQ processing topology', command: ['ruby', 'rabbitmq-processing-topology.rb', 'verify']),
    Stage.new(name: 'RabbitMQ source-intake topology and users', command: ['ruby', 'rabbitmq-source-intake-bootstrap.rb', 'verify']),
    Stage.new(name: 'MinIO bucket boundaries and shared samples', command: ['ruby', 'minio-buckets-stage.rb', 'verify']),
    Stage.new(name: 'MinIO buckets/IAM verification and upload notification', command: ['ruby', 'minio-notification-stage.rb', 'verify']),
    Stage.new(name: 'Mailpit release', command: ['ruby', 'mailpit-release.rb', 'verify']),
    Stage.new(name: 'Keycloak release', command: ['ruby', 'keycloak-release.rb', 'verify']),
    Stage.new(name: 'Keycloak realm and clients', command: ['ruby', 'keycloak-config-verify.rb', 'verify']),
    Stage.new(name: 'Job API release', command: ['ruby', 'job-api-release.rb', 'verify']),
    Stage.new(name: 'upload-intake release', command: ['ruby', 'upload-intake-release.rb', 'verify']),
    Stage.new(name: 'legacy dispatcher release', command: ['ruby', 'dispatcher-release.rb', 'verify']),
    Stage.new(name: 'generic dispatcher release', command: ['ruby', 'generic-dispatcher-release.rb', 'verify']),
    Stage.new(name: 'KEDA operator', command: ['kubectl', '--context', CONTEXT, '-n', 'keda', 'rollout', 'status', 'deployment/keda-operator', '--timeout=30s']),
    Stage.new(name: 'KEDA metrics API', command: ['kubectl', '--context', CONTEXT, '-n', 'keda', 'rollout', 'status', 'deployment/keda-operator-metrics-apiserver', '--timeout=30s']),
    Stage.new(name: 'KEDA admission webhooks', command: ['kubectl', '--context', CONTEXT, '-n', 'keda', 'rollout', 'status', 'deployment/keda-admission-webhooks', '--timeout=30s']),
    Stage.new(name: 'KEDA ScaledObject CRD', command: ['kubectl', '--context', CONTEXT, 'get', 'crd', 'scaledobjects.keda.sh']),
    Stage.new(name: 'KEDA TriggerAuthentication CRD', command: ['kubectl', '--context', CONTEXT, 'get', 'crd', 'triggerauthentications.keda.sh']),
    Stage.new(name: 'scaling authentication release', command: ['ruby', 'scaling-auth-release.rb', 'verify']),
    Stage.new(name: 'Basic Pitch release', command: ['ruby', 'basic-pitch-release.rb', 'verify']),
    Stage.new(name: 'ADTOF release', command: ['ruby', 'adtof-release.rb', 'verify']),
    Stage.new(name: 'Demucs release', command: ['ruby', 'demucs-release.rb', 'verify']),
    Stage.new(name: 'frontend release', command: ['ruby', 'frontend-release.rb', 'verify'])
  ].freeze

  def initialize(runner: Open3.method(:capture3), output: $stdout, error: $stderr, mode: 'verify')
    raise ArgumentError, 'mode must be verify or reconcile' unless %w[verify reconcile].include?(mode)

    @runner = runner
    @output = output
    @error = error
    @mode = mode
  end

  def run
    scope = @mode == 'verify' ? 'verification (read-only)' : 'reconcile (existing cluster; versioned bootstrap only)'
    @output.puts "CloudDSP local #{scope}; context #{CONTEXT}"
    STAGES.each_with_index do |stage, index|
      @current_stage = stage.name
      command = stage_command(stage.command)
      argv = resolve(command)
      _stdout, _stderr, status = @runner.call(*argv)
      unless status.success?
        @error.puts "[FAIL] #{index + 1}/#{STAGES.length} #{stage.name}: #{File.basename(argv.first)} exited #{status.exitstatus || 'without a status'}."
        @error.puts "Run #{diagnostic_command(command)} separately for details; later stages were not run."
        return 1
      end
      # Child output can include data from an external client on failure, so
      # the root reports only its fixed stage names. The component command is
      # still available for a focused, human-reviewed diagnostic run.
      action = command.last == 'reconcile' ? 'RECONCILED' : 'PASS'
      @output.puts "[#{action}] #{index + 1}/#{STAGES.length} #{stage.name}"
    end
    @output.puts "Result: all configured #{@mode} gates passed."
    @output.puts 'Scope: Helm releases, MinIO IAM, and Keycloak state verified; bucket creation, their write paths, and remaining bootstrap runners await review.' if @mode == 'reconcile'
    0
  rescue Errno::ENOENT
    @error.puts "[FAIL] Required local command is unavailable during #{@current_stage}."
    1
  end

  private

  def stage_command(command)
    return command unless @mode == 'reconcile' && command.first == 'ruby' && RECONCILABLE.include?(command[1])

    # Only the five reviewed external-state runners get a write-capable
    # argument. Release scripts stay in verify mode, so no adoption, Helm
    # revision, PVC replacement, or unreviewed chart upgrade is implicit.
    [*command.take(2), 'reconcile']
  end

  def resolve(command)
    return [RbConfig.ruby, SCRIPT_DIRECTORY.join(command.fetch(1)).to_s, *command.drop(2)] if command.first == 'ruby'

    command
  end

  def diagnostic_command(command)
    return "ruby ./k8Deployment/kubernetes/scripts/#{command.fetch(1)} #{command.drop(2).join(' ')}" if command.first == 'ruby'

    command.join(' ')
  end
end

exit CloudDSPLocalVerify.new.run if $PROGRAM_NAME == __FILE__
