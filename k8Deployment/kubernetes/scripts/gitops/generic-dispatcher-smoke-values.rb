#!/usr/bin/env ruby
# Preserve the generic-only staging entrypoint for controlled routing checks.
# The shared editor validates a fixed identity and stages one reviewed line;
# dispatchers-smoke-values.rb stages both publishers for the intake smoke.
require_relative 'dispatchers-smoke-values'
require_relative 'generic-dispatcher-flux-ownership'

module GenericDispatcherSmokeValues
  MANIFEST = DispatcherSmokeValues::CONFIG.fetch('generic-dispatcher').fetch(:manifest)
  ACTIVE = DispatcherSmokeValues.active_line('generic-dispatcher').freeze
  INACTIVE = DispatcherSmokeValues.inactive_line('generic-dispatcher').freeze

  def self.changed_text(text, mode)
    DispatcherSmokeValues.changed_text(text, mode, component: 'generic-dispatcher')
  end

  def self.run(mode)
    DispatcherSmokeValues.run(mode, components: ['generic-dispatcher'])
  end
end

GenericDispatcherSmokeValues.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
