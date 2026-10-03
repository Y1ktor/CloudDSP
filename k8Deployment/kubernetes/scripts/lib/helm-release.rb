# Shared guardrails for adopting one existing one-Pod component at a time.
# A thin component script supplies exact object names, a reviewed image lock,
# and an optional HTTP health route. A StatefulSet caller also supplies a
# bound-PVC identity check and a tested backup gate before one-time takeover.
# Long-lived workload mutation is either `adopt`, gated by source/render/live
# spec equality and original ownership, or an explicitly enabled `install`
# that requires every intended object and the release to be absent.
# Normal `verify` never takes ownership; a component-specific `smoke` may run
# only a versioned disposable Job. This helper does not create namespaces,
# change other releases, or read any Kubernetes Secret contents.

require_relative 'paths'
require 'json'
require 'net/http'
require 'open3'
require 'pathname'
require 'yaml'

class HelmRelease
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  REPOSITORY_ROOT = CloudDSPPaths::REPOSITORY_ROOT
  CONTEXT = 'k3d-clouddsp-local'
  IDLE_POD_DRAIN_TIMEOUT_SECONDS = 60
  IDLE_POD_RECHECK_INTERVAL_SECONDS = 1

  def initialize(component:, namespace:, release:, source_files:, resources:, pod_selector:, health_host: nil, health_path: nil,
                 source_directory: component, image_lock_key: component, verify_running_digest: false,
                 health_status: '200', additional_http_checks: [], smoke_job: nil, browser_shell: false,
                 workload_kind: 'Deployment', pvc_name: nil, before_adopt: nil,
                 expected_replicas: 1, smoke_timeout_seconds: 150,
                 allow_fresh_install: false, before_install: nil,
                 fresh_install_timeout: '3m')
    @component = component
    @namespace = namespace
    @release = release
    @source_files = source_files
    @source_directory = source_directory
    @resources = resources
    @pod_selector = pod_selector
    @health_host = health_host
    @http_checks = [[health_path, health_status], *additional_http_checks]
    @image_lock_key = image_lock_key
    @verify_running_digest = verify_running_digest
    @smoke_job = smoke_job
    @browser_shell = browser_shell
    @workload_kind = workload_kind
    @pvc_name = pvc_name
    @before_adopt = before_adopt
    # A KEDA worker is healthy at zero. The scaler/HPA own /scale, so the
    # Deployment manifest deliberately omits spec.replicas.
    @expected_replicas = expected_replicas
    @smoke_timeout_seconds = smoke_timeout_seconds
    @allow_fresh_install = allow_fresh_install
    @before_install = before_install
    @fresh_install_timeout = fresh_install_timeout
    @fresh_install_started = false
    @chart = ROOT.join('helm', component)
  end

  def run(mode)
    allowed_modes = %w[plan adopt verify]
    allowed_modes << 'install' if @allow_fresh_install
    allowed_modes << 'smoke' if @smoke_job
    abort usage unless allowed_modes.include?(mode)
    check_tools
    check_chart_and_source

    case mode
    when 'plan'
      check_cluster('kubectl', allow_failed_release: true)
      puts "#{@component} chart matches #{@resources.length} source objects and all live specs; adoption is ready."
    when 'adopt'
      before = check_cluster('kubectl', allow_failed_release: true)
      # A StatefulSet's metadata-only handoff still warrants a tested backup:
      # the referenced PVC contains the authoritative application databases.
      # The component-supplied non-interactive gate runs only after source and
      # live parity have passed and before Helm can mutate ownership.
      if @before_adopt
        puts command(*@before_adopt).strip
        # A protected volume snapshot can deliberately stop and restart its
        # one Pod. Re-read the post-backup baseline so UID preservation below
        # measures the Helm handoff itself, not that earlier controlled pause.
        before = check_cluster('kubectl', allow_failed_release: true)
      end
      # Helm 4's explicit takeover flag is used only for this one-time handoff
      # (or a retry of its failed first revision). Automatic rollback could
      # delete adopted objects, so a failed attempt remains for inspection.
      puts "Adopting the #{@resources.length} existing #{@component} objects into Helm..."
      # kubectl owns the old managed-by label through server-side field
      # management. Force only that pre-reviewed conflict after the exact
      # rendered/source/live comparison above has passed. A failed first
      # revision can be upgraded in place; uninstalling it could delete the
      # original resources once Helm considers them part of the release.
      operation = before.fetch(:release) ? 'upgrade' : 'install'
      output = command('helm', operation, @release, @chart.to_s,
                       '--kube-context', CONTEXT, '--namespace', @namespace,
                       '--take-ownership', '--force-conflicts', '--wait', '--timeout', '3m')
      puts output.lines.grep(/^(NAME|NAMESPACE|STATUS|REVISION):/)
      after = check_cluster('Helm')
      verify_stable_identity(before, after)
      verify_http_route if @health_host
      identity_fields = @expected_replicas.zero? ? 'resource UIDs' : 'resource and Pod UIDs'
      identity_fields += ' and Service IPs' if @resources.any? { |resource| resource.start_with?('service/') }
      identity_fields += ' and PVC identity' if @pvc_name
      puts "#{@component} adopted: release deployed; #{identity_fields} unchanged; #{runtime_detail}."
    when 'install'
      # This branch never takes ownership of a resource from kubectl or a
      # failed earlier release. Fresh creation and adoption have different
      # recovery rules, so their preconditions must stay separate.
      check_fresh_install_boundary
      # StatefulSet Secrets are installed outside Helm. Each component's
      # read-only prerequisite gate must pass before the first Helm write;
      # MinIO has both root and AMQP Secrets, while earlier callers have one.
      # Preserve the one-command form for those callers and accept an ordered
      # list of command arrays when a component has several prerequisites.
      if @before_install
        prerequisites = @before_install.first.is_a?(Array) ? @before_install : [@before_install]
        prerequisites.each { |arguments| command(*arguments) }
      end
      puts "Installing fresh #{@component} Helm release..."
      @fresh_install_started = true
      output = command('helm', 'install', @release, @chart.to_s,
                       '--kube-context', CONTEXT, '--namespace', @namespace,
                       '--wait', '--timeout', @fresh_install_timeout)
      puts output.lines.grep(/^(NAME|NAMESPACE|STATUS|REVISION):/)
      check_cluster('Helm')
      verify_http_route if @health_host
      puts "#{@component} fresh install: release deployed; #{runtime_detail} verified."
    when 'verify'
      check_cluster('Helm')
      verify_http_route if @health_host
      readiness = @expected_replicas.zero? ? 'zero idle replicas' : 'Pod readiness'
      puts "#{@component} Helm ownership, source spec, #{readiness}, and #{runtime_detail} verified."
    when 'smoke'
      check_cluster('Helm')
      run_smoke_job
    end
  rescue StandardError => error
    warn "#{@component} #{mode} stopped: #{error.message}"
    warn "If install started, inspect the release and #{@resources.length} live objects before any retry; do not uninstall an adopted release to recover." if mode == 'adopt'
    warn "Inspect the Helm release and live objects before retrying." if mode == 'install' && @fresh_install_started
    exit 1
  end

  private

  def usage
    modes = %w[plan adopt verify]
    modes << 'install' if @allow_fresh_install
    modes << 'smoke' if @smoke_job
    "Usage: #{CloudDSPPaths.repository_script("#{@component}-release.rb")} #{modes.join('|')}"
  end

  def runtime_detail
    if @health_host
      route = if @http_checks.any? { |_path, status| status != '200' }
                'protected browser routes'
              else
                @browser_shell ? 'browser route and static assets' : 'browser route'
              end
      return @verify_running_digest ? "running image digest and #{route}" : route
    end

    # Stateful data services are checked through their Ready Pod and the
    # generated bound claim, even when they have no browser-facing route.
    if @pvc_name
      detail = 'StatefulSet Pod and bound PVC'
      return @verify_running_digest ? "#{detail} and running image digest" : detail
    end
    # Internal publishers have no HTTP listener; their Ready Pod and running
    # image are the observable deployment-path checks here.
    return 'KEDA idle worker identity' if @expected_replicas.zero?

    @verify_running_digest ? 'running image digest' : 'internal Pod identity'
  end

  def ensure_true(condition, message)
    raise message unless condition
  end

  def command(*args, stdin_data: nil)
    output, error, status = Open3.capture3(*args, stdin_data: stdin_data, chdir: REPOSITORY_ROOT)
    raise "#{args.first} failed: #{error.strip.empty? ? output.strip : error.strip}" unless status.success?
    output
  end

  def kubectl(*args)
    command('kubectl', '--context', CONTEXT, '--namespace', @namespace, *args)
  end

  def helm(*args)
    command('helm', '--kube-context', CONTEXT, *args)
  end

  def check_tools
    %w[helm kubectl].each do |tool|
      ensure_true(ENV.fetch('PATH', '').split(File::PATH_SEPARATOR).any? { |dir| File.executable?(File.join(dir, tool)) },
                  "#{tool} is required")
    end
    contexts = command('kubectl', 'config', 'get-contexts', '--output=name').lines.map(&:strip)
    ensure_true(contexts.include?(CONTEXT), "Kubernetes context #{CONTEXT} is unavailable")
  end

  def documents(yaml)
    YAML.load_stream(yaml).compact
  end

  def identity(object)
    [object.fetch('kind'), object.fetch('metadata').fetch('namespace'), object.fetch('metadata').fetch('name')]
  end

  def indexed(documents)
    result = documents.to_h { |object| [identity(object), object] }
    ensure_true(result.length == documents.length, "duplicate #{@component} resource identity")
    result
  end

  def first_difference(expected, actual, path = '')
    if expected.is_a?(Hash) && actual.is_a?(Hash)
      key = (expected.keys | actual.keys).find { |candidate| expected.key?(candidate) != actual.key?(candidate) }
      return "#{path}/#{key}" if key
      expected.each_key do |candidate|
        difference = first_difference(expected[candidate], actual[candidate], "#{path}/#{candidate}")
        return difference if difference
      end
      nil
    elsif expected.is_a?(Array) && actual.is_a?(Array)
      return "#{path}/length" unless expected.length == actual.length
      expected.each_index do |index|
        difference = first_difference(expected[index], actual[index], "#{path}/#{index}")
        return difference if difference
      end
      nil
    else
      expected == actual ? nil : path
    end
  end

  def check_chart_and_source
    # A chart's release name need not match its source directory: both
    # dispatchers share services/dispatcher/, while Job API lives in
    # services/job-api/. Keep those original manifests as adoption baselines.
    source = indexed(@source_files.flat_map do |name|
      documents(ROOT.join('services', @source_directory, name).read)
    end)
    ensure_true(source.length == @resources.length, "expected #{@resources.length} source #{@component} objects")
    lock = YAML.load_file(ROOT.join('images.lock.yaml'))
    values = YAML.load_file(@chart.join('values.yaml'))
    locked_image = lock.fetch('images').fetch(@image_lock_key).fetch('immutableReference')
    ensure_true(values.fetch('image').fetch('reference') == locked_image, "#{@component} chart image differs from images.lock.yaml")
    @locked_image = locked_image

    command('helm', 'lint', @chart.to_s, '--strict')
    rendered_yaml = command('helm', 'template', @release, @chart.to_s, '--namespace', @namespace)
    rendered = indexed(documents(rendered_yaml))
    ensure_true(rendered.keys.sort == source.keys.sort, "chart must render exactly the #{@resources.length} source #{@component} identities")
    source.each do |id, object|
      expected = Marshal.load(Marshal.dump(object))
      expected.fetch('metadata').fetch('labels')['app.kubernetes.io/managed-by'] = 'Helm'
      difference = first_difference(expected, rendered.fetch(id))
      ensure_true(difference.nil?, "rendered #{id.first}/#{id.last} differs from source at #{difference}")
    end
    # API-server dry run validates the chart against this cluster's schemas
    # without persisting the Helm ownership change or touching the Pod.
    command('kubectl', '--context', CONTEXT, '--namespace', @namespace,
            'apply', '--dry-run=server', '--filename', '-', stdin_data: rendered_yaml)
    @source = source
    @rendered = rendered
    @chart_identity = "#{@component}-#{YAML.load_file(@chart.join('Chart.yaml')).fetch('version')}"
  end

  def release_record
    # Helm 4 lists every release state by default in the selected namespace.
    # A failed or pending attempt must still block a second takeover install.
    releases = JSON.parse(helm('list', '--namespace', @namespace, '--output', 'json'))
    releases.find { |release| release['name'] == @release }
  end

  def check_fresh_install_boundary
    # A single-Pod Deployment has no generated data. A single-Pod StatefulSet
    # must also prove its generated claim and any orphan Pod are absent before
    # Helm can create storage. PVC data is never reused by an implicit install.
    stateless = @workload_kind == 'Deployment' && @pvc_name.nil?
    stateful = @workload_kind == 'StatefulSet' && !@pvc_name.nil?
    fresh_deployment = @expected_replicas == 1 ||
                       (@expected_replicas.zero? && @source.any? { |(kind, _namespace, _name), _object| kind == 'ScaledObject' })
    ensure_true(fresh_deployment && (stateless || stateful),
                "fresh #{@component} install requires a one-Pod Deployment, an idle KEDA worker, or guarded StatefulSet")
    ensure_true(release_record.nil?, "#{@component} Helm release already exists; use verify or inspect it")
    @resources.each do |resource|
      # Explicit names plus --ignore-not-found distinguish an empty namespace
      # from a partial prior attempt. Never pass --take-ownership on install.
      present = kubectl('get', resource, '--ignore-not-found', '--output', 'name').strip
      ensure_true(present.empty?, "#{@component} #{resource} already exists; inspect it before install")
    end
    return unless stateful

    claim = @source.fetch(['StatefulSet', @namespace, @release]).fetch('spec').fetch('volumeClaimTemplates').fetch(0)
    expected_claim = "#{claim.fetch('metadata').fetch('name')}-#{@release}-0"
    ensure_true(@pvc_name == expected_claim, "#{@component} generated PVC name differs from reviewed claim template")
    present_claim = kubectl('get', "pvc/#{@pvc_name}", '--ignore-not-found', '--output', 'name').strip
    ensure_true(present_claim.empty?, "#{@component} PVC #{@pvc_name} already exists; inspect its data before install")
    pods = JSON.parse(kubectl('get', 'pods', '--selector', @pod_selector, '--output', 'json')).fetch('items')
    ensure_true(pods.empty?, "#{@component} Pod already exists without its StatefulSet; inspect it before install")
  end

  def live_objects
    response = JSON.parse(kubectl('get', *@resources, '--output', 'json'))
    # kubectl returns one bare object for a single explicit resource, whereas
    # it wraps several resources in a List. The dispatcher chart owns only one
    # Deployment; normalize both shapes before comparing resource identities.
    objects = response['kind'] == 'List' ? response.fetch('items') : [response]
    indexed(objects)
  end

  def live_pod
    list = JSON.parse(kubectl('get', 'pods', '--selector', @pod_selector, '--output', 'json'))
    pods = list.fetch('items')
    ensure_true(pods.length == 1, "expected one #{@component} Pod, found #{pods.length}")
    pod = pods.first
    ensure_true(pod.fetch('status').fetch('conditions', []).any? { |condition| condition['type'] == 'Ready' && condition['status'] == 'True' },
                "#{@component} Pod is not Ready")
    if @verify_running_digest
      # The dispatcher lock points to a single-platform local registry image,
      # so its running imageID should contain the same digest as the manifest.
      # This would be unsuitable for a multi-platform image-index digest,
      # whose selected child image can have a different digest.
      digest = @locked_image.split('@', 2).last
      statuses = pod.fetch('status').fetch('containerStatuses', [])
      ensure_true(statuses.length == 1 && statuses.first.fetch('imageID', '').include?(digest),
                  "#{@component} running Pod imageID does not match its locked digest")
    end
    pod
  end

  def normalized_live_spec(object, expected_spec)
    spec = Marshal.load(Marshal.dump(object.fetch('spec')))
    case object.fetch('kind')
    when 'Deployment', 'StatefulSet'
      # Kubernetes defaults this field to 600 only when the source omits it.
      # Preserve and compare any value explicitly declared by the source.
      spec.delete('progressDeadlineSeconds') unless expected_spec.key?('progressDeadlineSeconds')
      # HPA/KEDA write the Deployment scale subresource. Its current replica
      # count is runtime state, not chart drift, when source omits replicas.
      if object.fetch('kind') == 'Deployment'
        spec.delete('replicas') unless expected_spec.key?('replicas')
        spec.delete('revisionHistoryLimit') unless expected_spec.key?('revisionHistoryLimit')
      end
      if object.fetch('kind') == 'StatefulSet'
        spec.delete('revisionHistoryLimit') unless expected_spec.key?('revisionHistoryLimit')
        spec.delete('ordinals') unless expected_spec.key?('ordinals')
        strategy = spec.fetch('updateStrategy')
        declared_strategy = expected_spec.fetch('updateStrategy')
        strategy.delete('rollingUpdate') unless declared_strategy.key?('rollingUpdate')
        # A previous kubectl rollout left its restart timestamp on the live
        # Pod template. It is an operational marker, not chart configuration.
        # Ignore only this key for source parity; the takeover must still
        # preserve the Pod UID, and any other template annotation remains a
        # real drift that blocks adoption.
        annotations = spec.fetch('template').fetch('metadata').fetch('annotations', {})
        declared_annotations = expected_spec.fetch('template').fetch('metadata').fetch('annotations', {})
        annotations.delete('kubectl.kubernetes.io/restartedAt') unless declared_annotations.key?('kubectl.kubernetes.io/restartedAt')
        spec.fetch('template').fetch('metadata').delete('annotations') if annotations.empty?
        # The API server embeds each PVC template as a fully defaulted PVC
        # object. Compare only fields declared by the versioned claim template;
        # its name, labels, class, access mode, and requested size still must
        # match exactly, and the bound live PVC is checked separately below.
        spec.fetch('volumeClaimTemplates').each_with_index do |claim, index|
          declared_claim = expected_spec.fetch('volumeClaimTemplates').fetch(index)
          %w[apiVersion kind status].each { |field| claim.delete(field) unless declared_claim.key?(field) }
          claim.fetch('spec').delete('volumeMode') unless declared_claim.fetch('spec').key?('volumeMode')
        end
      end
      pod = spec.fetch('template').fetch('spec')
      %w[dnsPolicy restartPolicy schedulerName].each { |key| pod.delete(key) }
      declared_containers = expected_spec.fetch('template').fetch('spec').fetch('containers')
      pod.fetch('containers').each_with_index do |container, index|
        declared = declared_containers.fetch(index)
        %w[terminationMessagePath terminationMessagePolicy].each { |key| container.delete(key) }
        %w[startupProbe readinessProbe livenessProbe].each do |probe|
          next unless declared.key?(probe)

          actual_probe = container.fetch(probe)
          declared_probe = declared.fetch(probe)
          actual_probe.delete('successThreshold') unless declared_probe.key?('successThreshold')
          # The API server adds HTTP to an omitted probe scheme. Some source
          # manifests declare it explicitly, while others rely on that default.
          if declared_probe.key?('httpGet')
            actual_get = actual_probe.fetch('httpGet')
            declared_get = declared_probe.fetch('httpGet')
            actual_get.delete('scheme') unless declared_get.key?('scheme')
          end
        end
      end
    when 'Service'
      %w[type clusterIP clusterIPs internalTrafficPolicy ipFamilies ipFamilyPolicy sessionAffinity].each do |key|
        spec.delete(key) unless expected_spec.key?(key)
      end
    when 'ScaledObject'
      # KEDA's webhook stores an empty scalingModifiers block when the source
      # has no composite metric. Keep every declared trigger and scale policy
      # under strict comparison; ignore only that server-added empty block.
      advanced = spec.fetch('advanced', {})
      advanced.delete('scalingModifiers') if !expected_spec.fetch('advanced', {}).key?('scalingModifiers') &&
                                             advanced['scalingModifiers'] == {}
    end
    spec
  end

  def check_cluster(owner, allow_failed_release: false)
    record = release_record
    if owner == 'Helm'
      ensure_true(record && record['status'] == 'deployed' && record['chart'] == @chart_identity,
                  "expected #{@component} Helm chart/release is not deployed")
      # A Ready object with Helm labels alone does not prove the release stores
      # this reviewed chart. Compare the release's actual manifest as well.
      installed = indexed(documents(helm('get', 'manifest', @release, '--namespace', @namespace)))
      ensure_true(installed == @rendered, "installed #{@component} release manifest differs from the reviewed chart")
    else
      retryable = allow_failed_release && record && record['status'] == 'failed' && record['chart'] == @chart_identity
      ensure_true(record.nil? || retryable, "#{@component} Helm release already exists in a non-retryable state; inspect it before take-ownership")
    end
    objects = live_objects
    ensure_true(objects.keys.sort == @source.keys.sort, "live #{@component} resource identities differ from chart")
    @source.each do |id, expected|
      object = objects.fetch(id)
      metadata = object.fetch('metadata')
      ensure_true(metadata.fetch('ownerReferences', []).empty?, "#{id.first}/#{id.last} has an unexpected controller owner")
      labels = expected.fetch('metadata').fetch('labels').merge('app.kubernetes.io/managed-by' => owner)
      live_labels = metadata.fetch('labels').dup
      if id.first == 'ScaledObject'
        # KEDA itself stamps this name label on its CR. It is generated from
        # metadata.name and is not part of the source or Helm template.
        ensure_true(live_labels.delete('scaledobject.keda.sh/name') == id.last,
                    "#{id.first}/#{id.last} KEDA name label changed")
      end
      ensure_true(live_labels == labels, "#{id.first}/#{id.last} labels differ from expected #{owner} ownership")
      annotations = metadata.fetch('annotations', {})
      expected.fetch('metadata').fetch('annotations', {}).each do |key, value|
        ensure_true(annotations[key] == value, "#{id.first}/#{id.last} annotation #{key} differs")
      end
      if owner == 'Helm'
        ensure_true(annotations['meta.helm.sh/release-name'] == @release && annotations['meta.helm.sh/release-namespace'] == @namespace,
                    "#{id.first}/#{id.last} has unexpected Helm release annotations")
      else
        ensure_true(!annotations.key?('meta.helm.sh/release-name') && !annotations.key?('meta.helm.sh/release-namespace'),
                    "#{id.first}/#{id.last} is already claimed by Helm")
      end
      difference = first_difference(expected.fetch('spec'), normalized_live_spec(object, expected.fetch('spec')))
      ensure_true(difference.nil?, "live #{id.first}/#{id.last} spec differs from source at #{difference}")
    end
    workload = objects.fetch([@workload_kind, @namespace, @release])
    ensure_true(workload.fetch('spec').fetch('replicas') == @expected_replicas,
                "#{@component} #{@workload_kind} desired replicas changed")
    ensure_true(workload.dig('status', 'readyReplicas').to_i == @expected_replicas,
                "#{@component} #{@workload_kind} ready replicas changed")
    ensure_true(workload.dig('status', 'replicas').to_i == @expected_replicas,
                "#{@component} #{@workload_kind} observed replicas changed")
    pvc = live_pvc if @pvc_name
    pod_uid = if @expected_replicas.zero?
                wait_for_idle_pods
                nil
              else
                live_pod.fetch('metadata').fetch('uid')
              end
    { objects: objects, pod_uid: pod_uid, pvc: pvc, release: record }
  end

  def wait_for_idle_pods
    deadline = monotonic_time + IDLE_POD_DRAIN_TIMEOUT_SECONDS
    loop do
      pods = JSON.parse(kubectl('get', 'pods', '--selector', @pod_selector, '--output', 'json')).fetch('items')
      # The Deployment's desired and observed replica counts were checked
      # above. A Pod with deletionTimestamp is already being drained by its
      # controller; long-running workers deliberately keep a grace period
      # longer than this readiness check so an active task can finish safely.
      # Wait only for Pods that have not entered termination. Requiring the
      # API object to disappear would make a successful KEDA scale-to-zero
      # fail during Demucs's 780-second (or another worker's) drain window.
      active_pods = pods.select { |pod| pod.fetch('metadata').fetch('deletionTimestamp', nil).to_s.empty? }
      return if active_pods.empty?

      remaining = deadline - monotonic_time
      ensure_true(remaining.positive?,
                  "expected no non-terminating #{@component} Pods, found #{active_pods.length} after scale-to-zero")
      sleep([remaining, IDLE_POD_RECHECK_INTERVAL_SECONDS].min)
    end
  end

  def monotonic_time
    Process.clock_gettime(Process::CLOCK_MONOTONIC)
  end

  def live_pvc
    # A generated StatefulSet claim is durable data, not part of the Helm
    # manifest. Check its binding and declared storage contract separately.
    object = JSON.parse(kubectl('get', "pvc/#{@pvc_name}", '--output', 'json'))
    statefulset = @source.fetch(['StatefulSet', @namespace, @release])
    claim = statefulset.fetch('spec').fetch('volumeClaimTemplates').fetch(0)
    expected_name = "#{claim.fetch('metadata').fetch('name')}-#{@release}-0"
    ensure_true(@pvc_name == expected_name && object.dig('metadata', 'name') == expected_name,
                "#{@component} generated PVC name differs from the claim template")
    ensure_true(object.dig('status', 'phase') == 'Bound', "#{@component} PVC is not Bound")
    expected = claim.fetch('spec')
    %w[storageClassName accessModes].each do |field|
      ensure_true(object.fetch('spec')[field] == expected[field], "#{@component} PVC #{field} changed")
    end
    ensure_true(object.dig('spec', 'resources', 'requests', 'storage') == expected.dig('resources', 'requests', 'storage'),
                "#{@component} PVC requested storage changed")
    ensure_true(!object.dig('spec', 'volumeName').to_s.empty?, "#{@component} PVC has no bound PV")
    object
  end

  def verify_stable_identity(before, after)
    before.fetch(:objects).each do |id, object|
      current = after.fetch(:objects).fetch(id)
      ensure_true(object.dig('metadata', 'uid') == current.dig('metadata', 'uid'), "#{id.first}/#{id.last} was replaced")
      next unless id.first == 'Service'
      ensure_true(object.dig('spec', 'clusterIP') == current.dig('spec', 'clusterIP'), "#{id.last} ClusterIP changed")
    end
    ensure_true(before.fetch(:pod_uid) == after.fetch(:pod_uid), "#{@component} Pod was replaced during metadata-only adoption")
    if @pvc_name
      old_claim = before.fetch(:pvc)
      new_claim = after.fetch(:pvc)
      %w[uid name].each do |field|
        ensure_true(old_claim.dig('metadata', field) == new_claim.dig('metadata', field), "#{@component} PVC #{field} changed")
      end
      ensure_true(old_claim.dig('spec', 'volumeName') == new_claim.dig('spec', 'volumeName'),
                  "#{@component} bound PV changed")
    end
  end

  def verify_http_route
    # Route via the host's published k3d HTTP port while preserving the Host
    # header that Traefik uses to select only this component's local Ingress.
    http = Net::HTTP.new('127.0.0.1', 8080, nil)
    http.open_timeout = 3
    http.read_timeout = 3
    @http_checks.each do |path, expected_status|
      # Protected API paths should reject an anonymous caller with 401. A
      # healthy stateless UI instead returns 200. In either case the request
      # proves Traefik selected this release without sending a credential.
      response = http.request(Net::HTTP::Get.new(path, { 'Host' => @health_host }))
      ensure_true(response.code == expected_status,
                  "#{@component} browser route #{path} returned HTTP #{response.code}, expected #{expected_status}")
    end
    return unless @browser_shell

    # The static frontend can return /healthz while its HTML, CSP, or bundled
    # assets are broken. Check the actual app shell through the same Traefik
    # host and request its two build artifacts without downloading their bodies.
    headers = { 'Host' => @health_host }
    page = http.request(Net::HTTP::Get.new('/', headers))
    ensure_true(page.code == '200' && page.body.include?('<div id="root"></div>'),
                "#{@component} app shell is unavailable")
    csp = page['Content-Security-Policy'].to_s
    ensure_true(csp.include?("default-src 'self'") && csp.include?("object-src 'none'"),
                "#{@component} app shell is missing its expected CSP")
    assets = [page.body[/<script[^>]+src="(\/assets\/[^\"]+\.js)"/, 1],
              page.body[/<link[^>]+href="(\/assets\/[^\"]+\.css)"/, 1]]
    ensure_true(assets.all?, "#{@component} app shell does not reference both bundled assets")
    assets.each do |path|
      asset = http.request(Net::HTTP::Head.new(path, headers))
      ensure_true(asset.code == '200', "#{@component} bundled asset #{path} returned HTTP #{asset.code}")
    end
  end

  def run_smoke_job
    jobs = JSON.parse(kubectl('get', 'jobs', '--output', 'json')).fetch('items')
    name = @smoke_job.fetch(:name)
    ensure_true(jobs.none? { |job| job.dig('metadata', 'name') == name }, "#{@component} smoke Job already exists; inspect it before rerunning")
    manifest = ROOT.join(@smoke_job.fetch(:manifest))
    kubectl('create', '--filename', manifest.to_s)
    # A failed Job is intentionally retained for log/Pod inspection. Only the
    # Job created by this invocation is removed after it completes and logs.
    kubectl('wait', "job/#{name}", '--for=condition=complete', "--timeout=#{@smoke_timeout_seconds}s")
    puts kubectl('logs', "job/#{name}").strip
    kubectl('delete', "job/#{name}", '--wait=true')
    puts "#{@component} smoke passed; disposable test Job removed."
  end
end
