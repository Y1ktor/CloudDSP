#!/usr/bin/env ruby
# Read-only source/live preflight for the local k3d deployment.
#
# Kubernetes objects have two distinct identities here: their stable
# kind/namespace/name and their controller ownership. Helm will refuse to
# install over a kubectl-owned object unless an adoption step is deliberate.
# This program reports those facts and protects the three live data claims;
# it never calls an API that writes to Kubernetes or runs a Helm upgrade.

require 'json'
require 'open3'
require 'pathname'
require 'yaml'

class CloudDSPDeploymentPlan
  CONTEXT = 'k3d-clouddsp-local'.freeze
  CLUSTER_NAME = 'clouddsp-local'.freeze
  NAMESPACES = %w[clouddsp-system clouddsp-data clouddsp-app].freeze
  DURABLE_KINDS = %w[Deployment StatefulSet Service Ingress NetworkPolicy ScaledObject TriggerAuthentication].freeze
  CORE_KINDS = 'deployments,statefulsets,services,ingresses,networkpolicies'.freeze
  KEDA_KINDS = 'scaledobjects,triggerauthentications'.freeze
  ROOT = Pathname.new(File.expand_path('..', __dir__)).freeze
  LOCAL_SECRET_DIRECTORY = ROOT.parent.join('.local').freeze

  def initialize
    @blockers = []
    @warnings = []
    @notes = []
  end

  def run
    puts "CloudDSP local deployment plan (read-only; context #{CONTEXT})"
    check_tools
    manifests = load_manifests
    check_locks_and_images(manifests)
    return finish unless tools_ready?

    check_cluster
    return finish unless @cluster_ready

    live = load_live_resources
    check_helm_release
    check_namespaces
    check_ownership_and_identity(manifests, live) if live
    check_runtime_secrets(manifests)
    check_charts
    finish
  end

  private

  # Every external command is an argv array, so no manifest text, path, or
  # Kubernetes name can be interpreted as shell code. stderr is intentionally
  # not echoed: client errors may include private endpoints or credentials.
  def read_command(*argv)
    stdout, _stderr, status = Open3.capture3(*argv)
    return stdout if status.success?

    @blockers << "Read-only command failed: #{argv.first} #{argv[1]} (exit #{status.exitstatus})."
    nil
  end

  def command_available?(name)
    ENV.fetch('PATH', '').split(File::PATH_SEPARATOR).any? do |directory|
      File.executable?(File.join(directory, name))
    end
  end

  def check_tools
    @missing_tools = %w[docker k3d kubectl helm].reject { |name| command_available?(name) }
    @blockers << "Missing tools: #{@missing_tools.join(', ')}." unless @missing_tools.empty?
    @notes << 'Ruby YAML/JSON parser available.'
  end

  def tools_ready?
    @missing_tools.empty?
  end

  def load_yaml(path)
    YAML.load_file(path.to_s)
  rescue StandardError => error
    @blockers << "Cannot parse #{path.relative_path_from(ROOT)}: #{error.class}."
    nil
  end

  def load_manifests
    # Only long-lived service and scaling resources belong in the ownership
    # comparison. Fixed-name bootstrap Jobs, SQL ConfigMaps, Secret examples,
    # and opt-in test resources have different lifecycle rules and must never
    # be swept into a generic Helm release.
    paths = Dir.glob(ROOT.join('services', '*', '*.yaml').to_s)
    paths += Dir.glob(ROOT.join('helm', 'keda', '*trigger-authentication.yaml').to_s)
    manifests = []
    paths.sort.each do |path|
      begin
        YAML.load_stream(File.read(path)).each do |document|
          next unless document.is_a?(Hash) && DURABLE_KINDS.include?(document['kind'])
          metadata = document['metadata'] || {}
          if metadata['name'].to_s.empty? || metadata['namespace'].to_s.empty?
            @blockers << "Missing kind/name/namespace in #{Pathname.new(path).relative_path_from(ROOT)}."
            next
          end
          manifests << { 'path' => path, 'document' => document }
        end
      rescue StandardError => error
        @blockers << "Cannot parse #{Pathname.new(path).relative_path_from(ROOT)}: #{error.class}."
      end
    end
    @notes << "Versioned long-lived resources: #{manifests.length}."
    manifests
  end

  def workloads(manifests)
    manifests.select { |item| %w[Deployment StatefulSet].include?(item['document']['kind']) }
  end

  def check_locks_and_images(manifests)
    required = [ROOT.join('cluster', 'k3d.yaml'), ROOT.join('cluster', 'namespaces.yaml'),
                ROOT.join('images.lock.yaml'), ROOT.join('helm', 'keda', 'release.lock.yaml'),
                ROOT.join('helm', 'keda', 'values.yaml')]
    required.each { |path| @blockers << "Missing versioned input #{path.relative_path_from(ROOT)}." unless path.file? }
    return unless ROOT.join('images.lock.yaml').file?

    lock = load_yaml(ROOT.join('images.lock.yaml'))
    unless lock.is_a?(Hash) && lock['images'].is_a?(Hash)
      @blockers << 'images.lock.yaml has no images mapping.'
      return
    end

    locked_references = lock['images'].values.map { |entry| entry.is_a?(Hash) ? entry['immutableReference'] : nil }.compact
    checked = 0
    workloads(manifests).each do |item|
      document = item['document']
      name = document.dig('metadata', 'name')
      containers = document.dig('spec', 'template', 'spec', 'containers') || []
      containers.each do |container|
        reference = container['image'].to_s
        checked += 1
        unless reference.match?(/@sha256:[0-9a-f]{64}\z/)
          @blockers << "#{name} container #{container['name']} does not use an immutable sha256 image reference."
          next
        end
        unless locked_references.include?(reference)
          @blockers << "#{name} image digest is absent from images.lock.yaml (#{reference})."
        end
      end
    end
    @notes << "Digest-pinned workload containers checked against images.lock.yaml: #{checked}."
  end

  def check_cluster
    docker = read_command('docker', 'info', '--format', '{{.ServerVersion}}')
    return unless docker

    clusters = read_command('k3d', 'cluster', 'list', CLUSTER_NAME, '--no-headers')
    return unless clusters
    unless clusters.lines.any? { |line| line.split.first == CLUSTER_NAME }
      @blockers << "k3d cluster #{CLUSTER_NAME} is absent."
      return
    end

    contexts = read_command('kubectl', 'config', 'get-contexts', '-o', 'name')
    return unless contexts
    unless contexts.lines.map(&:strip).include?(CONTEXT)
      @blockers << "Kubernetes context #{CONTEXT} is absent."
      return
    end

    nodes = read_json('kubectl', '--context', CONTEXT, 'get', 'nodes', '-o', 'json', '--request-timeout=15s')
    return unless nodes
    count = nodes.fetch('items', []).count do |node|
      node.fetch('status', {}).fetch('conditions', []).any? { |condition| condition['type'] == 'Ready' && condition['status'] == 'True' }
    end
    @blockers << "Only #{count} of #{nodes.fetch('items', []).length} k3d nodes are Ready." if count != nodes.fetch('items', []).length || count.zero?
    @notes << "k3d cluster found; Ready nodes: #{count}/#{nodes.fetch('items', []).length}."
    @cluster_ready = true
  end

  def read_json(*argv)
    output = read_command(*argv)
    return nil unless output
    JSON.parse(output)
  rescue JSON::ParserError
    @blockers << "Invalid JSON from read-only command #{argv.first} #{argv[1]}."
    nil
  end

  def load_live_resources
    prefix = ['kubectl', '--context', CONTEXT, 'get']
    core = read_json(*(prefix + [CORE_KINDS, '--all-namespaces', '-o', 'json', '--request-timeout=15s']))
    keda = read_json(*(prefix + [KEDA_KINDS, '--all-namespaces', '-o', 'json', '--request-timeout=15s']))
    return nil unless core && keda

    resources = (core.fetch('items', []) + keda.fetch('items', []))
    @notes << "Live long-lived Kubernetes objects scanned: #{resources.length} across all namespaces."
    resources.each_with_object({}) do |object, index|
      metadata = object['metadata'] || {}
      index[[object['kind'], metadata['namespace'], metadata['name']]] = object
    end
  end

  def check_helm_release
    lock = load_yaml(ROOT.join('helm', 'keda', 'release.lock.yaml'))
    unless lock.is_a?(Hash) && lock['chart'].is_a?(Hash)
      @blockers << 'helm/keda/release.lock.yaml has no chart mapping.'
      return
    end
    releases = read_json('helm', '--kube-context', CONTEXT, 'list', '--all-namespaces', '-o', 'json')
    return unless releases.is_a?(Array)
    chart = lock['chart']
    release = releases.find { |item| item['name'] == chart['releaseName'] && item['namespace'] == chart['namespace'] }
    if release.nil?
      @blockers << "Pinned KEDA release #{chart['releaseName']} is absent."
    elsif release['chart'] != "#{chart['name']}-#{chart['version']}" || release['status'] != 'deployed'
      @blockers << "KEDA Helm release differs from the pinned version/status in release.lock.yaml."
    else
      @notes << "KEDA Helm release matches chart #{release['chart']} (#{release['status']})."
    end
    app_releases = releases.reject { |item| %w[keda traefik traefik-crd].include?(item['name']) }
    names = app_releases.map { |item| item['name'] }.sort.join(', ')
    @notes << "Other Helm releases: #{names.empty? ? 'none' : names}."
  end

  def check_namespaces
    namespace_list = read_json('kubectl', '--context', CONTEXT, 'get', 'namespaces', '-o', 'json', '--request-timeout=15s')
    return unless namespace_list
    namespace_index = namespace_list.fetch('items', []).each_with_object({}) do |item, result|
      result[item.dig('metadata', 'name')] = item
    end
    NAMESPACES.each do |name|
      object = namespace_index[name]
      if object.nil?
        @blockers << "Required namespace #{name} is absent."
      elsif object.dig('metadata', 'labels', 'app.kubernetes.io/managed-by') != 'kubectl'
        @blockers << "Namespace #{name} has unexpected managed-by ownership."
      end
    end
    @notes << "Project namespaces present: #{NAMESPACES.count { |name| namespace_index.key?(name) }}/#{NAMESPACES.length}."
  end

  def proposed_release(item)
    document = item['document']
    return 'clouddsp-scaling-auth' if document['kind'] == 'TriggerAuthentication'
    name = document.dig('metadata', 'name')
    return name if %w[clouddsp-dispatcher clouddsp-generic-dispatcher].include?(name)
    component = Pathname.new(item['path']).relative_path_from(ROOT).each_filename.to_a[1]
    { 'api' => 'clouddsp-job-api', 'upload-intake' => 'clouddsp-upload-intake',
      'frontend' => 'clouddsp-frontend', 'keycloak' => 'clouddsp-keycloak',
      'mailpit' => 'clouddsp-mailpit' }.fetch(component, "clouddsp-#{component}")
  end

  def check_ownership_and_identity(manifests, live)
    present = 0
    kubectl_owned = 0
    adoption_by_release = Hash.new { |result, release| result[release] = [] }
    manifests.each do |item|
      expected = item['document']
      metadata = expected['metadata']
      name = metadata['name']
      object = live[[expected['kind'], metadata['namespace'], name]]
      next unless object
      present += 1
      labels = object.dig('metadata', 'labels') || {}
      annotations = object.dig('metadata', 'annotations') || {}
      managed = labels['app.kubernetes.io/managed-by']
      release = annotations['meta.helm.sh/release-name']
      if managed == 'kubectl' && release.nil?
        kubectl_owned += 1
        adoption_by_release[proposed_release(item)] << "#{expected['kind']}/#{name}"
      elsif managed == 'Helm' && release == proposed_release(item)
        # This is a future state once a reviewed adoption has completed.
      else
        @blockers << "Unexpected owner for #{expected['kind']}/#{name} in #{metadata['namespace']}: #{managed || 'unlabeled'} / #{release || 'no Helm release'}."
      end
      check_immutable_fields(expected, object)
      check_live_images(expected, object)
    end
    @notes << "Source long-lived objects present by identity: #{present}/#{manifests.length}; kubectl-owned adoption candidates: #{kubectl_owned}."
    adoption_by_release.sort.each do |release_name, objects|
      @notes << "Pending #{release_name}: #{objects.sort.join(', ')}."
    end
    @warnings << "#{manifests.length - present} source long-lived objects are absent from the cluster." if present < manifests.length
    @notes << 'Chart-specific rendered/live spec comparison is outside this general preflight; use mailpit-release.rb verify for Mailpit.'
    check_pvcs(manifests, live)
  end

  def claim_identity(claim)
    spec = claim['spec'] || {}
    [claim.dig('metadata', 'name'), spec['storageClassName'], spec['accessModes'], spec.dig('resources', 'requests', 'storage')]
  end

  def check_immutable_fields(expected, live)
    kind = expected['kind']
    name = expected.dig('metadata', 'name')
    return unless %w[Deployment StatefulSet].include?(kind)
    if expected.dig('spec', 'selector') != live.dig('spec', 'selector')
      @blockers << "Immutable selector differs for #{kind}/#{name}."
    end
    return unless kind == 'StatefulSet'

    fields_match = expected.dig('spec', 'serviceName') == live.dig('spec', 'serviceName') &&
                   (expected.dig('spec', 'volumeClaimTemplates') || []).map { |claim| claim_identity(claim) } ==
                     (live.dig('spec', 'volumeClaimTemplates') || []).map { |claim| claim_identity(claim) }
    @blockers << "StatefulSet/#{name} headless Service or claim template differs from source." unless fields_match
  end

  def check_live_images(expected, live)
    return unless %w[Deployment StatefulSet].include?(expected['kind'])
    source_images = (expected.dig('spec', 'template', 'spec', 'containers') || []).map { |container| [container['name'], container['image']] }.to_h
    live_images = (live.dig('spec', 'template', 'spec', 'containers') || []).map { |container| [container['name'], container['image']] }.to_h
    @warnings << "Live image differs from source for #{expected['kind']}/#{expected.dig('metadata', 'name')}." unless source_images == live_images
  end

  def check_pvcs(manifests, live)
    claims = read_json('kubectl', '--context', CONTEXT, 'get', 'persistentvolumeclaims',
                       '--namespace', 'clouddsp-data', '-o', 'json', '--request-timeout=15s')
    return unless claims
    by_name = claims.fetch('items', []).each_with_object({}) { |claim, index| index[claim.dig('metadata', 'name')] = claim }
    manifests.each do |item|
      expected = item['document']
      next unless expected['kind'] == 'StatefulSet'
      next unless live.key?(['StatefulSet', expected.dig('metadata', 'namespace'), expected.dig('metadata', 'name')])
      (expected.dig('spec', 'volumeClaimTemplates') || []).each do |template|
        name = "#{template.dig('metadata', 'name')}-#{expected.dig('metadata', 'name')}-0"
        pvc = by_name[name]
        if pvc.nil? || pvc.dig('status', 'phase') != 'Bound' || pvc.dig('spec', 'volumeName').to_s.empty?
          @blockers << "StatefulSet/#{expected.dig('metadata', 'name')} claim #{name} is not bound to a PV."
        elsif pvc.dig('spec', 'storageClassName') != template.dig('spec', 'storageClassName')
          @blockers << "StatefulSet/#{expected.dig('metadata', 'name')} claim #{name} uses a different storage class."
        end
      end
    end
    @notes << "Data PVCs inspected: #{by_name.length}."
  end

  def collect_secret_names(value, names)
    case value
    when Hash
      %w[secretKeyRef secretRef].each do |key|
        next unless value[key].is_a?(Hash)
        name = value[key]['name']
        names << name if name.is_a?(String)
      end
      names << value['secretName'] if value['secretName'].is_a?(String)
      if value['secretTargetRef'].is_a?(Array)
        value['secretTargetRef'].each { |entry| names << entry['name'] if entry.is_a?(Hash) && entry['name'].is_a?(String) }
      end
      value.each_value { |child| collect_secret_names(child, names) }
    when Array
      value.each { |child| collect_secret_names(child, names) }
    end
  end

  def check_runtime_secrets(manifests)
    # The Kubernetes Secret list is projected to namespace/name pairs by
    # kubectl's JSONPath output. No Secret `data`/`stringData` field is printed,
    # saved, or parsed by this program.
    output = read_command('kubectl', '--context', CONTEXT, 'get', 'secrets', '--all-namespaces',
                          '-o=jsonpath={range .items[*]}{.metadata.namespace}{"\\t"}{.metadata.name}{"\\n"}{end}',
                          '--request-timeout=15s')
    return unless output
    live_names = output.lines.map { |line| line.strip.split("\t", 2) }.select { |pair| pair.length == 2 }
    examples = Dir.glob(ROOT.join('**', '*.secret.example.yaml').to_s).reject { |path| path.include?('/node_modules/') }.map do |path|
      metadata = load_yaml(Pathname.new(path))
      metadata.is_a?(Hash) ? [metadata.dig('metadata', 'namespace'), metadata.dig('metadata', 'name')] : nil
    end.compact
    required = []
    manifests.each do |item|
      document = item['document']
      next unless %w[Deployment StatefulSet TriggerAuthentication].include?(document['kind'])
      names = []
      collect_secret_names(document['spec'], names)
      names.each { |name| required << [document.dig('metadata', 'namespace'), name] }
    end
    required.uniq.sort.each do |namespace, name|
      @blockers << "Required runtime Secret #{namespace}/#{name} is absent." unless live_names.include?([namespace, name])
      local_name = name.sub(/\Aclouddsp-/, '') + '.secret.yaml'
      @blockers << "Ignored local Secret file is absent: .local/#{local_name}." unless LOCAL_SECRET_DIRECTORY.join(local_name).file?
      @warnings << "No committed example contract for runtime Secret #{namespace}/#{name}." unless examples.include?([namespace, name])
    end
    @notes << "Referenced runtime Secret identities checked: #{required.uniq.length} (values not read or printed)."
  end

  def check_charts
    charts = Dir.glob(ROOT.join('helm', '**', 'Chart.yaml').to_s)
    @notes << "CloudDSP application/data Helm charts found: #{charts.length}; this general preflight does not render them."
  end

  def finish
    puts
    @notes.each { |message| puts "[INFO] #{message}" }
    @warnings.uniq.each { |message| puts "[WARN] #{message}" }
    @blockers.uniq.each { |message| puts "[BLOCK] #{message}" }
    puts
    puts "Result: #{@blockers.empty? ? 'preflight complete' : "#{@blockers.uniq.length} blocker(s)"}; no cluster resources changed."
    @blockers.empty? ? 0 : 1
  end
end

exit CloudDSPDeploymentPlan.new.run
