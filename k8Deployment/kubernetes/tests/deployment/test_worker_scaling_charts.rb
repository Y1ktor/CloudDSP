# Exercise the real worker Helm charts without contacting Kubernetes. The
# original service manifests remain the reviewed default snapshots; custom
# scaling values may change only the documented policy fields, never worker
# Pod templates, private endpoints, authentication, or durable-task queries.
require 'minitest/autorun'
require 'open3'
require 'pathname'
require 'tempfile'
require 'yaml'

class WorkerScalingChartsTest < Minitest::Test
  ROOT = Pathname.new(__dir__).join('../..').expand_path
  WORKERS = %w[demucs basic-pitch adtof].freeze
  SCALING_FIELDS = %w[pollingInterval cooldownPeriod minReplicaCount maxReplicaCount].freeze

  def test_default_charts_preserve_reviewed_source_manifests
    WORKERS.each do |worker|
      rendered = render(worker)
      assert_equal %w[Deployment ScaledObject], rendered.map { |object| object.fetch('kind') }.sort,
                   "#{worker}: unexpected chart resources"
      rendered.each do |object|
        suffix = object.fetch('kind') == 'Deployment' ? 'deployment' : 'scaledobject'
        source = YAML.load_file(ROOT.join('services', worker, "#{worker}-#{suffix}.yaml"))
        assert_equal normalize_owner(source), object, "#{worker}: default #{suffix} differs from source"
      end
    end
  end

  def test_custom_values_change_all_scaling_settings_without_changing_the_deployment
    WORKERS.each do |worker|
      baseline = render(worker)
      settings = custom_settings(worker)
      customized = render(worker, settings)
      assert_equal resource(baseline, 'Deployment'), resource(customized, 'Deployment'),
                   "#{worker}: a policy-only change modified the Pod template"

      spec = resource(customized, 'ScaledObject').fetch('spec')
      SCALING_FIELDS.each do |field|
        assert_equal settings.fetch(field), spec.fetch(field), "#{worker}: #{field} was not applied"
      end
      behavior = spec.fetch('advanced').fetch('horizontalPodAutoscalerConfig').fetch('behavior')
      %w[scaleUp scaleDown].each do |direction|
        expected = settings.fetch('behavior').fetch(direction)
        actual = behavior.fetch(direction)
        assert_equal expected.fetch('stabilizationWindowSeconds'), actual.fetch('stabilizationWindowSeconds')
        assert_equal [{ 'type' => 'Pods', 'value' => expected.fetch('value'),
                        'periodSeconds' => expected.fetch('periodSeconds') }], actual.fetch('policies')
      end

      rabbitmq = trigger(spec, 'rabbitmq').fetch('metadata')
      { 'value' => 'queueLength', 'activationValue' => 'activationValue', 'timeout' => 'timeout' }.each do |field, value|
        assert_equal settings.fetch('rabbitmq').fetch(value).to_s, rabbitmq.fetch(field),
                     "#{worker}: RabbitMQ #{field} must be a quoted numeric metadata value"
      end
      next if worker == 'adtof'

      postgresql = trigger(spec, 'postgresql').fetch('metadata')
      %w[targetQueryValue activationTargetQueryValue].each do |field|
        assert_equal settings.fetch('postgresql').fetch(field).to_s, postgresql.fetch(field),
                     "#{worker}: PostgreSQL #{field} must be quoted metadata"
      end
    end
  end

  def test_policy_overrides_preserve_targets_authentication_and_sql
    WORKERS.each do |worker|
      baseline = resource(render(worker), 'ScaledObject')
      customized = resource(render(worker, custom_settings(worker)), 'ScaledObject')
      # Remove only the explicit operational allowlist. Comparing everything
      # else protects queue coordinates, task SQL, read-only authentication,
      # unacknowledged-message handling, and restore behavior together.
      assert_equal fixed_scaler_contract(baseline), fixed_scaler_contract(customized),
                   "#{worker}: an operational override changed the scaler's fixed contract"
    end
  end

  def test_invalid_policies_are_rejected_before_any_cluster_operation
    invalid = {
      'autoscaling.minReplicaCount=5,autoscaling.maxReplicaCount=4' => 'must not exceed',
      'autoscaling.pollingInterval=-1' => 'minimum',
      'autoscaling.cooldownPeriod=-1' => 'minimum',
      'autoscaling.minReplicaCount=-1' => 'minimum',
      'autoscaling.maxReplicaCount=0' => 'minimum',
      'autoscaling.rabbitmq.queueLength=0' => 'minimum',
      'autoscaling.rabbitmq.activationValue=-1' => 'minimum',
      'autoscaling.rabbitmq.timeout=0' => 'minimum',
      'autoscaling.behavior.scaleUp.stabilizationWindowSeconds=3601' => 'maximum',
      'autoscaling.behavior.scaleDown.stabilizationWindowSeconds=-1' => 'minimum',
      'autoscaling.behavior.scaleUp.value=0' => 'minimum',
      'autoscaling.behavior.scaleDown.periodSeconds=0' => 'minimum',
      'autoscaling.behavior.scaleDown.periodSeconds=1801' => 'maximum',
      'autoscaling.pollInterval=15' => 'additional properties'
    }
    WORKERS.each do |worker|
      invalid.each { |override, reason| assert_rejected(worker, '--set', override, reason) }
      assert_rejected(worker, '--set-string', 'autoscaling.rabbitmq.queueLength=two', 'integer')
      assert_rejected(worker, '--set-string', 'autoscaling.minReplicaCount=1', 'integer')
      next if worker == 'adtof'

      assert_rejected(worker, '--set', 'autoscaling.postgresql.targetQueryValue=0', 'minimum')
      assert_rejected(worker, '--set', 'autoscaling.postgresql.activationTargetQueryValue=-1', 'minimum')
    end
  end

  def test_all_worker_charts_lint_offline
    WORKERS.each do |worker|
      _output, error, status = helm('lint', chart(worker).to_s, '--strict')
      assert status.success?, "#{worker}: Helm lint failed: #{error.strip}"
    end
  end

  private

  def chart(worker)
    ROOT.join('helm', worker)
  end

  def helm(*arguments)
    Open3.capture3('helm', *arguments)
  rescue Errno::ENOENT
    flunk 'Helm must be installed to run the offline worker-chart regression tests'
  end

  def render(worker, settings = nil)
    arguments = ['template', "clouddsp-#{worker}", chart(worker).to_s]
    # The values file is temporary test input, never a deployed release or an
    # ignored .local credential file. Helm template performs client rendering.
    if settings
      Tempfile.create(['clouddsp-worker-policy-', '.yaml']) do |file|
        file.write(YAML.dump('autoscaling' => settings))
        file.flush
        return rendered_documents(worker, *arguments, '--values', file.path)
      end
    end
    rendered_documents(worker, *arguments)
  end

  def rendered_documents(worker, *arguments)
    output, error, status = helm(*arguments)
    assert status.success?, "#{worker}: Helm template failed: #{error.strip}"
    YAML.load_stream(output).compact
  end

  def resource(objects, kind)
    objects.find { |object| object.fetch('kind') == kind } || flunk("missing #{kind}")
  end

  def trigger(spec, type)
    spec.fetch('triggers').find { |item| item.fetch('type') == type } || flunk("missing #{type} trigger")
  end

  def normalize_owner(object)
    # Helm adoption changes this top-level ownership label only; preserve its
    # presence and every other source field while comparing default output.
    object.fetch('metadata').fetch('labels')['app.kubernetes.io/managed-by'] = 'Helm'
    object
  end

  def custom_settings(worker)
    settings = {
      'pollingInterval' => 12, 'cooldownPeriod' => 90, 'minReplicaCount' => 1, 'maxReplicaCount' => 4,
      'rabbitmq' => { 'queueLength' => 2, 'activationValue' => 1, 'timeout' => 8000 },
      'behavior' => {
        'scaleUp' => { 'stabilizationWindowSeconds' => 30, 'value' => 2, 'periodSeconds' => 45 },
        'scaleDown' => { 'stabilizationWindowSeconds' => 240, 'value' => 2, 'periodSeconds' => 120 }
      }
    }
    settings['postgresql'] = { 'targetQueryValue' => 2, 'activationTargetQueryValue' => 1 } unless worker == 'adtof'
    settings
  end

  def fixed_scaler_contract(object)
    fixed = Marshal.load(Marshal.dump(object))
    spec = fixed.fetch('spec')
    SCALING_FIELDS.each { |field| spec.delete(field) }
    spec.fetch('advanced').fetch('horizontalPodAutoscalerConfig').fetch('behavior').each_value do |behavior|
      behavior.delete('stabilizationWindowSeconds')
      behavior.fetch('policies').each { |policy| %w[value periodSeconds].each { |field| policy.delete(field) } }
    end
    spec.fetch('triggers').each do |item|
      fields = item.fetch('type') == 'rabbitmq' ? %w[value activationValue timeout] :
        %w[targetQueryValue activationTargetQueryValue]
      fields.each { |field| item.fetch('metadata').delete(field) }
    end
    fixed
  end

  def assert_rejected(worker, option, override, reason)
    _output, error, status = helm('template', "clouddsp-#{worker}", chart(worker).to_s, option, override)
    refute status.success?, "#{worker}: accepted invalid policy #{override}"
    # Helm releases use different schema-validation libraries. Require the
    # same rejection category without depending on their exact prose.
    pattern = case reason
              when 'minimum' then /minimum|greater than or equal/
              when 'maximum' then /maximum|less than or equal/
              when 'additional properties' then /additional propert/
              else Regexp.new(Regexp.escape(reason))
              end
    assert_match pattern, error.downcase, "#{worker}: unexpected rejection for #{override}"
  end
end
