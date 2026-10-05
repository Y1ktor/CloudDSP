#!/usr/bin/env ruby
# Stage the publisher's reviewed pause/restore in its VERSIONED HelmRelease.
# This edits one valuesFiles line, preserving comments and all other settings.
# Commit/push remains explicit: this script never writes to the cluster or Git.
require_relative '../lib/paths'
require_relative 'generic-dispatcher-flux-ownership'
require 'open3'
require 'yaml'

module GenericDispatcherSmokeValues
  MANIFEST = CloudDSPPaths::KUBERNETES_ROOT.join('gitops/clusters/clouddsp-local/generic-dispatcher/helmrelease.yaml')
  ACTIVE = "        - #{GenericDispatcherFluxOwnership::PAUSE_FILE}\n".freeze
  INACTIVE = "        # - #{GenericDispatcherFluxOwnership::PAUSE_FILE}\n".freeze

  def self.changed_text(text, mode)
    raise 'use pause or restore' unless %w[pause restore].include?(mode)

    record = YAML.safe_load(text)
    spec = record.fetch('spec')
    raise 'unexpected generic dispatcher HelmRelease identity' unless record['kind'] == 'HelmRelease' &&
      record.dig('metadata', 'name') == 'clouddsp-generic-dispatcher' && record.dig('metadata', 'namespace') == 'flux-system' &&
      spec['releaseName'] == 'clouddsp-generic-dispatcher' && spec['targetNamespace'] == 'clouddsp-app' && spec['storageNamespace'] == 'clouddsp-app'
    normal = [GenericDispatcherFluxOwnership::VALUES_FILE]
    paused = normal + [GenericDispatcherFluxOwnership::PAUSE_FILE]
    raise 'unexpected dispatcher values overrides' unless [normal, paused].include?(spec.dig('chart', 'spec', 'valuesFiles')) &&
      spec.fetch('values', {}).empty? && spec.fetch('valuesFrom', []).empty?
    raise 'missing or repeated pause marker' unless text.lines.count { |line| [ACTIVE, INACTIVE].include?(line) } == 1

    mode == 'pause' ? text.sub(INACTIVE, ACTIVE) : text.sub(ACTIVE, INACTIVE)
  end

  def self.run(mode)
    # Stage only in the watched checkout, so a main-branch edit is not mistaken
    # for a live pause. The caller reviews/publishes precisely this manifest.
    branch, status = Open3.capture2('git', '-C', CloudDSPPaths::REPOSITORY_ROOT.to_s, 'branch', '--show-current')
    raise 'use the codex/flux-clouddsp-local checkout' unless status.success? && branch.strip == 'codex/flux-clouddsp-local'

    MANIFEST.write(changed_text(MANIFEST.read, mode))
    puts "Generic dispatcher #{mode} staged in HelmRelease valuesFiles; review, commit, and push this file, then verify reconciliation."
  rescue StandardError => error
    warn "Dispatcher smoke values stopped: #{error.message}"
    exit 1
  end
end

GenericDispatcherSmokeValues.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
