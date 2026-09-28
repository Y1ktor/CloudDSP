#!/usr/bin/env ruby
# Compose a partial fresh-cluster path through the MinIO release. The nested
# RabbitMQ bootstrap owns foundation/image preparation and broker creation.
# MinIO's root and AMQP Secrets plus the upload-intake runtime Secret must
# precede the source-intake broker Job, which creates restricted users and
# topology. Only after that broker state verifies may Helm create MinIO's
# StatefulSet and generated PVC. Only then can the fresh bucket stage create
# the private uploads and shared-sample boundaries. The shared samples are
# mirrored and made read-only to browsers only after their checked-in hashes
# match. A failed child leaves all partial state for inspection; this runner
# never retries by taking ownership or deleting data.
require 'rbconfig'

class CloudDSPBootstrapMinio
  SCRIPT_DIRECTORY = File.expand_path(__dir__).freeze
  STEPS = [
    ['fresh foundation, images, and RabbitMQ', 'deploy-local-rabbitmq.rb'],
    ['MinIO root credential Secret', 'minio-root-secret-stage.rb', 'bootstrap'],
    ['MinIO AMQP credential Secret', 'minio-amqp-secret-stage.rb', 'bootstrap'],
    ['upload-intake RabbitMQ runtime Secret', 'upload-intake-rabbitmq-secret-stage.rb', 'bootstrap'],
    ['RabbitMQ source-intake topology and restricted users', 'rabbitmq-source-intake-bootstrap.rb', 'reconcile'],
    ['RabbitMQ source-intake verification', 'rabbitmq-source-intake-bootstrap.rb', 'verify'],
    ['fresh MinIO Helm install', 'minio-release.rb', 'install'],
    ['MinIO Helm, PVC, and S3 route verification', 'minio-release.rb', 'verify'],
    ['fresh MinIO bucket boundaries', 'minio-fresh-buckets-stage.rb', 'bootstrap'],
    ['MinIO bucket boundary verification', 'minio-fresh-buckets-stage.rb', 'verify'],
    ['fresh shared MIDI sample mirror', 'minio-fresh-samples-stage.rb', 'bootstrap'],
    ['shared MIDI sample and browser policy verification', 'minio-fresh-samples-stage.rb', 'verify']
  ].freeze

  def initialize(run_command: method(:system), output: $stdout, error: $stderr)
    @run_command = run_command
    @output = output
    @error = error
  end

  def run
    STEPS.each_with_index do |(label, script, *arguments), index|
      @output.puts "CloudDSP bootstrap-minio #{index + 1}/#{STEPS.length}: #{label}"
      @output.flush
      next if @run_command.call(RbConfig.ruby, File.join(SCRIPT_DIRECTORY, script), *arguments)

      @error.puts "CloudDSP bootstrap-minio stopped at #{label}; inspect that stage before retrying."
      return 1
    end
    @output.puts 'CloudDSP bootstrap-minio complete: broker, MinIO, and 461 locked shared samples ready; IAM and applications remain pending.'
    0
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: deploy-local-minio.rb (no arguments)' unless ARGV.empty?
  exit CloudDSPBootstrapMinio.new.run
end
