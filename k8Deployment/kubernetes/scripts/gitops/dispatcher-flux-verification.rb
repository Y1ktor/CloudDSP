# Shared read-only normal/pause verification for the two reviewed publishers.
# Component adapters fix their identities; this module cannot pause arbitrary
# workloads. Full source/Helm/live parity is retained in both replica states.
require_relative 'flux-ownership'

module DispatcherFluxVerification
  include FluxOwnership

  def run(mode)
    @dispatcher_smoke_pause = mode == 'verify-smoke-pause'
    super(@dispatcher_smoke_pause ? 'verify' : mode)
  ensure
    @dispatcher_smoke_pause = false
    @expected_replicas = 1
  end

  private

  def usage
    "#{super}|verify-smoke-pause"
  end

  def check_tools
    ensure_true(%w[dispatcher generic-dispatcher].include?(flux_ownership_component),
                'publisher smoke verification requires a reviewed dispatcher binding')
    super
    ensure_true(@flux_ownership_helmrelease, 'verify-smoke-pause requires the active Flux HelmRelease') if @dispatcher_smoke_pause
  end

  def validate_flux_ownership_record
    super
    spec = @flux_ownership_helmrelease.fetch('spec')
    normal = "./k8Deployment/kubernetes/helm/#{flux_ownership_component}/values.yaml"
    pause = "./k8Deployment/kubernetes/helm/#{flux_ownership_component}/values.smoke-pause.yaml"
    files = @dispatcher_smoke_pause ? [normal, pause] : [normal]
    # Inline values or external valuesFrom could override the reviewed pause or
    # image. This component deliberately supports only these repository files.
    ensure_true(spec.dig('chart', 'spec', 'valuesFiles') == files &&
                spec.fetch('values', {}).empty? && spec.fetch('valuesFrom', []).empty?,
                "#{flux_ownership_binding.fetch(:label)} Flux values must match the requested normal or smoke-pause configuration")
  end

  def check_chart_and_source
    # First prove the ordinary chart still matches the retained source exactly;
    # Flux's common adapter then checks the chart version and metadata labels.
    super
    return unless @dispatcher_smoke_pause

    pause = self.class::ROOT.join('helm', flux_ownership_component, 'values.smoke-pause.yaml')
    ensure_true(YAML.load_file(pause) == { 'replicas' => 0 }, 'dispatcher smoke values may change only replicas to zero')
    yaml = command('helm', 'template', @release, @chart.to_s, '--namespace', @namespace,
                   '--values', pause.to_s)
    paused = indexed(documents(yaml))
    paused.each_value { |object| object.fetch('metadata').fetch('labels').merge!(flux_ownership_binding.fetch(:origin_labels)) }
    expected = Marshal.load(Marshal.dump(@rendered))
    expected.each_value { |object| object.fetch('spec')['replicas'] = 0 }
    ensure_true(paused == expected, 'paused dispatcher chart may change only the Deployment replica count')
    command('kubectl', '--context', self.class::CONTEXT, '--namespace', @namespace,
            'apply', '--dry-run=server', '--filename', '-', stdin_data: yaml)
    @source.each_value { |object| object.fetch('spec')['replicas'] = 0 }
    @rendered = paused
    @expected_replicas = 0
  end

  def wait_for_idle_pods
    return super unless @dispatcher_smoke_pause

    # A terminating publisher can still commit or publish a final event. The
    # source-to-outbox smoke requires ALL matching Pods to disappear, whereas
    # ordinary KEDA worker draining permits terminating Pods to finish leases.
    deadline = monotonic_time + self.class::IDLE_POD_DRAIN_TIMEOUT_SECONDS
    loop do
      pods = JSON.parse(kubectl('get', 'pods', '--selector', @pod_selector, '--output', 'json')).fetch('items')
      return if pods.empty?

      ensure_true(monotonic_time < deadline, "paused #{flux_ownership_component} still has Pods, including terminating publishers")
      sleep(self.class::IDLE_POD_RECHECK_INTERVAL_SECONDS)
    end
  end

  def runtime_detail
    @dispatcher_smoke_pause ? 'no publisher Pods (including terminating Pods)' : super
  end
end
