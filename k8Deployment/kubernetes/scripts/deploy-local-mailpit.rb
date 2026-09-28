#!/usr/bin/env ruby
# Compose the first fresh application release after the guarded cluster/image
# preparation. A child failure stops later stages, leaving any created cluster
# or Helm release available for inspection. This partial bootstrap is named
# for Mailpit so it cannot be confused with a complete CloudDSP deployment.
require 'rbconfig'

class CloudDSPBootstrapMailpit
  SCRIPT_DIRECTORY = File.expand_path(__dir__).freeze
  STEPS = [
    ['fresh foundation and locked images', 'deploy-local-prepare.rb'],
    ['fresh Mailpit Helm install', 'mailpit-release.rb', 'install'],
    ['Mailpit Helm verification', 'mailpit-release.rb', 'verify']
  ].freeze

  def initialize(run_command: method(:system), output: $stdout, error: $stderr)
    @run_command = run_command
    @output = output
    @error = error
  end

  def run
    STEPS.each_with_index do |(label, script, *arguments), index|
      @output.puts "CloudDSP bootstrap-mailpit #{index + 1}/#{STEPS.length}: #{label}"
      @output.flush
      next if @run_command.call(RbConfig.ruby, File.join(SCRIPT_DIRECTORY, script), *arguments)

      @error.puts "CloudDSP bootstrap-mailpit stopped at #{label}; inspect that stage before retrying."
      return 1
    end
    @output.puts 'CloudDSP bootstrap-mailpit complete: cluster, images, and Mailpit ready; remaining application stages are pending.'
    0
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: deploy-local-mailpit.rb (no arguments)' unless ARGV.empty?
  exit CloudDSPBootstrapMailpit.new.run
end
