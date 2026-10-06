#!/usr/bin/env ruby
# Prepare only the fresh-cluster foundation and its locked image mirror.
# Each child is a separately versioned, independently verifiable stage. The
# order matters: require an absent target cluster and check public digests
# while creation can still be avoided, create its registry and namespaces, then
# copy images into that registry and verify both sides. A failed child stops
# here; it never skips ahead to an incomplete application installation.
require_relative '../lib/paths'
require 'rbconfig'

class CloudDSPPrepare
  STEPS = [
    ['fresh foundation plan', 'deploy-local-foundation.rb', 'plan'],
    ['locked image plan', 'image-registry-stage.rb', 'plan'],
    ['public image source', 'image-registry-stage.rb', 'verify-source'],
    ['fresh foundation bootstrap', 'deploy-local-foundation.rb', 'bootstrap'],
    ['local image mirror', 'image-registry-stage.rb', 'mirror'],
    ['image digest verification', 'image-registry-stage.rb', 'verify']
  ].freeze

  def initialize(run_command: method(:system), output: $stdout, error: $stderr)
    @run_command = run_command
    @output = output
    @error = error
  end

  def run
    STEPS.each_with_index do |(label, script, mode), index|
      @output.puts "CloudDSP prepare #{index + 1}/#{STEPS.length}: #{label}"
      @output.flush
      next if @run_command.call(RbConfig.ruby, CloudDSPPaths.script(script).to_s, mode)

      @error.puts "CloudDSP prepare stopped at #{label}; inspect that stage before retrying."
      return 1
    end
    @output.puts 'CloudDSP prepare complete: cluster, namespaces, and locked images ready; Helm releases remain to install.'
    0
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: deploy-local-prepare.rb (no arguments)' unless ARGV.empty?
  exit CloudDSPPrepare.new.run
end
