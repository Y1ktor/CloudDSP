#!/usr/bin/env ruby
# One deliberately narrow Helm handoff for the existing local Mailpit objects.
# `plan` reads only; `adopt` transfers four reviewed objects to one release;
# `verify` checks ownership/routing; `smoke` runs the versioned SMTP capture Job.
# Nothing here creates a namespace, changes another release, or reads Secrets.

require 'json'
require 'net/http'
require 'open3'
require 'pathname'
require 'yaml'

class MailpitRelease
  ROOT = Pathname.new(__dir__).parent
  CHART = ROOT.join('helm', 'mailpit')
  CONTEXT = 'k3d-clouddsp-local'
  NAMESPACE = 'clouddsp-data'
  RELEASE = 'clouddsp-mailpit'
  SOURCE_FILES = %w[mailpit-deployment.yaml mailpit-services.yaml mailpit-ingress.yaml].freeze
  RESOURCES = %w[deployment/clouddsp-mailpit service/clouddsp-mailpit-smtp service/clouddsp-mailpit ingress/clouddsp-mailpit].freeze
  POD_SELECTOR = 'app.kubernetes.io/name=mailpit,app.kubernetes.io/instance=clouddsp-mailpit,app.kubernetes.io/component=test-email'
  SMOKE_JOB = 'mailpit-smtp-capture-smoke'

  def run(mode)
    abort usage unless %w[plan adopt verify smoke].include?(mode)
    check_tools
    check_chart_and_source

    case mode
    when 'plan'
      check_cluster('kubectl', allow_failed_release: true)
      puts 'Mailpit chart matches four source objects and all four live specs; adoption is ready.'
    when 'adopt'
      before = check_cluster('kubectl', allow_failed_release: true)
      # Helm 4's explicit takeover flag is used only for this one-time handoff
      # (or a retry of its failed first revision). Automatic rollback could
      # delete adopted objects, so a failed attempt remains for inspection.
      puts 'Adopting the four existing Mailpit objects into Helm...'
      # kubectl owns the old managed-by label through server-side field
      # management. Force only that pre-reviewed conflict after the exact
      # rendered/source/live comparison above has passed. A failed first
      # revision can be upgraded in place; uninstalling it could delete the
      # four existing resources once Helm considers them part of the release.
      operation = before.fetch(:release) ? 'upgrade' : 'install'
      output = command('helm', operation, RELEASE, CHART.to_s,
                       '--kube-context', CONTEXT, '--namespace', NAMESPACE,
                       '--take-ownership', '--force-conflicts', '--wait', '--timeout', '3m')
      puts output.lines.grep(/^(NAME|NAMESPACE|STATUS|REVISION):/)
      after = check_cluster('Helm')
      verify_stable_identity(before, after)
      verify_http_route
      puts 'Mailpit adopted: release deployed; four UIDs, both Service IPs, and Pod UID unchanged; browser route ready.'
    when 'verify'
      check_cluster('Helm')
      verify_http_route
      puts 'Mailpit Helm ownership, source spec, Pod readiness, and browser route verified.'
    when 'smoke'
      check_cluster('Helm')
      run_smoke_job
    end
  rescue StandardError => error
    warn "Mailpit #{mode} stopped: #{error.message}"
    warn 'If install started, inspect the release and four live objects before any retry; do not uninstall an adopted release to recover.' if mode == 'adopt'
    exit 1
  end

  private

  def usage
    'Usage: ./k8Deployment/kubernetes/scripts/mailpit-release.rb plan|adopt|verify|smoke'
  end

  def ensure_true(condition, message)
    raise message unless condition
  end

  def command(*args, stdin_data: nil)
    output, error, status = Open3.capture3(*args, stdin_data: stdin_data)
    raise "#{args.first} failed: #{error.strip.empty? ? output.strip : error.strip}" unless status.success?
    output
  end

  def kubectl(*args)
    command('kubectl', '--context', CONTEXT, '--namespace', NAMESPACE, *args)
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
    ensure_true(result.length == documents.length, 'duplicate Mailpit resource identity')
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
    source = indexed(SOURCE_FILES.flat_map do |name|
      documents(ROOT.join('services', 'mailpit', name).read)
    end)
    ensure_true(source.length == 4, 'expected exactly four source Mailpit objects')
    lock = YAML.load_file(ROOT.join('images.lock.yaml'))
    values = YAML.load_file(CHART.join('values.yaml'))
    locked_image = lock.fetch('images').fetch('mailpit').fetch('immutableReference')
    ensure_true(values.fetch('image').fetch('reference') == locked_image, 'Mailpit chart image differs from images.lock.yaml')

    command('helm', 'lint', CHART.to_s, '--strict')
    rendered_yaml = command('helm', 'template', RELEASE, CHART.to_s, '--namespace', NAMESPACE)
    rendered = indexed(documents(rendered_yaml))
    ensure_true(rendered.keys.sort == source.keys.sort, 'chart must render exactly the four source Mailpit identities')
    source.each do |id, object|
      expected = Marshal.load(Marshal.dump(object))
      expected.fetch('metadata').fetch('labels')['app.kubernetes.io/managed-by'] = 'Helm'
      difference = first_difference(expected, rendered.fetch(id))
      ensure_true(difference.nil?, "rendered #{id.first}/#{id.last} differs from source at #{difference}")
    end
    # API-server dry run validates the chart against this cluster's schemas
    # without persisting the Helm ownership change or touching the Pod.
    command('kubectl', '--context', CONTEXT, '--namespace', NAMESPACE,
            'apply', '--dry-run=server', '--filename', '-', stdin_data: rendered_yaml)
    @source = source
    @rendered = rendered
    @chart_identity = "mailpit-#{YAML.load_file(CHART.join('Chart.yaml')).fetch('version')}"
  end

  def release_record
    # Helm 4 lists every release state by default in the selected namespace.
    # A failed or pending attempt must still block a second takeover install.
    releases = JSON.parse(helm('list', '--namespace', NAMESPACE, '--output', 'json'))
    releases.find { |release| release['name'] == RELEASE }
  end

  def live_objects
    list = JSON.parse(kubectl('get', *RESOURCES, '--output', 'json'))
    indexed(list.fetch('items'))
  end

  def live_pod
    list = JSON.parse(kubectl('get', 'pods', '--selector', POD_SELECTOR, '--output', 'json'))
    pods = list.fetch('items')
    ensure_true(pods.length == 1, "expected one Mailpit Pod, found #{pods.length}")
    pod = pods.first
    ensure_true(pod.fetch('status').fetch('conditions', []).any? { |condition| condition['type'] == 'Ready' && condition['status'] == 'True' },
                'Mailpit Pod is not Ready')
    pod
  end

  def normalized_live_spec(object)
    spec = Marshal.load(Marshal.dump(object.fetch('spec')))
    case object.fetch('kind')
    when 'Deployment'
      spec.delete('progressDeadlineSeconds')
      pod = spec.fetch('template').fetch('spec')
      %w[dnsPolicy restartPolicy schedulerName].each { |key| pod.delete(key) }
      pod.fetch('containers').each do |container|
        %w[terminationMessagePath terminationMessagePolicy].each { |key| container.delete(key) }
        %w[startupProbe readinessProbe livenessProbe].each do |probe|
          container.fetch(probe).delete('successThreshold')
        end
      end
    when 'Service'
      %w[clusterIP clusterIPs internalTrafficPolicy ipFamilies ipFamilyPolicy sessionAffinity].each { |key| spec.delete(key) }
    end
    spec
  end

  def check_cluster(owner, allow_failed_release: false)
    record = release_record
    if owner == 'Helm'
      ensure_true(record && record['status'] == 'deployed' && record['chart'] == @chart_identity,
                  'expected Mailpit Helm chart/release is not deployed')
      # A Ready object with Helm labels alone does not prove the release stores
      # this reviewed chart. Compare the release's actual manifest as well.
      installed = indexed(documents(helm('get', 'manifest', RELEASE, '--namespace', NAMESPACE)))
      ensure_true(installed == @rendered, 'installed Mailpit release manifest differs from the reviewed chart')
    else
      retryable = allow_failed_release && record && record['status'] == 'failed' && record['chart'] == @chart_identity
      ensure_true(record.nil? || retryable, 'Mailpit Helm release already exists in a non-retryable state; inspect it before take-ownership')
    end
    objects = live_objects
    ensure_true(objects.keys.sort == @source.keys.sort, 'live Mailpit resource identities differ from chart')
    @source.each do |id, expected|
      object = objects.fetch(id)
      metadata = object.fetch('metadata')
      ensure_true(metadata.fetch('ownerReferences', []).empty?, "#{id.first}/#{id.last} has an unexpected controller owner")
      labels = expected.fetch('metadata').fetch('labels').merge('app.kubernetes.io/managed-by' => owner)
      ensure_true(metadata.fetch('labels') == labels, "#{id.first}/#{id.last} labels differ from expected #{owner} ownership")
      annotations = metadata.fetch('annotations', {})
      expected.fetch('metadata').fetch('annotations', {}).each do |key, value|
        ensure_true(annotations[key] == value, "#{id.first}/#{id.last} annotation #{key} differs")
      end
      if owner == 'Helm'
        ensure_true(annotations['meta.helm.sh/release-name'] == RELEASE && annotations['meta.helm.sh/release-namespace'] == NAMESPACE,
                    "#{id.first}/#{id.last} has unexpected Helm release annotations")
      else
        ensure_true(!annotations.key?('meta.helm.sh/release-name') && !annotations.key?('meta.helm.sh/release-namespace'),
                    "#{id.first}/#{id.last} is already claimed by Helm")
      end
      difference = first_difference(expected.fetch('spec'), normalized_live_spec(object))
      ensure_true(difference.nil?, "live #{id.first}/#{id.last} spec differs from source at #{difference}")
    end
    deployment = objects.fetch(['Deployment', NAMESPACE, RELEASE])
    ensure_true(deployment.dig('status', 'readyReplicas') == 1, 'Mailpit Deployment has no ready replica')
    { objects: objects, pod_uid: live_pod.fetch('metadata').fetch('uid'), release: record }
  end

  def verify_stable_identity(before, after)
    before.fetch(:objects).each do |id, object|
      current = after.fetch(:objects).fetch(id)
      ensure_true(object.dig('metadata', 'uid') == current.dig('metadata', 'uid'), "#{id.first}/#{id.last} was replaced")
      next unless id.first == 'Service'
      ensure_true(object.dig('spec', 'clusterIP') == current.dig('spec', 'clusterIP'), "#{id.last} ClusterIP changed")
    end
    ensure_true(before.fetch(:pod_uid) == after.fetch(:pod_uid), 'Mailpit Pod was replaced during metadata-only adoption')
  end

  def verify_http_route
    # Route via the host's published k3d HTTP port while preserving the Host
    # header that Traefik uses to select only Mailpit's local Ingress.
    http = Net::HTTP.new('127.0.0.1', 8080, nil)
    http.open_timeout = 3
    http.read_timeout = 3
    response = http.request(Net::HTTP::Get.new('/readyz', { 'Host' => 'mailpit.localhost' }))
    ensure_true(response.code == '200', "Mailpit browser route returned HTTP #{response.code}")
  end

  def run_smoke_job
    jobs = JSON.parse(kubectl('get', 'jobs', '--output', 'json')).fetch('items')
    ensure_true(jobs.none? { |job| job.dig('metadata', 'name') == SMOKE_JOB }, 'Mailpit smoke Job already exists; inspect it before rerunning')
    manifest = ROOT.join('tests', 'mailpit-smoke', 'mailpit-smtp-capture-smoke-job.yaml')
    kubectl('create', '--filename', manifest.to_s)
    # A failed Job is intentionally retained for log/Pod inspection. Only the
    # Job created by this invocation is removed after it completes and logs.
    kubectl('wait', "job/#{SMOKE_JOB}", '--for=condition=complete', '--timeout=150s')
    puts kubectl('logs', "job/#{SMOKE_JOB}").strip
    kubectl('delete', "job/#{SMOKE_JOB}", '--wait=true')
    puts 'Mailpit SMTP capture smoke passed; disposable test Job removed.'
  end
end

MailpitRelease.new.run(ARGV.length == 1 ? ARGV.first : nil)
