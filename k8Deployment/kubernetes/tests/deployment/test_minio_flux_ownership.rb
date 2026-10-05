# Run the real stateful broker verifier against controlled CLI responses.
# Flux Ready must not bypass stored manifests, claim contracts, runtime digest,
# readiness or HTTP health; existence/lookup errors must block native writes.
require 'minitest/autorun'
require 'minitest/mock'
require_relative '../../scripts/releases/minio-release'

class MinIOFluxOwnershipTest < Minitest::Test
  VERSION = YAML.load_file(HelmRelease::ROOT.join('helm/minio/Chart.yaml')).fetch('version').freeze
  REVISION = "#{VERSION}+abcdef123456.1".freeze

  module ExternalResponses
    attr_accessor :record, :api_error, :native_chart, :native_namespace, :native_status,
                  :manifest_change, :live_change, :claim_change, :pod_ready,
                  :pod_digest, :http_failure, :render_change, :unquoted_console
    attr_reader :commands

    def prepare
      @commands = []
      @record = YAML.load_file(HelmRelease::ROOT.join('gitops/clusters/clouddsp-local/minio/helmrelease.yaml'))
      @record['metadata']['generation'] = 1
      @record['spec']['chart']['spec']['version'] = '*' # exact Git-chart API default
      @record['status'] = { 'observedGeneration' => 1, 'storageNamespace' => 'clouddsp-data',
                            'lastAttemptedRevision' => REVISION,
                            'conditions' => [{ 'type' => 'Ready', 'status' => 'True', 'observedGeneration' => 1 }] }
      @plain = @source_files.map { |file| YAML.load_file(HelmRelease::ROOT.join('services/minio', file)) }
      @plain.each { |o| o.dig('metadata', 'labels')['app.kubernetes.io/managed-by'] = 'Helm' }
      @native_chart = "minio-#{REVISION}"
      @native_namespace = 'clouddsp-data'; @native_status = 'deployed'; @pod_ready = true
      @pod_digest = @plain.find { |o| o['kind'] == 'StatefulSet' }.dig('spec', 'template', 'spec', 'containers', 0, 'image')
      self
    end

    private

    def copy(data)
      Marshal.load(Marshal.dump(data))
    end

    def stored
      copy(@plain).each { |o| o.dig('metadata', 'labels').merge!(FluxOwnership::BINDINGS.fetch('minio').fetch(:origin_labels)) if @record }
    end

    def command(*args, stdin_data: nil)
      @commands << args
      return "k3d-clouddsp-local\n" if args.first(3) == %w[kubectl config get-contexts]
      if args.include?('customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io')
        raise 'Flux API lookup failed' if @api_error == :crd
        return 'present'
      end
      if args.include?('helmrelease.helm.toolkit.fluxcd.io/clouddsp-minio')
        raise 'Flux API lookup failed' if @api_error == :helmrelease
        return '{invalid-json' if @api_error == :json
        return @record ? JSON.generate(@record) : ''
      end
      return '' if args.first(2) == %w[helm lint] || args.include?('--dry-run=server')
      if args.first(2) == %w[helm template]
        data = copy(@plain); @render_change&.call(data); return data.map(&:to_yaml).join
      end
      if args.first == 'helm' && args.include?('list')
        return JSON.generate([{ 'name' => 'clouddsp-minio', 'namespace' => @native_namespace,
                                'status' => @native_status, 'chart' => @native_chart }])
      end
      if args.first == 'helm' && args.include?('manifest')
        data = stored; @manifest_change&.call(data)
        text = data.map(&:to_yaml).join
        # Model source-controller/helm-controller's real unquoted serialization,
        # exercising the YAML parser rather than stubbing parsed structures.
        text = text.gsub(/^(\s*- )['"]:9001['"]$/, '\1:9001') if @unquoted_console
        return text
      end
      if args.include?('pvc/minio-data-clouddsp-minio-0')
        data = { 'metadata' => { 'name' => 'minio-data-clouddsp-minio-0', 'uid' => 'retained-claim' },
                 'spec' => copy(@plain.first.dig('spec', 'volumeClaimTemplates', 0, 'spec')).merge('volumeName' => 'retained-pv'),
                 'status' => { 'phase' => 'Bound' } }
        @claim_change&.call(data); return JSON.generate(data)
      end
      if args.include?('pods')
        return JSON.generate('items' => [{ 'metadata' => { 'uid' => 'minio-pod' },
          'status' => { 'conditions' => [{ 'type' => 'Ready', 'status' => @pod_ready ? 'True' : 'False' }],
                        'containerStatuses' => [{ 'imageID' => @pod_digest }] } }])
      end
      raise "unexpected command: #{args.inspect}"
    end

    # Keep the real HTTP gate; only its transport is controlled by the test.
    def verify_http_route
      response = Struct.new(:code).new(@http_failure ? '503' : '200')
      http = Object.new
      def http.open_timeout=(_); end
      def http.read_timeout=(_); end
      http.define_singleton_method(:request) { |_request| response }
      Net::HTTP.stub(:new, http) { super }
    end

    def live_objects
      data = stored.each do |o|
        o['metadata']['annotations'] = (o['metadata']['annotations'] || {}).merge({ 'meta.helm.sh/release-name' => 'clouddsp-minio',
                                         'meta.helm.sh/release-namespace' => 'clouddsp-data' })
        o['status'] = { 'replicas' => 1, 'readyReplicas' => 1 } if o['kind'] == 'StatefulSet'
      end
      @live_change&.call(data)
      indexed(data)
    end

    def run_smoke_job
      @commands << [:separate_smoke]
    end
  end

  def runner
    CloudDSPMinIORelease.build.extend(ExternalResponses).prepare
  end

  def execute(stage, mode = 'verify')
    status = nil
    output, error = capture_io do
      File.stub(:executable?, true) do
        begin
          stage.run(mode)
        rescue SystemExit => exception
          status = exception.status
        end
      end
    end
    [status, output, error]
  end

  def rejected(stage, message, mode = 'verify')
    status, _, error = execute(stage, mode)
    assert_equal 1, status, error
    assert_includes error, message
    refute stage.commands.any? { |a| a.first == 'helm' && %w[install upgrade].include?(a[1]) }
    refute stage.commands.any? { |a| a.any? { |x| x.to_s.include?('backup-and-restore') } }
  end

  def test_ready_flux_retains_stateful_native_digest_and_application_gates
    %w[verify smoke].each do |mode|
      stage = runner; status, _, error = execute(stage, mode); assert_nil status, error
      assert stage.commands.any? { |a| a.include?('pvc/minio-data-clouddsp-minio-0') }
      assert_includes stage.commands, [:separate_smoke] if mode == 'smoke'
    end
  end

  def test_flux_unquoted_console_address_keeps_its_exact_kubernetes_string
    stage = runner; stage.unquoted_console = true
    status, _, error = execute(stage); assert_nil status, error
    objects = stage.send(:documents, "args: [server, /data, --console-address, :9001]\n")
    assert_equal ':9001', objects.first.fetch('args').last
    assert_instance_of String, objects.first.fetch('args').last
    stage = runner; stage.unquoted_console = true
    stage.manifest_change = ->(a) { a.find { |o| o['kind'] == 'StatefulSet' }.dig('spec', 'template', 'spec', 'containers', 0, 'args')[3] = ':9002' }
    rejected(stage, 'manifest differs')
  end

  def test_existence_reserves_writes_before_chart_secret_or_backup_work
    %w[install adopt].each do |mode|
      [:ready, :failed, :suspended, :deleting].each do |state|
        stage = runner
        stage.record['status']['conditions'].first['status'] = 'False' if state == :failed
        stage.record['spec']['suspend'] = true if state == :suspended
        stage.record['metadata']['deletionTimestamp'] = '2026-10-05T00:00:00Z' if state == :deleting
        rejected(stage, 'owned by Flux HelmRelease', mode)
        refute stage.commands.any? { |a| a.first == 'helm' || a.first == 'ruby' }
      end
    end
  end

  def test_lookup_failures_fail_closed
    [:crd, :helmrelease, :json].each do |failure|
      %w[verify install adopt].each do |mode|
        stage = runner; stage.api_error = failure
        status, _, error = execute(stage, mode); assert_equal 1, status, error
        assert stage.commands.all? { |a| a.first == 'kubectl' }
      end
    end
  end

  def test_exact_source_storage_revision_and_generation_are_required
    changes = [
      ->(r) { r['spec']['chart']['spec']['version'] = 'another-selector' },
      ->(r) { r['spec']['upgrade']['force'] = true },
      ->(r) { r['spec']['upgrade']['remediation']['retries'] = 1 },
      ->(r) { r['spec']['test']['enable'] = true },
      ->(r) { r['spec']['dependsOn'] = [] },
      ->(r) { r['spec']['storageNamespace'] = 'flux-system' },
      ->(r) { r['spec']['targetNamespace'] = 'clouddsp-app' },
      ->(r) { r['status']['observedGeneration'] = 0 },
      ->(r) { r['status']['conditions'].first['observedGeneration'] = 0 },
      ->(r) { r['spec']['chart']['spec']['chart'] = './another-chart' },
      ->(r) { r['spec']['chart']['spec']['sourceRef']['name'] = 'another-source' },
      ->(r) { r['spec']['chart']['spec']['valuesFiles'] << './unreviewed-values.yaml' },
      ->(r) { r['spec']['values'] = { 'image' => { 'reference' => 'wrong' } } },
      ->(r) { r['spec']['valuesFrom'] = [{ 'kind' => 'Secret', 'name' => 'external' }] },
      ->(r) { r['spec']['driftDetection']['ignore'] = [{ 'paths' => ['/spec'] }] },
      ->(r) { r['status']['lastAttemptedRevision'] = '0.2.0+abcdef123456.1' }
    ]
    changes.each do |change|
      stage = runner; change.call(stage.record)
      status, _, error = execute(stage); assert_equal 1, status, error
    end
    stage = runner; stage.native_namespace = 'flux-system'; rejected(stage, 'native Helm release namespace')
    stage = runner; stage.native_chart = 'minio-0.1.0'; rejected(stage, 'Helm chart/release is not deployed')
  end

  def test_ready_flux_does_not_hide_manifest_source_or_live_drift
    stage = runner; stage.render_change = ->(a) { a.first['spec']['replicas'] = 2 }; rejected(stage, 'differs from source')
    stage = runner; stage.manifest_change = ->(a) { a.first['spec']['replicas'] = 2 }; rejected(stage, 'manifest differs')
    stage = runner; stage.live_change = ->(a) { a.first.dig('spec', 'template', 'spec', 'containers').first['image'] = 'minio:latest' }; rejected(stage, 'spec differs from source')
  end

  def test_bound_claim_storage_ready_pod_digest_and_http_health_remain_required
    [->(p) { p['status']['phase'] = 'Pending' },
     ->(p) { p['spec']['volumeName'] = '' },
     ->(p) { p['spec']['resources']['requests']['storage'] = '20Gi' },
     ->(p) { p['spec']['storageClassName'] = 'another-class' }].each do |change|
      stage = runner; stage.claim_change = change; rejected(stage, 'PVC')
    end
    stage = runner; stage.pod_ready = false; rejected(stage, 'Pod is not Ready')
    stage = runner; stage.pod_digest = 'wrong'; rejected(stage, 'running Pod imageID')
    stage = runner; stage.http_failure = true; rejected(stage, 'browser route /minio/health/ready returned HTTP 503')
  end

  def test_explicit_absence_preserves_native_verification
    stage = runner; stage.record = nil; stage.native_chart = "minio-#{VERSION}"
    status, _, error = execute(stage); assert_nil status, error
  end
end
