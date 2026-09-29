#!/usr/bin/env ruby
# Install KEDA only on a fresh CloudDSP cluster, then verify the chart and its
# controller API. The existing install-keda.sh owns the actual Helm write; this
# wrapper makes its upgrade-capable command safe for one-command bootstrap by
# refusing a pre-existing namespace, release, CRD, or metrics API registration.
# A partial chart install remains visible for diagnosis instead of being
# adopted or silently upgraded on a second bootstrap attempt.
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class KedaReleaseStage
  ROOT = Pathname.new(File.expand_path('..', __dir__)).freeze
  CONTEXT = 'k3d-clouddsp-local'.freeze
  RELEASE = 'keda'.freeze
  NAMESPACE = 'keda'.freeze
  VERSION = '2.20.2'.freeze
  METRICS_API = 'v1beta1.external.metrics.k8s.io'.freeze
  CONTROLLERS = %w[keda-operator keda-operator-metrics-apiserver keda-admission-webhooks].freeze
  CRDS = %w[
    cloudeventsources.eventing.keda.sh
    clustercloudeventsources.eventing.keda.sh
    clustertriggerauthentications.keda.sh
    scaledjobs.keda.sh
    scaledobjects.keda.sh
    triggerauthentications.keda.sh
  ].freeze

  def initialize(command: Open3.method(:capture3), output: $stdout, error: $stderr)
    @command = command
    @output = output
    @error = error
  end

  def run(mode)
    ensure_true(%w[plan install verify].include?(mode), 'use plan, install, or verify')
    values = validate_source

    if mode == 'verify'
      verify_release(values)
      @output.puts 'KEDA release verify: pinned Helm chart, values, controllers, and CRDs ready'
      return 0
    end

    check_fresh_boundary
    if mode == 'plan'
      @output.puts "KEDA release plan: pinned #{VERSION} install pending on an empty KEDA boundary"
      return 0
    end

    # Recheck immediately before the Helm action. The shell installer downloads
    # the pinned upstream chart and uses --atomic; this Ruby runner never
    # upgrades an existing release or claims a partial install as success.
    check_fresh_boundary
    command!('pinned KEDA Helm install', 'bash', ROOT.join('scripts', 'install-keda.sh').to_s)
    verify_release(values)
    @output.puts 'KEDA release install: pinned Helm chart, values, controllers, and CRDs ready'
    0
  rescue StandardError => exception
    # Helm and kubectl output may include cluster details. Only our fixed
    # contract label is returned through the root orchestration command.
    @error.puts "KEDA release #{mode} stopped: #{exception.message}"
    1
  end

  private

  def ensure_true(condition, label)
    raise label unless condition
  end

  def command!(label, *arguments)
    output, _stderr, status = @command.call(*arguments)
    ensure_true(status.success?, "#{label} failed")
    output
  end

  def validate_source
    lock = YAML.load_file(ROOT.join('helm', 'keda', 'release.lock.yaml').to_s)
    chart = lock.fetch('chart')
    expected = { 'repositoryName' => 'kedacore', 'repositoryURL' => 'https://kedacore.github.io/charts',
                 'name' => RELEASE, 'version' => VERSION, 'releaseName' => RELEASE,
                 'namespace' => NAMESPACE }
    expected.each { |key, value| ensure_true(chart[key] == value, "KEDA release lock #{key} changed") }
    ensure_true(lock['imageLockKeys'] == %w[images.keda-operator images.keda-metrics-api-server images.keda-admission-webhooks],
                'KEDA image lock keys changed')

    values = YAML.load_file(ROOT.join('helm', 'keda', 'values.yaml').to_s)
    ensure_true(values['clusterName'] == 'clouddsp-local' && values['watchNamespace'] == 'clouddsp-app',
                'KEDA cluster or watch scope changed')
    ensure_true(values.dig('crds', 'install') == true, 'KEDA CRD install setting changed')
    { 'keda' => 'kedacore/keda', 'metricsApiServer' => 'kedacore/keda-metrics-apiserver',
      'webhooks' => 'kedacore/keda-admission-webhooks' }.each do |component, repository|
      image = values.dig('image', component)
      ensure_true(image == { 'registry' => 'ghcr.io', 'repository' => repository, 'tag' => VERSION },
                  "KEDA #{component} image changed")
    end
    ensure_true(values.dig('image', 'pullPolicy') == 'IfNotPresent', 'KEDA image pull policy changed')
    %w[operator metricsServer webhooks].each do |controller|
      ensure_true(values.dig(controller, 'replicaCount') == 1,
                  "KEDA #{controller} replica count changed")
    end
    values
  end

  def releases
    output = command!('KEDA Helm release lookup', 'helm', '--kube-context', CONTEXT,
                      'list', '--all-namespaces', '--filter', '^keda$', '--output', 'json')
    JSON.parse(output).select { |release| release['name'] == RELEASE }
  end

  def namespace_name
    command!('KEDA namespace lookup', 'kubectl', '--context', CONTEXT,
             'get', 'namespace', NAMESPACE, '--ignore-not-found', '-o', 'name').strip
  end

  def crd_names
    output = command!('KEDA CRD lookup', 'kubectl', '--context', CONTEXT, 'get', 'crds', '-o', 'json')
    JSON.parse(output).fetch('items').map { |item| item.dig('metadata', 'name') }
                      .select { |name| name.end_with?('.keda.sh') }.sort
  end

  def metrics_api_name
    command!('KEDA metrics API lookup', 'kubectl', '--context', CONTEXT,
             'get', 'apiservice', METRICS_API, '--ignore-not-found', '-o', 'name').strip
  end

  def check_fresh_boundary
    ensure_true(releases.empty?, 'KEDA Helm release already exists')
    ensure_true(namespace_name.empty?, 'KEDA namespace already exists')
    ensure_true(crd_names.empty?, 'KEDA CRDs already exist')
    ensure_true(metrics_api_name.empty?, 'KEDA external metrics API already exists')
  end

  def verify_release(values)
    matches = releases
    ensure_true(matches.length == 1, 'KEDA Helm release is absent or duplicated')
    release = matches.first
    ensure_true(release['namespace'] == NAMESPACE && release['chart'] == "keda-#{VERSION}" &&
                release['status'] == 'deployed', 'KEDA Helm release differs from lock')
    ensure_true(namespace_name == "namespace/#{NAMESPACE}", 'KEDA namespace is absent')
    actual_values = YAML.load(command!('KEDA Helm values lookup', 'helm', '--kube-context', CONTEXT,
                                       '-n', NAMESPACE, 'get', 'values', RELEASE, '--output', 'yaml'))
    ensure_true(actual_values == values, 'KEDA Helm values differ from committed profile')
    CONTROLLERS.each do |controller|
      command!("KEDA #{controller} rollout", 'kubectl', '--context', CONTEXT, '-n', NAMESPACE,
               'rollout', 'status', "deployment/#{controller}", '--timeout=60s')
    end
    ensure_true(crd_names == CRDS, 'KEDA CRD set differs from pinned chart')
    ensure_true(!metrics_api_name.empty?, 'KEDA external metrics API is absent')
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: keda-release-stage.rb plan|install|verify' unless ARGV.length == 1
  exit KedaReleaseStage.new.run(ARGV.first)
end
