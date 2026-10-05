# Exercise the frontend's optional Flux handoff without cluster or Helm writes.
# The real source specs, image lock, chart version, parent manifest/live gates,
# and browser verifier are used; CLI and HTTP responses are local test doubles.
require 'minitest/autorun'
require 'minitest/mock'
require 'yaml'
require 'json'
require_relative '../../scripts/lib/helm-release'
require_relative '../../scripts/gitops/frontend-flux-ownership'
require_relative '../../scripts/gitops/mailpit-flux-ownership'

class FrontendFluxOwnershipTest < Minitest::Test
  VERSION = '0.1.3+abcdef123456.2'.freeze
  ROUTES = %w[/architecture /architecture/ /k8 /cost].freeze
  SHELL = '<html><div id="root"></div><script src="/assets/app.js"></script><link href="/assets/app.css"></html>'.freeze
  CSP = "default-src 'self'; object-src 'none'".freeze

  Response = Struct.new(:code, :body, :headers) do
    def [](name)
      headers[name]
    end
  end

  class FakeHttp
    attr_accessor :open_timeout, :read_timeout, :failed_route
    attr_reader :requests

    def initialize
      @requests = []
    end

    def request(request)
      @requests << [request.method, request.path, request['Host']]
      status = request.path == @failed_route ? '403' : '200'
      Response.new(status, SHELL, { 'Content-Security-Policy' => CSP })
    end
  end

  class FakeFrontend < HelmRelease
    attr_accessor :helmrelease, :crd_present, :api_failure, :chart_record, :record_namespace,
                  :installed_labels, :live_labels, :live_annotations, :pod_labels,
                  :release_present, :release_status, :source_labels, :rendered_labels,
                  :installed_spec_change, :live_spec_change, :live_owner, :objects_present
    attr_reader :events, :http

    def initialize(component: 'frontend', namespace: 'clouddsp-app', release: 'clouddsp-frontend')
      super(component: component, namespace: namespace, release: release,
            source_files: %w[frontend-deployment.yaml frontend-service.yaml frontend-ingress.yaml],
            resources: %w[deployment/clouddsp-frontend service/clouddsp-frontend ingress/clouddsp-frontend],
            pod_selector: 'app.kubernetes.io/name=clouddsp-frontend,app.kubernetes.io/component=frontend',
            health_host: 'clouddsp.localhost', health_path: '/healthz', browser_shell: true,
            browser_routes: ROUTES, allow_fresh_install: true,
            before_install: [%w[ruby keycloak-prerequisite verify], %w[ruby job-api-prerequisite verify]])
      @helmrelease = {
        'kind' => 'HelmRelease',
        'metadata' => { 'name' => 'clouddsp-frontend', 'namespace' => 'flux-system', 'generation' => 2 },
        'spec' => { 'releaseName' => 'clouddsp-frontend', 'targetNamespace' => 'clouddsp-app',
                    'storageNamespace' => 'clouddsp-app' },
        'status' => { 'observedGeneration' => 2, 'storageNamespace' => 'clouddsp-app',
                      'lastAttemptedRevision' => VERSION,
                      'conditions' => [{ 'type' => 'Ready', 'status' => 'True', 'observedGeneration' => 2 }] }
      }
      @events = []
      @http = FakeHttp.new
      @crd_present = true
      @release_present = true
      @objects_present = true
      @release_status = 'deployed'
      @chart_record = "frontend-#{VERSION}"
      @record_namespace = 'clouddsp-app'
      @installed_labels = FrontendFluxOwnership::ORIGIN_LABELS.dup
      @live_labels = FrontendFluxOwnership::ORIGIN_LABELS.dup
      @live_annotations = { 'meta.helm.sh/release-name' => 'clouddsp-frontend',
                            'meta.helm.sh/release-namespace' => 'clouddsp-app' }
      @live_owner = 'Helm'
      @plain_rendered = indexed(@source_files.flat_map do |name|
        documents(self.class::ROOT.join('services', 'frontend', name).read)
      end)
      @plain_rendered.each_value do |object|
        object.fetch('metadata').fetch('labels')['app.kubernetes.io/managed-by'] = 'Helm'
      end
      extend FrontendFluxOwnership
    end

    def manual_release
      @helmrelease = nil
      @chart_record = 'frontend-0.1.3'
      @installed_labels = @live_labels = {}
      self
    end

    private

    def check_tools
      @events << :tools
    end

    def command(*arguments, stdin_data: nil)
      @events << [:command, arguments]
      if arguments.include?('customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io')
        raise 'Flux API lookup failed' if @api_failure == :crd

        return @crd_present ? "customresourcedefinition.apiextensions.k8s.io/helmreleases.helm.toolkit.fluxcd.io\n" : ''
      end
      if arguments.include?('helmrelease.helm.toolkit.fluxcd.io/clouddsp-frontend')
        raise 'Flux API lookup failed' if @api_failure == :helmrelease
        return '{invalid-json' if @api_failure == :invalid_json

        return @helmrelease ? JSON.generate(@helmrelease) : ''
      end
      return 'chart lint passed' if arguments.first(2) == %w[helm lint]
      if arguments.first(2) == %w[helm template]
        objects = Marshal.load(Marshal.dump(@plain_rendered))
        objects.each_value { |object| object.fetch('metadata').fetch('labels').merge!(@rendered_labels) } if @rendered_labels
        return objects.values.map(&:to_yaml).join
      end
      return '' if arguments.include?('--dry-run=server')
      if arguments.include?('pods') && arguments.include?('get')
        return JSON.generate('items' => [{ 'metadata' => { 'uid' => 'original-frontend-pod' },
                                          'status' => { 'conditions' => [{ 'type' => 'Ready', 'status' => 'True' }] } }])
      end
      if arguments.include?('get') && arguments.include?('--ignore-not-found')
        return @objects_present ? "present\n" : ''
      end
      if arguments.first == 'ruby'
        @events << :prerequisite
        return ''
      end
      # Unit tests stop at the first authorized manual write. A sentinel proves
      # CRD/HelmRelease absence did not disable the original install/adopt path.
      raise 'test reached authorized manual Helm write' if arguments.first == 'helm' && %w[install upgrade].include?(arguments[1])

      raise "unexpected external command: #{arguments.inspect}"
    end

    def check_chart_and_source
      @events << :chart
      super
      @source.each_value { |object| object.fetch('metadata').fetch('labels').merge!(@source_labels) } if @source_labels
    end

    def helm(*arguments)
      @events << [:helm_read, arguments]
      if arguments.first == 'list'
        return '[]' unless @release_present

        return JSON.generate([{ 'name' => 'clouddsp-frontend', 'namespace' => @record_namespace,
                                'status' => @release_status, 'chart' => @chart_record }])
      end
      if arguments.first(2) == %w[get manifest]
        objects = Marshal.load(Marshal.dump(@plain_rendered))
        objects.each_value { |object| object.fetch('metadata').fetch('labels').merge!(@installed_labels) }
        @installed_spec_change&.call(objects)
        return objects.values.map(&:to_yaml).join
      end
      raise "unexpected Helm read: #{arguments.inspect}"
    end

    def live_objects
      @events << :live_objects
      objects = Marshal.load(Marshal.dump(@plain_rendered))
      objects.each_value do |object|
        metadata = object.fetch('metadata')
        metadata.fetch('labels')['app.kubernetes.io/managed-by'] = @live_owner
        metadata.fetch('labels').merge!(@live_labels)
        metadata['annotations'] = metadata.fetch('annotations', {}).merge(@live_annotations)
        if object['kind'] == 'Deployment'
          object['status'] = { 'replicas' => 1, 'readyReplicas' => 1 }
          object.dig('spec', 'template', 'metadata', 'labels').merge!(@pod_labels) if @pod_labels
        end
      end
      @live_spec_change&.call(objects)
      objects
    end

    def live_pod
      @events << :pod
      super
    end

    def verify_http_route
      @events << :route
      Net::HTTP.stub(:new, @http) { super }
    end
  end

  def stopped(runner, mode = 'verify')
    _output, error = capture_io { assert_raises(SystemExit) { runner.run(mode) } }
    error
  end

  def completed(runner, mode = 'verify')
    exit_status = nil
    output, error = capture_io do
      begin
        runner.run(mode)
      rescue SystemExit => stopped
        exit_status = stopped.status
      end
    end
    assert_nil exit_status, error
    assert_empty error
    output
  end

  def assert_no_chart_or_write(runner)
    refute_includes runner.events, :chart
    refute_includes runner.events, :prerequisite
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :helm_read }
    refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command && event.last.first == 'helm' }
  end

  def test_ready_flux_release_preserves_native_manifest_pod_and_all_browser_gates
    runner = FakeFrontend.new
    assert_includes completed(runner), 'Helm ownership'
    assert_includes runner.events, :live_objects
    assert_includes runner.events, :pod
    assert_equal ['/healthz', '/', *ROUTES, '/assets/app.js', '/assets/app.css'], runner.http.requests.map { |_method, path, _host| path }
    assert runner.http.requests.all? { |_method, _path, host| host == 'clouddsp.localhost' }

    runner = FakeFrontend.new
    runner.http.failed_route = '/architecture/'
    assert_includes stopped(runner), 'app route /architecture/ returned HTTP 403'
  end

  def test_helmrelease_existence_blocks_install_and_adopt_in_every_controller_state
    %w[install adopt].each do |mode|
      [:ready, :suspended, :failed, :deleting].each do |state|
        runner = FakeFrontend.new
        runner.release_present = false if mode == 'install'
        runner.release_status = 'failed' if mode == 'adopt'
        case state
        when :suspended then runner.helmrelease['spec']['suspend'] = true
        when :failed then runner.helmrelease['status']['conditions'].first['status'] = 'False'
        when :deleting then runner.helmrelease['metadata']['deletionTimestamp'] = '2026-10-04T00:00:00Z'
        end
        assert_includes stopped(runner, mode), 'owned by Flux HelmRelease flux-system/clouddsp-frontend'
        assert_no_chart_or_write(runner)
      end
    end
  end

  def test_adapter_cannot_enable_flux_tolerance_for_a_different_release_instance
    [{ component: 'job-api' }, { namespace: 'clouddsp-data' }, { release: 'another-frontend' }].each do |identity|
      runner = FakeFrontend.new(**identity)
      assert_includes stopped(runner), 'requires the reviewed Frontend release identity'
      assert_no_chart_or_write(runner)
    end
    assert_equal %w[dispatcher frontend generic-dispatcher job-api mailpit upload-intake], FluxOwnership::BINDINGS.keys.sort
    assert_equal 'clouddsp-mailpit', MailpitFluxOwnership::HELMRELEASE_NAME
    assert_equal 'clouddsp-data', MailpitFluxOwnership::RELEASE_NAMESPACE
  end

  def test_api_identity_must_be_the_expected_kind_name_and_namespace
    [['kind', 'Deployment'], ['name', 'clouddsp-mailpit'], ['namespace', 'clouddsp-app']].each do |field, value|
      runner = FakeFrontend.new
      field == 'kind' ? runner.helmrelease[field] = value : runner.helmrelease['metadata'][field] = value
      %w[verify install adopt].each do |mode|
        assert_includes stopped(runner, mode), 'unexpected HelmRelease identity'
        assert_no_chart_or_write(runner)
      end
    end
  end

  def test_stale_missing_or_non_ready_generation_is_rejected
    [:status_generation, :condition_generation, :ready, :missing_ready, :invalid_generation].each do |field|
      runner = FakeFrontend.new
      case field
      when :status_generation then runner.helmrelease['status']['observedGeneration'] = 1
      when :condition_generation then runner.helmrelease['status']['conditions'].first['observedGeneration'] = 1
      when :ready then runner.helmrelease['status']['conditions'].first['status'] = 'False'
      when :missing_ready then runner.helmrelease['status']['conditions'] = []
      when :invalid_generation then runner.helmrelease['metadata']['generation'] = 0
      end
      assert_includes stopped(runner), 'not Ready for its current generation'
      assert_no_chart_or_write(runner)
    end
  end

  def test_suspended_or_deleting_release_cannot_pass_verification
    [:suspended, :deleting].each do |state|
      runner = FakeFrontend.new
      state == :suspended ? runner.helmrelease['spec']['suspend'] = true : runner.helmrelease['metadata']['deletionTimestamp'] = '2026-10-04T00:00:00Z'
      assert_includes stopped(runner), 'is suspended or deleting'
      assert_no_chart_or_write(runner)
    end
  end

  def test_flux_spec_and_status_must_use_the_existing_native_release_storage
    %w[releaseName targetNamespace storageNamespace].each do |field|
      ['unexpected-name-or-namespace', nil].each do |value|
        runner = FakeFrontend.new
        runner.helmrelease['spec'][field] = value
        assert_includes stopped(runner), "spec.#{field} must be"
      end
    end
    runner = FakeFrontend.new
    runner.helmrelease['status']['storageNamespace'] = 'flux-system'
    assert_includes stopped(runner), 'status.storageNamespace must be clouddsp-app'

    runner = FakeFrontend.new
    runner.record_namespace = 'flux-system'
    assert_includes stopped(runner), 'native Helm release namespace must be clouddsp-app'
  end

  def test_native_chart_and_flux_revision_must_match_reviewed_base_and_exact_suffix
    ['frontend-0.1.4+abcdef123456.2', 'frontend-0.1.3+abcdef123456.1', 'frontend-0.1.3', 'mailpit-0.1.3+abcdef123456.2'].each do |chart|
      runner = FakeFrontend.new
      runner.chart_record = chart
      assert_includes stopped(runner), 'expected frontend Helm chart/release is not deployed'
    end
    ['0.1.4+abcdef123456.2', '0.1.3+abcdef123456', '0.1.3+abcdef123456.0',
     '0.1.3+ABCDEF123456.2', '0.1.3+abcdef1234567.2', '0.1.3+abcdef123456.2.extra'].each do |revision|
      runner = FakeFrontend.new
      runner.helmrelease['status']['lastAttemptedRevision'] = revision
      assert_includes stopped(runner), 'Flux chart revision differs from the reviewed chart version'
    end
  end

  def test_missing_partial_wrong_and_arbitrary_stored_origin_labels_are_rejected
    [{}, { 'helm.toolkit.fluxcd.io/name' => 'clouddsp-frontend' },
     FrontendFluxOwnership::ORIGIN_LABELS.merge('helm.toolkit.fluxcd.io/name' => 'clouddsp-mailpit'),
     FrontendFluxOwnership::ORIGIN_LABELS.merge('helm.toolkit.fluxcd.io/namespace' => 'clouddsp-app'),
     FrontendFluxOwnership::ORIGIN_LABELS.merge('unreviewed-label' => 'added')].each do |labels|
      runner = FakeFrontend.new
      runner.installed_labels = labels
      assert_includes stopped(runner), 'installed frontend release manifest differs from the reviewed chart'
    end
  end

  def test_live_labels_and_helm_annotations_remain_exact
    [{}, FrontendFluxOwnership::ORIGIN_LABELS.merge('helm.toolkit.fluxcd.io/name' => 'another-release'),
     FrontendFluxOwnership::ORIGIN_LABELS.merge('unreviewed-label' => 'added')].each do |labels|
      runner = FakeFrontend.new
      runner.live_labels = labels
      assert_includes stopped(runner), 'labels differ from expected Helm ownership'
    end
    %w[meta.helm.sh/release-name meta.helm.sh/release-namespace].each do |annotation|
      runner = FakeFrontend.new
      runner.live_annotations[annotation] = 'unexpected'
      assert_includes stopped(runner), 'unexpected Helm release annotations'
    end
  end

  def test_flux_labels_in_source_or_chart_are_rejected_before_native_verification
    runner = FakeFrontend.new
    runner.source_labels = FrontendFluxOwnership::ORIGIN_LABELS
    assert_includes stopped(runner), 'source/chart must not declare Flux origin labels'

    runner = FakeFrontend.new
    runner.rendered_labels = FrontendFluxOwnership::ORIGIN_LABELS
    assert_includes stopped(runner), 'differs from source at /metadata/labels/'
  end

  def test_stored_and_live_specs_including_pod_templates_remain_strict
    runner = FakeFrontend.new
    runner.pod_labels = FrontendFluxOwnership::ORIGIN_LABELS
    assert_includes stopped(runner), 'spec differs from source at /template/metadata/labels/'

    runner = FakeFrontend.new
    runner.installed_spec_change = lambda do |objects|
      objects.fetch(['Deployment', 'clouddsp-app', 'clouddsp-frontend']).dig('spec', 'template', 'spec', 'containers').first['image'] = 'unexpected-image'
    end
    assert_includes stopped(runner), 'installed frontend release manifest differs from the reviewed chart'

    runner = FakeFrontend.new
    runner.live_spec_change = lambda do |objects|
      objects.fetch(['Ingress', 'clouddsp-app', 'clouddsp-frontend']).dig('spec', 'rules').first['host'] = 'unexpected.localhost'
    end
    assert_includes stopped(runner), 'spec differs from source at /rules/0/host'
  end

  def test_absent_helmrelease_or_crd_preserves_strict_manual_verification
    [true, false].each do |crd_present|
      runner = FakeFrontend.new.manual_release
      runner.crd_present = crd_present
      completed(runner)
      assert_includes runner.events, :route
      if !crd_present
        refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command && event.last.include?('helmrelease.helm.toolkit.fluxcd.io/clouddsp-frontend') }
      end
    end
    runner = FakeFrontend.new.manual_release
    runner.chart_record = "frontend-#{VERSION}"
    assert_includes stopped(runner), 'expected frontend Helm chart/release is not deployed'

    runner = FakeFrontend.new.manual_release
    runner.installed_labels = FrontendFluxOwnership::ORIGIN_LABELS
    assert_includes stopped(runner), 'installed frontend release manifest differs from the reviewed chart'
  end

  def test_absent_helmrelease_preserves_guarded_manual_install_and_adopt_paths
    runner = FakeFrontend.new.manual_release
    runner.release_present = false
    runner.objects_present = false
    assert_includes stopped(runner, 'install'), 'test reached authorized manual Helm write'
    assert_equal 2, runner.events.count(:prerequisite)

    runner = FakeFrontend.new.manual_release
    runner.release_present = false
    runner.live_owner = 'kubectl'
    runner.live_annotations = {}
    assert_includes stopped(runner, 'adopt'), 'test reached authorized manual Helm write'
    assert_includes runner.events, :live_objects
    assert_includes runner.events, :pod
  end

  def test_api_and_json_failures_never_authorize_verification_or_direct_writes
    [:crd, :helmrelease, :invalid_json].each do |lookup|
      %w[verify install adopt].each do |mode|
        runner = FakeFrontend.new
        runner.api_failure = lookup
        error = stopped(runner, mode)
        assert_includes error, lookup == :invalid_json ? 'unexpected' : 'Flux API lookup failed'
        assert_no_chart_or_write(runner)
      end
    end
  end
end
