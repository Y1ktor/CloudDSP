#!/usr/bin/env ruby
# Compose only RabbitMQ's fresh-cluster path. Preparation creates the reviewed
# cluster and mirrors its locked images. The Secret stage creates and verifies
# the broker administrator credentials before Helm may create the StatefulSet
# and its generated data claim. A failed child leaves the partial deployment
# for inspection; this runner never adopts existing broker resources.
require 'rbconfig'

class CloudDSPBootstrapRabbitmq
  SCRIPT_DIRECTORY = File.expand_path(__dir__).freeze
  STEPS = [
    ['fresh foundation and locked images', 'deploy-local-prepare.rb'],
    ['RabbitMQ credential Secret', 'rabbitmq-secret-stage.rb', 'bootstrap'],
    ['fresh RabbitMQ Helm install', 'rabbitmq-release.rb', 'install'],
    ['RabbitMQ Helm and PVC verification', 'rabbitmq-release.rb', 'verify']
  ].freeze

  def initialize(run_command: method(:system), output: $stdout, error: $stderr)
    @run_command = run_command
    @output = output
    @error = error
  end

  def run
    STEPS.each_with_index do |(label, script, *arguments), index|
      @output.puts "CloudDSP bootstrap-rabbitmq #{index + 1}/#{STEPS.length}: #{label}"
      @output.flush
      next if @run_command.call(RbConfig.ruby, File.join(SCRIPT_DIRECTORY, script), *arguments)

      @error.puts "CloudDSP bootstrap-rabbitmq stopped at #{label}; inspect that stage before retrying."
      return 1
    end
    @output.puts 'CloudDSP bootstrap-rabbitmq complete: cluster, images, credentials, and broker release ready; remaining services are pending.'
    0
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: deploy-local-rabbitmq.rb (no arguments)' unless ARGV.empty?
  exit CloudDSPBootstrapRabbitmq.new.run
end
