#!/usr/bin/env ruby
# Transfer only CloudDSP's two live KEDA TriggerAuthentications to one Helm
# release. The chart contains Secret names and keys, never Secret values;
# KEDA itself, three worker ScaledObjects, their HPAs and Deployments retain
# their existing owners and identities. `plan` and `verify` are read-only.
require_relative '../lib/paths'
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class ScalingAuthRelease
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  REPOSITORY_ROOT = CloudDSPPaths::REPOSITORY_ROOT
  CONTEXT = 'k3d-clouddsp-local'
  NAMESPACE = 'clouddsp-app'
  RELEASE = 'clouddsp-scaling-auth'
  CHART = ROOT.join('helm', 'scaling-auth')
  SOURCE_FILES = %w[
    keda-rabbitmq-scaler-trigger-authentication.yaml
    keda-demucs-postgresql-trigger-authentication.yaml
  ].freeze
  SCALERS = {
    'clouddsp-adtof-rabbitmq-scaler' => {
      'worker' => 'clouddsp-adtof',
      'authentication' => %w[clouddsp-rabbitmq-scaler-authentication]
    },
    'clouddsp-basic-pitch-rabbitmq-scaler' => {
      'worker' => 'clouddsp-basic-pitch',
      'authentication' => %w[clouddsp-rabbitmq-scaler-authentication clouddsp-demucs-postgresql-scaler-authentication]
    },
    'clouddsp-demucs-rabbitmq-scaler' => {
      'worker' => 'clouddsp-demucs',
      'authentication' => %w[clouddsp-rabbitmq-scaler-authentication clouddsp-demucs-postgresql-scaler-authentication]
    }
  }.freeze

  def initialize(command: Open3.method(:capture3))
    @command = command
  end

  def run(mode)
    abort 'Usage: ./k8Deployment/kubernetes/scripts/releases/scaling-auth-release.rb plan|install|adopt|verify|verify-prerequisites' unless %w[plan install adopt verify verify-prerequisites].include?(mode)
    check_tools_and_keda
    check_chart_and_source

    case mode
    when 'plan'
      check_cluster('kubectl')
      puts 'scaling-auth chart matches both source objects and live specs; three dependent scalers are Ready.'
    when 'install'
      check_fresh_install_boundary
      puts 'Installing the fresh scaling-auth Helm release...'
      output = command('helm', 'install', RELEASE, CHART.to_s,
                       '--kube-context', CONTEXT, '--namespace', NAMESPACE,
                       '--wait', '--timeout', '3m')
      puts output.lines.grep(/^(NAME|NAMESPACE|STATUS|REVISION):/)
      check_cluster('Helm', require_scalers: false)
      puts 'scaling-auth fresh install: both TriggerAuthentications, Secret references, and KEDA controller are ready.'
    when 'adopt'
      before = check_cluster('kubectl')
      operation = before.fetch(:release) ? 'upgrade' : 'install'
      puts 'Adopting the two existing KEDA TriggerAuthentications into Helm...'
      # Explicit takeover follows exact source/render/live parity. No atomic
      # rollback or uninstall: either could delete a newly adopted CRD object.
      output = command('helm', operation, RELEASE, CHART.to_s,
                       '--kube-context', CONTEXT, '--namespace', NAMESPACE,
                       '--take-ownership', '--force-conflicts', '--wait', '--timeout', '3m')
      puts output.lines.grep(/^(NAME|NAMESPACE|STATUS|REVISION):/)
      after = check_cluster('Helm')
      verify_stable_identity(before, after)
      puts 'scaling-auth adopted: both resource UIDs and generations, dependent scaler/HPA/Deployment UIDs, and scaler readiness preserved.'
    when 'verify'
      check_cluster('Helm')
      puts 'scaling-auth Helm manifest, Secret references, both TriggerAuthentications, three Ready scalers, HPAs, and worker targets verified.'
    when 'verify-prerequisites'
      check_cluster('Helm', require_scalers: false)
      puts 'scaling-auth release, both TriggerAuthentications, Secret references, and KEDA controller verified for worker installation.'
    end
  rescue StandardError => error
    warn "scaling-auth #{mode} stopped: #{error.message}"
    warn 'If install started, inspect the Helm release and both TriggerAuthentications before any retry; do not uninstall an adopted release.' if mode == 'adopt'
    exit 1
  end

  private

  def ensure_true(condition, message)
    raise message unless condition
  end

  def command(*args, stdin_data: nil)
    output, error, status = @command.call(*args, stdin_data: stdin_data, chdir: REPOSITORY_ROOT)
    raise "#{args.first} failed: #{error.strip.empty? ? output.strip : error.strip}" unless status.success?
    output
  end

  def kubectl(*args)
    command('kubectl', '--context', CONTEXT, '--namespace', NAMESPACE, *args)
  end

  def helm(*args)
    command('helm', '--kube-context', CONTEXT, *args)
  end

  def yaml_documents(text)
    YAML.load_stream(text).compact
  end

  def indexed(objects)
    result = objects.to_h { |object| [object.fetch('metadata').fetch('name'), object] }
    ensure_true(result.length == objects.length, 'duplicate resource name')
    result
  end

  def check_tools_and_keda
    %w[helm kubectl].each do |tool|
      ensure_true(ENV.fetch('PATH', '').split(File::PATH_SEPARATOR).any? { |dir| File.executable?(File.join(dir, tool)) },
                  "#{tool} is required")
    end
    contexts = command('kubectl', 'config', 'get-contexts', '--output=name').lines.map(&:strip)
    ensure_true(contexts.include?(CONTEXT), "Kubernetes context #{CONTEXT} is unavailable")
    lock = YAML.load_file(ROOT.join('helm', 'keda', 'release.lock.yaml')).fetch('chart')
    releases = JSON.parse(helm('list', '--namespace', lock.fetch('namespace'), '--output', 'json'))
    installed = releases.find { |item| item['name'] == lock.fetch('releaseName') }
    ensure_true(installed && installed['status'] == 'deployed' && installed['chart'] == "#{lock.fetch('name')}-#{lock.fetch('version')}",
                'pinned KEDA controller release is not deployed')
    @keda_version = lock.fetch('version')
  end

  def check_chart_and_source
    source = indexed(SOURCE_FILES.flat_map do |name|
      yaml_documents(ROOT.join('helm', 'keda', name).read)
    end)
    ensure_true(source.length == 2, 'expected exactly two versioned KEDA authentication objects')
    ensure_true(source.values.all? { |object| object['kind'] == 'TriggerAuthentication' && object.dig('metadata', 'namespace') == NAMESPACE },
                'source KEDA resources differ in kind or namespace')
    @source = source
    chart_metadata = YAML.load_file(CHART.join('Chart.yaml'))
    ensure_true(chart_metadata.fetch('name') == 'scaling-auth' && chart_metadata.fetch('appVersion') == @keda_version,
                'scaling-auth chart differs from the pinned KEDA version')
    @chart_identity = "scaling-auth-#{chart_metadata.fetch('version')}"
    command('helm', 'lint', CHART.to_s, '--strict')
    rendered_yaml = command('helm', 'template', RELEASE, CHART.to_s, '--namespace', NAMESPACE)
    rendered = indexed(yaml_documents(rendered_yaml))
    ensure_true(rendered.keys.sort == source.keys.sort, 'chart must render exactly the two source resource names')
    source.each do |name, object|
      expected = Marshal.load(Marshal.dump(object))
      expected.fetch('metadata').fetch('labels')['app.kubernetes.io/managed-by'] = 'Helm'
      ensure_true(rendered.fetch(name) == expected, "rendered TriggerAuthentication/#{name} differs from source")
    end
    # Server dry run validates these KEDA CRDs without persisting ownership or
    # causing an operator reconciliation before the guarded Helm takeover.
    command('kubectl', '--context', CONTEXT, '--namespace', NAMESPACE,
            'apply', '--dry-run=server', '--filename', '-', stdin_data: rendered_yaml)
    @rendered = rendered
  end

  def release_record
    JSON.parse(helm('list', '--namespace', NAMESPACE, '--output', 'json')).find { |item| item['name'] == RELEASE }
  end

  def collect(kind, names)
    response = JSON.parse(kubectl('get', *names.map { |name| "#{kind}/#{name}" }, '--output', 'json'))
    objects = response['kind'] == 'List' ? response.fetch('items') : [response]
    result = indexed(objects)
    ensure_true(result.keys.sort == names.sort, "unexpected #{kind} identity set")
    result
  end

  def check_fresh_install_boundary
    ensure_true(release_record.nil?, 'scaling-auth Helm release already exists; use verify or inspect it')
    @source.each_key do |name|
      ensure_true(kubectl('get', "triggerauthentication/#{name}", '--ignore-not-found', '--output=name').strip.empty?,
                  "TriggerAuthentication/#{name} already exists; inspect it before install")
    end
    ensure_true(SCALERS.keys.all? { |name|
      kubectl('get', "scaledobject/#{name}", '--ignore-not-found', '--output=name').strip.empty?
    }, 'one or more worker ScaledObjects already exist; inspect their authentication before install')
    command('ruby', './k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb',
            'rabbitmq', 'keda-scaler', 'verify')
    command('ruby', './k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb',
            'database', 'keda-demucs', 'verify')
  end

  def check_cluster(owner, require_scalers: true)
    release = release_record
    if owner == 'Helm'
      ensure_true(release && release['status'] == 'deployed' && release['chart'] == @chart_identity,
                  'expected scaling-auth Helm release is not deployed')
      installed = indexed(yaml_documents(helm('get', 'manifest', RELEASE, '--namespace', NAMESPACE)))
      ensure_true(installed == @rendered, 'installed release manifest differs from the reviewed chart')
    else
      retryable = release && release['status'] == 'failed' && release['chart'] == @chart_identity
      ensure_true(release.nil? || retryable, 'scaling-auth release already exists in a non-retryable state')
    end

    authentications = collect('triggerauthentication', @source.keys)
    authentications.each do |name, object|
      expected = @source.fetch(name)
      metadata = object.fetch('metadata')
      expected_labels = expected.fetch('metadata').fetch('labels').merge('app.kubernetes.io/managed-by' => owner)
      ensure_true(metadata.fetch('labels') == expected_labels, "TriggerAuthentication/#{name} has unexpected ownership labels")
      ensure_true(metadata.fetch('ownerReferences', []).empty?, "TriggerAuthentication/#{name} has an unexpected controller owner")
      annotations = metadata.fetch('annotations', {})
      if owner == 'Helm'
        ensure_true(annotations['meta.helm.sh/release-name'] == RELEASE && annotations['meta.helm.sh/release-namespace'] == NAMESPACE,
                    "TriggerAuthentication/#{name} has unexpected Helm ownership annotations")
      else
        ensure_true(!annotations.key?('meta.helm.sh/release-name') && !annotations.key?('meta.helm.sh/release-namespace'),
                    "TriggerAuthentication/#{name} is already claimed by Helm")
      end
      ensure_true(object.fetch('spec') == expected.fetch('spec'), "TriggerAuthentication/#{name} Secret references differ from source")
      ensure_true(metadata.fetch('finalizers', []).include?('finalizer.keda.sh'),
                  "TriggerAuthentication/#{name} lost its KEDA finalizer")
    end

    # Checking only Secret identities avoids reading or leaking values into
    # chart values, logs, Helm history, or this read-only verifier.
    secret_names = @source.values.flat_map do |object|
      object.fetch('spec').fetch('secretTargetRef').map { |entry| entry.fetch('name') }
    end.uniq
    kubectl('get', *secret_names.map { |name| "secret/#{name}" }, '--output=name')

    return { authentications: authentications, release: release } unless require_scalers

    scalers = collect('scaledobject', SCALERS.keys)
    hpas = collect('hpa', SCALERS.keys.map { |name| "keda-hpa-#{name}" })
    workers = collect('deployment', SCALERS.values.map { |config| config.fetch('worker') })
    SCALERS.each do |name, config|
      scaler = scalers.fetch(name)
      target = config.fetch('worker')
      refs = scaler.fetch('spec').fetch('triggers').map { |trigger| trigger.dig('authenticationRef', 'name') }.compact.uniq.sort
      ensure_true(refs == config.fetch('authentication').sort && scaler.dig('spec', 'scaleTargetRef', 'name') == target,
                  "ScaledObject/#{name} authentication or worker target changed")
      ready = scaler.fetch('status', {}).fetch('conditions', []).any? do |condition|
        condition['type'] == 'Ready' && condition['status'] == 'True'
      end
      ensure_true(ready && scaler.dig('metadata', 'annotations', 'autoscaling.keda.sh/paused') != 'true',
                  "ScaledObject/#{name} is not Ready or is paused")
      hpa = hpas.fetch("keda-hpa-#{name}")
      parent = hpa.fetch('metadata').fetch('ownerReferences', []).find { |item| item['kind'] == 'ScaledObject' }
      ensure_true(parent && parent['uid'] == scaler.dig('metadata', 'uid') && hpa.dig('spec', 'scaleTargetRef', 'name') == target,
                  "generated HPA for #{name} lost its scaler or worker target")
      ensure_true(workers.fetch(target).dig('metadata', 'uid'), "worker Deployment/#{target} is absent")
    end
    expected_usage = {
      'clouddsp-rabbitmq-scaler-authentication' => SCALERS.keys,
      'clouddsp-demucs-postgresql-scaler-authentication' => %w[clouddsp-demucs-rabbitmq-scaler clouddsp-basic-pitch-rabbitmq-scaler]
    }
    expected_usage.each do |name, users|
      actual = authentications.fetch(name).dig('status', 'scaledobjects').to_s.split(',')
      ensure_true((users - actual).empty?, "TriggerAuthentication/#{name} no longer reports its dependent scalers")
    end
    { authentications: authentications, scalers: scalers, hpas: hpas, workers: workers, release: release }
  end

  def verify_stable_identity(before, after)
    %i[authentications scalers hpas workers].each do |kind|
      before.fetch(kind).each do |name, object|
        current = after.fetch(kind).fetch(name)
        ensure_true(object.dig('metadata', 'uid') == current.dig('metadata', 'uid'), "#{kind} #{name} was replaced")
        next unless kind == :authentications
        ensure_true(object.dig('metadata', 'generation') == current.dig('metadata', 'generation'),
                    "TriggerAuthentication/#{name} spec generation changed")
        ensure_true(object.dig('metadata', 'finalizers') == current.dig('metadata', 'finalizers'),
                    "TriggerAuthentication/#{name} KEDA finalizers changed")
      end
    end
  end
end

ScalingAuthRelease.new.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
