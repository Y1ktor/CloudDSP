#!/usr/bin/env ruby
# Stage both reviewed publishers' maintenance values in their Git manifests.
# Validate every selected file before writing either one; never mutate cluster
# resources or publish Git here. Commit/push and read-only readiness gates are
# explicit steps so a failed smoke cannot blindly resume orphaned test work.
require_relative '../lib/paths'
require 'open3'
require 'yaml'

module DispatcherSmokeValues
  SOURCE_MANIFEST = 'k8Deployment/kubernetes/gitops/clusters/clouddsp-local/flux-system/gotk-sync.yaml'.freeze
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

  def self.watched_branch
    root = CloudDSPPaths::REPOSITORY_ROOT.to_s
    source, _, status = Open3.capture3('git', '-C', root, 'show', "HEAD:#{SOURCE_MANIFEST}")
    raise 'commit the Flux Git source configuration before staging dispatcher values' unless status.success?

    # Read HEAD, then require both the index and worktree to match it. A local
    # branch edit must not bypass the guard while Flux still watches another
    # branch. The source must be published/reconciled separately before a smoke.
    [['--cached'], []].each do |target|
      _, _, status = Open3.capture3('git', '-C', root, 'diff', '--quiet', *target, 'HEAD', '--', SOURCE_MANIFEST)
      raise 'commit or discard Flux Git source changes before staging dispatcher values' unless status.success?
    end

    # gotk-sync.yaml contains several documents. Wrap each AST document in a
    # stream for safe_load (including macOS Ruby 2.6), without relying on order
    # or enabling arbitrary Ruby objects or YAML aliases.
    records = YAML.parse_stream(source).children.map do |document|
      stream = Psych::Nodes::Stream.new
      stream.children << document
      YAML.safe_load(stream.to_yaml)
    end
    sources = records.select do |record|
      record.is_a?(Hash) && record['kind'] == 'GitRepository' &&
        record.dig('metadata', 'name') == 'flux-system' && record.dig('metadata', 'namespace') == 'flux-system'
    end
    raise 'expected exactly one committed flux-system GitRepository' unless sources.length == 1

    ref = sources.first.dig('spec', 'ref')
    # Flux can give tag/semver/name/commit precedence over branch. Such a
    # source cannot safely receive this helper's commit-and-push workflow.
    raise 'Flux Git source must select only a nonempty spec.ref.branch for this smoke' unless
      ref.is_a?(Hash) && ref.keys == ['branch'] && ref['branch'].is_a?(String) && !ref['branch'].strip.empty?

    ref.fetch('branch')
  end

  def self.run(mode, components: CONFIG.keys)
    expected_branch = watched_branch
    branch, status = Open3.capture2('git', '-C', CloudDSPPaths::REPOSITORY_ROOT.to_s, 'branch', '--show-current')
    raise 'use a named checkout of the Flux watched branch; detached HEAD is not supported' unless status.success? && !branch.strip.empty?
    raise "use the Flux watched branch #{expected_branch.inspect}; current branch is #{branch.strip.inspect}" unless branch.strip == expected_branch

    texts = components.to_h { |component| [component, CONFIG.fetch(component).fetch(:manifest).read] }
    prepared_changes(texts, mode).each { |component, text| CONFIG.fetch(component).fetch(:manifest).write(text) }
    puts "Dispatcher #{mode} staged for #{components.join(', ')}; review, commit, push the HelmRelease files to #{expected_branch.inspect}, then verify reconciliation."
  rescue StandardError => error
    warn "Dispatcher smoke values stopped: #{error.message}"
    exit 1
  end
end

DispatcherSmokeValues.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
