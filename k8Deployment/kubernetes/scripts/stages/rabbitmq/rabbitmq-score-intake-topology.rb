#!/usr/bin/env ruby
# Reconcile the additive score queue without consuming or inspecting message
# bodies. The durable broker topology, not the TTL-bound Job, is the evidence.
require_relative 'rabbitmq-processing-topology'

class RabbitmqScoreIntakeTopology < RabbitmqProcessingTopology
  CONFIG = 'rabbitmq-score-intake-topology-v001'.freeze
  JOB = 'rabbitmq-score-intake-topology-v002-bootstrap'.freeze

  def run(mode)
    raise ArgumentError, 'use plan, verify, or reconcile' unless MODES.include?(mode)

    config_path = SOURCE.join("#{CONFIG}-configmap.yaml")
    job_path = SOURCE.join("#{JOB}-job.yaml")
    config = YAML.load_file(config_path)
    job = YAML.load_file(job_path)
    definition = JSON.parse(config.fetch('data').fetch("#{CONFIG}.json"))
    ensure_true(config['immutable'] == true && config.dig('metadata', 'name') == CONFIG &&
                definition.keys.sort == %w[bindings queues] &&
                definition.fetch('queues').length == 3 &&
                definition.fetch('bindings').length == 3 &&
                job.dig('metadata', 'name') == JOB,
                'score topology source identity changed')
    stage = Stage.new(version: 'score-v001', config_name: CONFIG, job_name: JOB,
                      config_path: config_path, job_path: job_path,
                      config: config, job: job, definition: definition)
    broker = snapshot
    source_exchange = broker.fetch('exchanges').find { |item| item['name'] == 'clouddsp.source-events' }
    ensure_true(!source_exchange.nil?, 'source-events exchange prerequisite is absent')
    state = assess(stage, broker)
    inspect_kubernetes(stage, state)
    if state == :ready
      puts "RabbitMQ score-intake topology #{mode}: verified"
      return
    end
    raise 'score-intake topology is incomplete' if mode == 'verify'
    if mode == 'plan'
      puts 'RabbitMQ score-intake topology: versioned import pending'
      return
    end
    ensure_prerequisites
    ensure_true(assess(stage, snapshot) == :absent, 'score topology changed during preflight')
    install(stage)
    ensure_true(assess(stage, snapshot) == :ready, 'score topology import did not complete')
    puts 'RabbitMQ score-intake topology: applied and verified'
  rescue StandardError => error
    warn "RabbitMQ score-intake topology #{mode} stopped: #{error.message}"
    exit 1
  end
end

RabbitmqScoreIntakeTopology.new.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
