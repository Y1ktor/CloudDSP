# Adapt a worker's fixed adoption baseline to the policy in Helm values.
# Only capacity, timing, and metric thresholds are configurable here. Resource
# targets, private endpoints, SQL, and authentication still come from the
# reviewed baseline and must match the rendered, installed, and live chart.
class WorkerScalingPolicy
  REPLICA_AND_TIMING_FIELDS = %w[pollingInterval cooldownPeriod minReplicaCount maxReplicaCount].freeze
  RABBITMQ_FIELDS = {
    'value' => 'queueLength', 'activationValue' => 'activationValue', 'timeout' => 'timeout'
  }.freeze
  POSTGRESQL_FIELDS = {
    'targetQueryValue' => 'targetQueryValue', 'activationTargetQueryValue' => 'activationTargetQueryValue'
  }.freeze

  def initialize(values)
    # Helm validates types and ranges against values.schema.json before this
    # adapter runs. Fetch required keys so an incomplete policy cannot fall
    # back silently to a different capacity during verification.
    @values = values.fetch('autoscaling')
  end

  def minimum_replicas
    @values.fetch('minReplicaCount')
  end

  def maximum_replicas
    @values.fetch('maxReplicaCount')
  end

  def configured_source(object)
    configured = Marshal.load(Marshal.dump(object))
    return configured unless configured.fetch('kind') == 'ScaledObject'

    spec = configured.fetch('spec')
    REPLICA_AND_TIMING_FIELDS.each { |field| spec[field] = @values.fetch(field) }
    behavior = spec.fetch('advanced').fetch('horizontalPodAutoscalerConfig').fetch('behavior')
    %w[scaleUp scaleDown].each do |direction|
      policy = @values.fetch('behavior').fetch(direction)
      behavior.fetch(direction)['stabilizationWindowSeconds'] = policy.fetch('stabilizationWindowSeconds')
      # Each reviewed worker has exactly one Pods policy in each direction.
      # Its type and list structure remain fixed rather than becoming values.
      step = behavior.fetch(direction).fetch('policies').fetch(0)
      step['value'] = policy.fetch('value')
      step['periodSeconds'] = policy.fetch('periodSeconds')
    end
    spec.fetch('triggers').each do |trigger|
      case trigger.fetch('type')
      when 'rabbitmq'
        configure_thresholds(trigger, @values.fetch('rabbitmq'), RABBITMQ_FIELDS)
      when 'postgresql'
        configure_thresholds(trigger, @values.fetch('postgresql'), POSTGRESQL_FIELDS)
      end
    end
    configured
  end

  private

  def configure_thresholds(trigger, values, fields)
    # KEDA trigger metadata requires strings even though chart values use
    # integers for useful schema validation and unambiguous units.
    fields.each { |metadata_key, value_key| trigger.fetch('metadata')[metadata_key] = values.fetch(value_key).to_s }
  end
end
