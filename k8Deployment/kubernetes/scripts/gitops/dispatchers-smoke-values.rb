#!/usr/bin/env ruby
# Stage both reviewed publishers' maintenance values in their Git manifests.
# Validate every selected file before writing either one; never mutate cluster
# resources or publish Git here. Commit/push and read-only readiness gates are
# explicit steps so a failed smoke cannot blindly resume orphaned test work.
require_relative '../lib/paths'
require 'open3'
require 'yaml'

module DispatcherSmokeValues
  CONFIG = %w[dispatcher generic-dispatcher].to_h do |component|
    [component.freeze, {
      release: "clouddsp-#{component}".freeze,
      manifest: CloudDSPPaths::KUBERNETES_ROOT.join("gitops/clusters/clouddsp-local/#{component}/helmrelease.yaml").freeze,
      normal: "./k8Deployment/kubernetes/helm/#{component}/values.yaml".freeze,
      pause: "./k8Deployment/kubernetes/helm/#{component}/values.smoke-pause.yaml".freeze
    }.freeze]
  end.freeze

  def self.active_line(component)
    "        - #{CONFIG.fetch(component).fetch(:pause)}\n"
  end

  def self.inactive_line(component)
    "        # - #{CONFIG.fetch(component).fetch(:pause)}\n"
  end

  def self.changed_text(text, mode, component:)
    raise 'use pause or restore' unless %w[pause restore].include?(mode)

    config = CONFIG.fetch(component)
    record = YAML.safe_load(text)
    spec = record.fetch('spec')
    raise 'unexpected dispatcher HelmRelease identity' unless record['kind'] == 'HelmRelease' &&
      record.dig('metadata', 'name') == config.fetch(:release) && record.dig('metadata', 'namespace') == 'flux-system' &&
      spec['releaseName'] == config.fetch(:release) && spec['targetNamespace'] == 'clouddsp-app' && spec['storageNamespace'] == 'clouddsp-app'
    normal = [config.fetch(:normal)]
    paused = normal + [config.fetch(:pause)]
    raise 'unexpected dispatcher values overrides' unless [normal, paused].include?(spec.dig('chart', 'spec', 'valuesFiles')) &&
      spec.fetch('values', {}).empty? && spec.fetch('valuesFrom', []).empty?
    active = active_line(component)
    inactive = inactive_line(component)
    raise 'missing or repeated pause marker' unless text.lines.count { |line| [active, inactive].include?(line) } == 1

    mode == 'pause' ? text.sub(inactive, active) : text.sub(active, inactive)
  end

  def self.prepared_changes(texts, mode)
    raise 'no reviewed dispatcher selected' if texts.empty?

    # Complete the validation map before callers can persist any changed text.
    texts.to_h { |component, text| [component, changed_text(text, mode, component: component)] }
  end

  def self.run(mode, components: CONFIG.keys)
    branch, status = Open3.capture2('git', '-C', CloudDSPPaths::REPOSITORY_ROOT.to_s, 'branch', '--show-current')
    raise 'use the codex/flux-clouddsp-local checkout' unless status.success? && branch.strip == 'codex/flux-clouddsp-local'

    texts = components.to_h { |component| [component, CONFIG.fetch(component).fetch(:manifest).read] }
    prepared_changes(texts, mode).each { |component, text| CONFIG.fetch(component).fetch(:manifest).write(text) }
    puts "Dispatcher #{mode} staged for #{components.join(', ')}; review, commit, push the HelmRelease files, then verify reconciliation."
  rescue StandardError => error
    warn "Dispatcher smoke values stopped: #{error.message}"
    exit 1
  end
end

DispatcherSmokeValues.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
