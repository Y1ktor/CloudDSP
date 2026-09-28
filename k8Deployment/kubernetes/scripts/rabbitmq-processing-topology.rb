#!/usr/bin/env ruby
# Reconcile the two versioned processing-topology imports independently of
# Helm. RabbitMQ's live exchanges, queues, and bindings are the durable
# completion evidence; a Kubernetes Job may disappear after its TTL expires.
# This stage reads no password, password hash, message body, or queue payload.
require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class RabbitmqProcessingTopology
  CONTEXT = 'k3d-clouddsp-local'.freeze
  NAMESPACE = 'clouddsp-data'.freeze
  POD = 'pod/clouddsp-rabbitmq-0'.freeze
  VHOST = '/clouddsp'.freeze
  ROOT = Pathname.new(File.expand_path('..', __dir__)).freeze
  SOURCE = ROOT.join('services', 'rabbitmq').freeze
  MODES = %w[plan verify reconcile].freeze
  VERSIONS = [
    ['v001', 'rabbitmq-processing-topology-v001', 'rabbitmq-processing-topology-bootstrap', 3, 3, 3],
    ['v002', 'rabbitmq-processing-topology-v002-downstream', 'rabbitmq-processing-topology-v002-downstream-bootstrap', 0, 6, 6]
  ].freeze
  Stage = Struct.new(:version, :config_name, :job_name, :config_path, :job_path,
                     :config, :job, :definition, keyword_init: true)

  def initialize(runner: nil)
    @runner = runner || method(:capture)
  end

  def run(mode)
    raise ArgumentError, 'use plan, verify, or reconcile' unless MODES.include?(mode)

    stages = load_stages
    broker = snapshot
    # Audit both versions before the first write. In particular, an incomplete
    # v002 must stop a fresh v001 import rather than leave a mixed version set.
    states = stages.map { |stage| assess(stage, broker) }
    ensure_true(states != %i[absent ready], 'v002 exists without its v001 prerequisite')
    stages.each_with_index { |stage, index| inspect_kubernetes(stage, states.fetch(index)) }

    stages.each_with_index do |stage, index|
      state = states.fetch(index)
      if state == :ready
        puts "RabbitMQ processing topology #{stage.version}: verified; no work pending"
        next
      end

      puts "RabbitMQ processing topology #{stage.version}: versioned import pending"
      if mode == 'verify'
        raise "#{stage.version} processing topology is incomplete"
      elsif mode == 'reconcile'
        ensure_true(index.zero? || states.fetch(index - 1) == :ready,
                    "#{stage.version} needs the prior topology version")
        ensure_prerequisites
        # Recheck immediately before importing: another administrator may
        # have changed the broker after the initial read-only audit.
        current = snapshot
        stages.take(index).each { |previous| ensure_true(assess(previous, current) == :ready,
                                                        "#{stage.version} prerequisite topology changed") }
        ensure_true(assess(stage, current) == :absent,
                    "#{stage.version} broker topology changed during preflight")
        install(stage)
        broker = snapshot
        ensure_true(assess(stage, broker) == :ready,
                    "#{stage.version} Job completed without the expected broker topology")
        stages.take(index).each { |previous| ensure_true(assess(previous, broker) == :ready,
                                                        "#{stage.version} import changed prior topology") }
        states[index] = :ready
        puts "RabbitMQ processing topology #{stage.version}: applied and verified"
      end
    end
    puts "RabbitMQ processing topology #{mode} passed"
  rescue StandardError => error
    warn "RabbitMQ processing topology #{mode} stopped: #{error.message}"
    exit 1
  end

  # Classify the exact objects declared by one immutable definition. A fully
  # absent version may be installed; a partially imported or drifted version
  # needs inspection because repeating an import can hide broker-side damage.
  def assess(stage, broker)
    definition = stage.definition
    expected_vhosts = definition.fetch('vhosts', [])
    expected_vhosts.each { |vhost| ensure_true(vhost == { 'name' => VHOST }, 'unexpected processing vhost definition') }
    found = 0
    total = 0
    {
      'exchanges' => method(:exchange_identity),
      'queues' => method(:queue_identity),
      'bindings' => method(:binding_identity)
    }.each do |kind, identity|
      definition.fetch(kind, []).each do |expected|
        total += 1
        actual = broker.fetch(kind).find { |item| identity.call(item) == identity.call(expected) }
        next unless actual

        found += 1
        ensure_true(normalize(kind, actual) == normalize(kind, expected),
                    "#{stage.version} #{kind.chomp('s')} #{identity.call(expected)} drifted")
      end
    end
    # RabbitMQ creates an implicit binding from the default exchange to every
    # queue. Ignore those, but reject any extra explicit route into a queue
    # owned by this version: it could deliver unintended work to a worker.
    queue_names = definition.fetch('queues', []).map { |queue| queue.fetch('name') }
    routes = definition.fetch('bindings', []).map { |binding| binding_identity(binding) }
    broker.fetch('bindings').each do |binding|
      next unless queue_names.include?(binding['destination'] || binding['destination_name'])
      next if (binding['source'] || binding['source_name']).to_s.empty?

      ensure_true(routes.include?(binding_identity(binding)),
                  "#{stage.version} queue has an unexpected explicit binding")
    end
    return :absent if found.zero?

    ensure_true(broker.fetch('vhosts').include?(VHOST), "#{stage.version} vhost is absent")
    ensure_true(found == total, "#{stage.version} processing topology is partially present")
    :ready
  end

  def load_stages
    VERSIONS.map do |version, config_name, job_name, exchanges, queues, bindings|
      config_path = SOURCE.join("#{config_name}-configmap.yaml")
      job_path = SOURCE.join("#{job_name}-job.yaml")
      config = YAML.load_file(config_path)
      job = YAML.load_file(job_path)
      key = "#{config_name}.json"
      ensure_true(config['kind'] == 'ConfigMap' && config.dig('metadata', 'name') == config_name &&
                  config.dig('metadata', 'namespace') == NAMESPACE && config['immutable'] == true &&
                  config.fetch('data').keys == [key], "#{version} immutable ConfigMap contract changed")
      definition = JSON.parse(config.fetch('data').fetch(key))
      ensure_true((definition.keys - %w[vhosts exchanges queues bindings]).empty? &&
                  definition.fetch('exchanges', []).length == exchanges &&
                  definition.fetch('queues', []).length == queues &&
                  definition.fetch('bindings', []).length == bindings &&
                  definition.fetch('vhosts', []).length == (version == 'v001' ? 1 : 0),
                  "#{version} source contains unexpected broker definitions")
      %w[exchanges queues bindings].each do |kind|
        definition.fetch(kind, []).each do |object|
          ensure_true(object['vhost'] == VHOST && object.fetch('arguments', {}).is_a?(Hash),
                      "#{version} #{kind} vhost or arguments changed")
        end
      end
      volumes = job.dig('spec', 'template', 'spec', 'volumes') || []
      config_volume = volumes.find { |volume| volume.dig('configMap', 'name') == config_name }
      init_containers = job.dig('spec', 'template', 'spec', 'initContainers') || []
      admin_ref = init_containers.any? { |container|
        (container['env'] || []).any? { |entry|
          entry.dig('valueFrom', 'secretKeyRef', 'name') == 'clouddsp-rabbitmq-credentials'
        }
      }
      ensure_true(job['kind'] == 'Job' && job.dig('metadata', 'name') == job_name &&
                  job.dig('metadata', 'namespace') == NAMESPACE &&
                  job.dig('spec', 'backoffLimit') == 0 &&
                  job.dig('spec', 'activeDeadlineSeconds').to_i.positive? &&
                  job.dig('spec', 'template', 'spec', 'automountServiceAccountToken') == false &&
                  config_volume && config_volume.dig('configMap', 'items')&.any? { |item| item['key'] == key } &&
                  admin_ref, "#{version} bootstrap Job safety contract changed")
      Stage.new(version: version, config_name: config_name, job_name: job_name,
                config_path: config_path, job_path: job_path,
                config: config, job: job, definition: definition)
    end
  end

  private

  def ensure_true(condition, message)
    raise message unless condition
  end

  def capture(*argv)
    stdout, _stderr, status = Open3.capture3(*argv)
    raise "cluster read or write failed (exit #{status.exitstatus}); inspect the named Pod or Job" unless status.success?

    stdout
  end

  def kubectl(*args)
    @runner.call('kubectl', '--context', CONTEXT, '--namespace', NAMESPACE, *args)
  end

  def broker_json(*args)
    JSON.parse(kubectl('exec', POD, '--', 'rabbitmqctl', '-q', *args, '--formatter', 'json'))
  end

  def snapshot
    vhosts = broker_json('list_vhosts', 'name').map { |row| row.fetch('name') }
    return { 'vhosts' => vhosts, 'exchanges' => [], 'queues' => [], 'bindings' => [] } unless vhosts.include?(VHOST)

    {
      'vhosts' => vhosts,
      'exchanges' => broker_json('-p', VHOST, 'list_exchanges', 'name', 'type', 'durable', 'auto_delete', 'internal', 'arguments'),
      'queues' => broker_json('-p', VHOST, 'list_queues', 'name', 'durable', 'auto_delete', 'arguments'),
      'bindings' => broker_json('-p', VHOST, 'list_bindings', 'source_name', 'destination_name', 'destination_kind', 'routing_key', 'arguments')
    }
  end

  def exchange_identity(item)
    item.fetch('name')
  end

  def queue_identity(item)
    item.fetch('name')
  end

  def binding_identity(item)
    [item['source'] || item['source_name'], item['destination'] || item['destination_name'],
     item['destination_type'] || item['destination_kind'], item['routing_key']]
  end

  def arguments(value)
    return value if value.is_a?(Hash)

    ensure_true(value.is_a?(Array) && value.all? { |item| item.is_a?(Array) && item.length == 3 },
                'unexpected RabbitMQ argument format')
    value.to_h { |name, _type, content| [name, content] }
  end

  def normalize(kind, object)
    case kind
    when 'exchanges'
      %w[name type durable auto_delete internal].to_h { |key| [key, object.fetch(key)] }
        .merge('arguments' => arguments(object.fetch('arguments')))
    when 'queues'
      %w[name durable auto_delete].to_h { |key| [key, object.fetch(key)] }
        .merge('arguments' => arguments(object.fetch('arguments')))
    when 'bindings'
      {
        'source' => object['source'] || object['source_name'],
        'destination' => object['destination'] || object['destination_name'],
        'destination_type' => object['destination_type'] || object['destination_kind'],
        'routing_key' => object.fetch('routing_key'),
        'arguments' => arguments(object.fetch('arguments'))
      }
    end
  end

  def live_object(kind, name)
    output = kubectl('get', "#{kind}/#{name}", '--ignore-not-found', '--output=json').strip
    output.empty? ? nil : JSON.parse(output)
  end

  def inspect_kubernetes(stage, state)
    config = live_object('configmap', stage.config_name)
    if config
      ensure_true(config['immutable'] == true && config['data'] == stage.config['data'],
                  "#{stage.version} live immutable ConfigMap differs from source")
    else
      ensure_true(state == :absent, "#{stage.version} broker topology has no immutable source ConfigMap")
    end
    job = live_object('job', stage.job_name)
    if state == :absent
      ensure_true(job.nil?, "#{stage.version} bootstrap Job exists without complete broker topology; inspect it")
    elsif job
      ensure_true(job.fetch('status', {}).fetch('conditions', []).any? { |condition|
                    condition['type'] == 'Complete' && condition['status'] == 'True'
                  }, "#{stage.version} bootstrap Job remains active or failed")
    end
    config
  end

  def ensure_prerequisites
    kubectl('get', 'secret/clouddsp-rabbitmq-credentials', '--output=name')
    sts = JSON.parse(kubectl('get', 'statefulset/clouddsp-rabbitmq', '--output=json'))
    ensure_true(sts.dig('status', 'readyReplicas') == 1, 'RabbitMQ StatefulSet must be Ready')
  end

  def install(stage)
    unless live_object('configmap', stage.config_name)
      kubectl('create', '--dry-run=server', '--filename', stage.config_path.to_s)
      kubectl('create', '--filename', stage.config_path.to_s)
      config = live_object('configmap', stage.config_name)
      ensure_true(config && config['immutable'] == true && config['data'] == stage.config['data'],
                  "#{stage.version} created ConfigMap differs from source")
    end
    kubectl('create', '--dry-run=server', '--filename', stage.job_path.to_s)
    kubectl('create', '--filename', stage.job_path.to_s)
    timeout = stage.job.dig('spec', 'activeDeadlineSeconds').to_i + 60
    kubectl('wait', '--for=condition=complete', "job/#{stage.job_name}", "--timeout=#{timeout}s")
  rescue StandardError => error
    raise "#{stage.version}: #{error.message}; inspect the fixed-name bootstrap Job before retrying"
  end
end

RabbitmqProcessingTopology.new.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
