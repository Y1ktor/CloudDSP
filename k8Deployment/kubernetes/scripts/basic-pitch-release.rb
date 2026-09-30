#!/usr/bin/env ruby
# Adopt the Basic Pitch Deployment and its queue scaler as one release. KEDA,
# shared TriggerAuthentication, runtime Secrets, and the generated HPA retain
# their existing owners. A zero-Pod worker is healthy when the queue is idle.
require_relative 'stateless-release'

class BasicPitchRelease < StatelessRelease
  SCALER = 'clouddsp-basic-pitch-rabbitmq-scaler'
  HPA = "keda-hpa-#{SCALER}"

  def run(mode)
    return upgrade_scaling if mode == 'upgrade-scaling'
    return upgrade_stabilization if mode == 'upgrade-stabilization'
    return upgrade_numba if mode == 'upgrade-numba'

    super
  end

  def initialize
    super(
      component: 'basic-pitch',
      namespace: 'clouddsp-app',
      release: 'clouddsp-basic-pitch',
      source_files: %w[basic-pitch-deployment.yaml basic-pitch-scaledobject.yaml],
      resources: ["deployment/clouddsp-basic-pitch", "scaledobject/#{SCALER}"],
      pod_selector: 'app.kubernetes.io/name=basic-pitch,app.kubernetes.io/instance=clouddsp-basic-pitch,app.kubernetes.io/component=midi-extraction-worker',
      expected_replicas: 0,
      allow_fresh_install: true,
      before_install: [
        %w[ruby ./k8Deployment/kubernetes/scripts/worker-database-stage.rb basic-pitch verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/application-identity-stage.rb rabbitmq basic-pitch verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/basic-pitch-minio-secret-stage.rb verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/minio-basic-pitch-iam-stage.rb verify],
        %w[ruby ./k8Deployment/kubernetes/scripts/scaling-auth-release.rb verify-prerequisites]
      ],
      smoke_job: { name: 'basic-pitch-worker-smoke', manifest: 'tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-job.yaml' },
      smoke_timeout_seconds: 360
    )
  end

  # Upgrade an already Helm-owned queue-only scaler to the reviewed
  # queue-plus-task policy. The current release manifest is the immutable
  # baseline: only the second trigger, scale-down window, portable Numba
  # setting, and descriptive purpose label may change. No new takeover occurs.
  def upgrade_scaling
    check_tools
    check_chart_and_source
    record = release_record
    baseline_release = record && record['status'] == 'deployed' && record['chart'] == 'basic-pitch-0.1.0'
    retryable_conflict = record && record['status'] == 'failed' && record['chart'] == 'basic-pitch-0.1.3' &&
                         record['revision'].to_s == '2'
    ensure_true(baseline_release || retryable_conflict,
                'expected original Basic Pitch release or its inspected first conflict failure')

    # The first v0.1.1 attempt may have stored a failed revision without
    # changing the live ScaledObject. Compare the original installed manifest
    # explicitly so no failed revision can become the baseline for a retry.
    installed = indexed(documents(helm('get', 'manifest', 'clouddsp-basic-pitch', '--namespace', 'clouddsp-app', '--revision', '1')))
    ensure_true(installed.keys.sort == @rendered.keys.sort, 'installed Basic Pitch resource set changed')
    deployment_id = ['Deployment', 'clouddsp-app', 'clouddsp-basic-pitch']
    scaler_id = ['ScaledObject', 'clouddsp-app', SCALER]
    expected_old_deployment = baseline_without_numba_env(@rendered.fetch(deployment_id))
    ensure_true(installed.fetch(deployment_id) == expected_old_deployment,
                'worker Deployment differs from the reviewed pre-Numba baseline')
    old_scaler = installed.fetch(scaler_id)
    new_scaler = @rendered.fetch(scaler_id)
    expected_old = Marshal.load(Marshal.dump(new_scaler))
    expected_old.fetch('spec').fetch('triggers').pop
    expected_old.dig('spec', 'advanced', 'horizontalPodAutoscalerConfig', 'behavior', 'scaleDown')['stabilizationWindowSeconds'] = 60
    expected_old.fetch('metadata').fetch('labels')['clouddsp.io/purpose'] = 'queue-depth-autoscale-private-midi-worker'
    ensure_true(old_scaler == expected_old, 'installed scaler differs from the reviewed queue-only baseline')

    objects = live_objects
    ensure_true(objects.keys.sort == installed.keys.sort, 'live Basic Pitch identities changed')
    installed.each do |id, expected|
      actual = objects.fetch(id)
      ensure_true(actual.dig('metadata', 'uid'), "#{id.last} lost its resource UID")
      difference = first_difference(expected.fetch('spec'), normalized_live_spec(actual, expected.fetch('spec')))
      ensure_true(difference.nil?, "live #{id.last} differs from installed baseline at #{difference}")
    end
    worker = objects.fetch(deployment_id)
    ensure_true(worker.dig('spec', 'replicas') == 0 && worker.dig('status', 'replicas').to_i == 0,
                'Basic Pitch must be idle before scaling policy upgrade')
    hpa = JSON.parse(kubectl('get', "hpa/#{HPA}", '--output', 'json'))
    before = { worker_uid: worker.dig('metadata', 'uid'), scaler_uid: objects.fetch(scaler_id).dig('metadata', 'uid'),
               hpa_uid: hpa.dig('metadata', 'uid'), scaler_generation: objects.fetch(scaler_id).dig('metadata', 'generation') }
    kubectl('get', 'triggerauthentication/clouddsp-demucs-postgresql-scaler-authentication',
            'secret/clouddsp-keda-demucs-postgresql-credentials', '--output=name')

    # The original kubectl field manager still owns the changed scaler
    # fields. Force only after the complete baseline
    # comparison above proves the chart changes nothing else.
    output = command('helm', 'upgrade', 'clouddsp-basic-pitch', @chart.to_s,
                     '--kube-context', CONTEXT, '--namespace', 'clouddsp-app',
                     '--force-conflicts', '--wait', '--timeout', '3m')
    puts output.lines.grep(/^(NAME|NAMESPACE|STATUS|REVISION):/)
    after = check_cluster('Helm')
    ensure_true(after.fetch(:objects).fetch(deployment_id).dig('metadata', 'uid') == before.fetch(:worker_uid) &&
                after.fetch(:objects).fetch(scaler_id).dig('metadata', 'uid') == before.fetch(:scaler_uid) &&
                after.fetch(:hpa_uid) == before.fetch(:hpa_uid),
                'Basic Pitch worker, scaler, or generated HPA was replaced')
    ensure_true(after.fetch(:scaler_generation) > before.fetch(:scaler_generation),
                'Basic Pitch scaler did not accept the second trigger')
    puts 'Basic Pitch policy upgraded: dual triggers, six-minute scale-in, and portable Numba target Ready; resource identities preserved.'
  rescue StandardError => error
    warn "Basic Pitch scaling upgrade stopped: #{error.message}"
    warn 'Inspect the installed Helm revision and live scaler before any retry; do not uninstall the adopted release.'
    exit 1
  end

  # This path starts at the intermediate dual-trigger chart 0.1.1 and
  # converges to the final policy. It rolls the Pod for NUMBA_CPU_NAME, so
  # require idle replicas before upgrading.
  def upgrade_stabilization
    check_tools
    check_chart_and_source
    record = release_record
    ensure_true(record && record['status'] == 'deployed' && record['chart'] == 'basic-pitch-0.1.1',
                'expected deployed Basic Pitch 0.1.1 before stabilization upgrade')
    installed = indexed(documents(helm('get', 'manifest', 'clouddsp-basic-pitch', '--namespace', 'clouddsp-app')))
    expected_old = Marshal.load(Marshal.dump(@rendered))
    scaler_id = ['ScaledObject', 'clouddsp-app', SCALER]
    deployment_id = ['Deployment', 'clouddsp-app', 'clouddsp-basic-pitch']
    expected_old[deployment_id] = baseline_without_numba_env(@rendered.fetch(deployment_id))
    expected_old.fetch(scaler_id).dig('spec', 'advanced', 'horizontalPodAutoscalerConfig', 'behavior', 'scaleDown')['stabilizationWindowSeconds'] = 60
    ensure_true(installed == expected_old, 'installed Basic Pitch release differs from the reviewed v0.1.1 baseline')
    live = live_objects
    ensure_true(live.fetch(deployment_id).dig('spec', 'replicas') == 0 &&
                live.fetch(deployment_id).dig('status', 'replicas').to_i == 0,
                'Basic Pitch must be idle before the portable Numba rollout')
    expected_old.each do |id, object|
      difference = first_difference(object.fetch('spec'), normalized_live_spec(live.fetch(id), object.fetch('spec')))
      ensure_true(difference.nil?, "live #{id.last} differs from installed baseline at #{difference}")
    end
    hpa_before = JSON.parse(kubectl('get', "hpa/#{HPA}", '--output', 'json'))
    before_uids = [live.fetch(deployment_id), live.fetch(scaler_id), hpa_before].map { |object| object.dig('metadata', 'uid') }

    output = command('helm', 'upgrade', 'clouddsp-basic-pitch', @chart.to_s,
                     '--kube-context', CONTEXT, '--namespace', 'clouddsp-app',
                     '--force-conflicts', '--wait', '--timeout', '3m')
    puts output.lines.grep(/^(NAME|NAMESPACE|STATUS|REVISION):/)
    ensure_true(release_record&.dig('status') == 'deployed' && release_record&.dig('chart') == 'basic-pitch-0.1.3',
                'Basic Pitch v0.1.3 release is not deployed')
    ensure_true(indexed(documents(helm('get', 'manifest', 'clouddsp-basic-pitch', '--namespace', 'clouddsp-app'))) == @rendered,
                'installed v0.1.3 manifest differs from reviewed chart')
    current = live_objects
    @rendered.each do |id, object|
      difference = first_difference(object.fetch('spec'), normalized_live_spec(current.fetch(id), object.fetch('spec')))
      ensure_true(difference.nil?, "live #{id.last} differs from v0.1.3 at #{difference}")
    end
    hpa_after = JSON.parse(kubectl('get', "hpa/#{HPA}", '--output', 'json'))
    after_uids = [current.fetch(deployment_id), current.fetch(scaler_id), hpa_after].map { |object| object.dig('metadata', 'uid') }
    ensure_true(after_uids == before_uids, 'Basic Pitch worker, scaler, or HPA identity changed')
    scaler = current.fetch(scaler_id)
    ensure_true(scaler.fetch('status', {}).fetch('conditions', []).any? { |item| item['type'] == 'Ready' && item['status'] == 'True' },
                'Basic Pitch scaler is not Ready after stabilization upgrade')
    puts 'Basic Pitch final scaling and portable Numba policy installed; worker, scaler and HPA identities preserved.'
  rescue StandardError => error
    warn "Basic Pitch stabilization upgrade stopped: #{error.message}"
    warn 'Inspect the Helm revision and live scaler before any retry; do not uninstall the adopted release.'
    exit 1
  end

  # Apply the portable Numba CPU target to the already stabilized worker.
  # Compare the installed and live resources with the reviewed 0.1.2
  # baseline so only the Deployment Pod template changes in this upgrade.
  def upgrade_numba
    check_tools
    check_chart_and_source
    record = release_record
    ensure_true(record && record['status'] == 'deployed' && record['chart'] == 'basic-pitch-0.1.2',
                'expected deployed Basic Pitch 0.1.2 before portable Numba upgrade')
    deployment_id = ['Deployment', 'clouddsp-app', 'clouddsp-basic-pitch']
    scaler_id = ['ScaledObject', 'clouddsp-app', SCALER]
    installed = indexed(documents(helm('get', 'manifest', 'clouddsp-basic-pitch', '--namespace', 'clouddsp-app')))
    expected_old = Marshal.load(Marshal.dump(@rendered))
    expected_old[deployment_id] = baseline_without_numba_env(@rendered.fetch(deployment_id))
    ensure_true(installed == expected_old, 'installed release differs from the reviewed Basic Pitch 0.1.2 baseline')
    live = live_objects
    ensure_true(live.keys.sort == installed.keys.sort, 'live Basic Pitch resource identities changed')
    installed.each do |id, object|
      ensure_true(live.fetch(id).dig('metadata', 'uid'), "#{id.last} lost its resource UID")
      difference = first_difference(object.fetch('spec'), normalized_live_spec(live.fetch(id), object.fetch('spec')))
      ensure_true(difference.nil?, "live #{id.last} differs from installed baseline at #{difference}")
    end
    hpa_before = JSON.parse(kubectl('get', "hpa/#{HPA}", '--output', 'json'))
    before_uids = [live.fetch(deployment_id), live.fetch(scaler_id), hpa_before].map { |object| object.dig('metadata', 'uid') }

    output = command('helm', 'upgrade', 'clouddsp-basic-pitch', @chart.to_s,
                     '--kube-context', CONTEXT, '--namespace', 'clouddsp-app',
                     '--force-conflicts', '--wait', '--timeout', '5m')
    puts output.lines.grep(/^(NAME|NAMESPACE|STATUS|REVISION):/)
    ensure_true(release_record&.dig('status') == 'deployed' && release_record&.dig('chart') == 'basic-pitch-0.1.3',
                'Basic Pitch v0.1.3 release is not deployed')
    ensure_true(indexed(documents(helm('get', 'manifest', 'clouddsp-basic-pitch', '--namespace', 'clouddsp-app'))) == @rendered,
                'installed v0.1.3 manifest differs from reviewed chart')
    current = live_objects
    @rendered.each do |id, object|
      difference = first_difference(object.fetch('spec'), normalized_live_spec(current.fetch(id), object.fetch('spec')))
      ensure_true(difference.nil?, "live #{id.last} differs from v0.1.3 at #{difference}")
    end
    hpa_after = JSON.parse(kubectl('get', "hpa/#{HPA}", '--output', 'json'))
    after_uids = [current.fetch(deployment_id), current.fetch(scaler_id), hpa_after].map { |object| object.dig('metadata', 'uid') }
    ensure_true(after_uids == before_uids, 'Basic Pitch worker, scaler, or HPA identity changed')
    ensure_true(current.fetch(scaler_id).fetch('status', {}).fetch('conditions', []).any? { |item| item['type'] == 'Ready' && item['status'] == 'True' },
                'Basic Pitch scaler is not Ready after portable Numba upgrade')
    puts 'Basic Pitch portable Numba target installed; worker, scaler and HPA identities preserved.'
  rescue StandardError => error
    warn "Basic Pitch Numba upgrade stopped: #{error.message}"
    warn 'Inspect the Helm revision and live Deployment before any retry; do not uninstall the adopted release.'
    exit 1
  end

  private

  def baseline_without_numba_env(object)
    baseline = Marshal.load(Marshal.dump(object))
    environment = baseline.fetch('spec').fetch('template').fetch('spec').fetch('containers').fetch(0).fetch('env')
    ensure_true(environment.count { |item| item['name'] == 'NUMBA_CPU_NAME' && item['value'] == 'generic' } == 1,
                'reviewed Deployment must set exactly one portable Numba CPU target')
    environment.reject! { |item| item['name'] == 'NUMBA_CPU_NAME' }
    baseline
  end

  def check_cluster(owner, allow_failed_release: false)
    result = super
    scaler = result.fetch(:objects).fetch(['ScaledObject', 'clouddsp-app', SCALER])
    ensure_true(scaler.dig('status', 'conditions').any? { |item| item['type'] == 'Ready' && item['status'] == 'True' },
                'Basic Pitch ScaledObject is not Ready')
    ensure_true(scaler.dig('spec', 'triggers', 0, 'authenticationRef', 'name') == 'clouddsp-rabbitmq-scaler-authentication' &&
                scaler.dig('spec', 'triggers', 1, 'authenticationRef', 'name') == 'clouddsp-demucs-postgresql-scaler-authentication',
                'Basic Pitch scaler no longer references both shared KEDA authentications')
    hpa = JSON.parse(kubectl('get', "hpa/#{HPA}", '--output', 'json'))
    ensure_true(hpa.dig('spec', 'scaleTargetRef') == { 'apiVersion' => 'apps/v1', 'kind' => 'Deployment', 'name' => 'clouddsp-basic-pitch' },
                'generated HPA target changed')
    owners = hpa.dig('metadata', 'ownerReferences') || []
    ensure_true(owners.length == 1 && owners.first['controller'] == true &&
                owners.first['kind'] == 'ScaledObject' && owners.first['name'] == SCALER &&
                owners.first['uid'] == scaler.dig('metadata', 'uid'),
                'generated HPA is not owned by the reviewed ScaledObject')
    result.merge(hpa_uid: hpa.dig('metadata', 'uid'), scaler_generation: scaler.dig('metadata', 'generation'))
  end

  def verify_stable_identity(before, after)
    super
    ensure_true(before.fetch(:hpa_uid) == after.fetch(:hpa_uid), 'KEDA generated HPA was replaced during adoption')
    ensure_true(before.fetch(:scaler_generation) == after.fetch(:scaler_generation),
                'ScaledObject spec generation changed during ownership-only adoption')
  end
end

BasicPitchRelease.new.run(ARGV.length == 1 ? ARGV.first : nil)
