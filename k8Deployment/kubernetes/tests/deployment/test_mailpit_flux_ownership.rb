# Exercise the Mailpit handoff against native Helm/source/live checks without
# a Kubernetes connection. Fixtures retain the real four Mailpit source specs;
# only the CLI responses and disposable smoke Job are replaced by test doubles.
require 'minitest/autorun'
require 'yaml'
require 'json'

# Keep this test usable in the older GitOps checkout, whose shared helper was
# called StatelessRelease before deployment scripts were reorganized.
modern_helper = File.expand_path('../../scripts/lib/helm-release.rb', __dir__)
require File.file?(modern_helper) ? modern_helper : File.expand_path('../../scripts/stateless-release.rb', __dir__)
require_relative '../../scripts/gitops/mailpit-flux-ownership'

class MailpitFluxOwnershipTest < Minitest::Test
  VERSION = '0.1.0+abcdef123456.1'.freeze
  Helper = defined?(HelmRelease) ? HelmRelease : StatelessRelease

  class FakeMailpit < Helper
    attr_accessor :helmrelease, :crd_present, :api_failure, :chart_record, :record_namespace,
                  :installed_labels, :live_labels, :live_annotations, :pod_labels,
                  :release_present, :release_status
    attr_reader :events

    def initialize
      super(component: 'mailpit', namespace: 'clouddsp-data', release: 'clouddsp-mailpit',
            source_files: %w[mailpit-deployment.yaml mailpit-services.yaml mailpit-ingress.yaml],
            resources: %w[deployment/clouddsp-mailpit service/clouddsp-mailpit-smtp service/clouddsp-mailpit ingress/clouddsp-mailpit],
            pod_selector: 'app.kubernetes.io/name=mailpit', health_host: 'mailpit.localhost',
            health_path: '/readyz', allow_fresh_install: true,
            smoke_job: { name: 'mailpit-smtp-capture-smoke', manifest: 'unused-test-double' })
      @helmrelease = {
        'kind' => 'HelmRelease',
        'metadata' => { 'name' => 'clouddsp-mailpit', 'namespace' => 'flux-system', 'generation' => 2 },
        'spec' => { 'releaseName' => 'clouddsp-mailpit', 'targetNamespace' => 'clouddsp-data',
                    'storageNamespace' => 'clouddsp-data' },
        'status' => { 'observedGeneration' => 2, 'storageNamespace' => 'clouddsp-data',
                      'lastAttemptedRevision' => VERSION,
                      'conditions' => [{ 'type' => 'Ready', 'status' => 'True', 'observedGeneration' => 2 }] }
      }
      @events = []
      @crd_present = true
      @release_present = true
      @release_status = 'deployed'
      @chart_record = "mailpit-#{VERSION}"
      @record_namespace = 'clouddsp-data'
      @installed_labels = MailpitFluxOwnership::ORIGIN_LABELS.dup
      @live_labels = MailpitFluxOwnership::ORIGIN_LABELS.dup
      @live_annotations = { 'meta.helm.sh/release-name' => 'clouddsp-mailpit',
                            'meta.helm.sh/release-namespace' => 'clouddsp-data' }
      extend MailpitFluxOwnership
    end

    def manual_release
      @helmrelease = nil
      @chart_record = 'mailpit-0.1.0'
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
      if arguments.include?('helmrelease.helm.toolkit.fluxcd.io/clouddsp-mailpit')
        raise 'Flux API lookup failed' if @api_failure == :helmrelease

        return @helmrelease ? JSON.generate(@helmrelease) : ''
      end
      raise "unexpected external command: #{arguments.inspect}"
    end

    def check_chart_and_source
      @events << :chart
      @source = indexed(@source_files.flat_map do |name|
        documents(self.class::ROOT.join('services', 'mailpit', name).read)
      end)
      @plain_rendered = Marshal.load(Marshal.dump(@source))
      @plain_rendered.each_value do |object|
        object.fetch('metadata').fetch('labels')['app.kubernetes.io/managed-by'] = 'Helm'
      end
      @rendered = Marshal.load(Marshal.dump(@plain_rendered))
      @chart_identity = 'mailpit-0.1.0'
    end

    def helm(*arguments)
      @events << [:helm_read, arguments]
      if arguments.first == 'list'
        return '[]' unless @release_present

        return JSON.generate([{ 'name' => 'clouddsp-mailpit', 'namespace' => @record_namespace,
                                'status' => @release_status, 'chart' => @chart_record }])
      end
      if arguments.first(2) == %w[get manifest]
        objects = Marshal.load(Marshal.dump(@plain_rendered))
        objects.each_value { |object| object.fetch('metadata').fetch('labels').merge!(@installed_labels) }
        return objects.values.map(&:to_yaml).join
      end
      raise "unexpected Helm command: #{arguments.inspect}"
    end

    def live_objects
      @events << :live_objects
      objects = Marshal.load(Marshal.dump(@plain_rendered))
      objects.each_value do |object|
        object.fetch('metadata').fetch('labels').merge!(@live_labels)
        object.fetch('metadata')['annotations'] = object.fetch('metadata').fetch('annotations', {}).merge(@live_annotations)
        if object['kind'] == 'Deployment'
          object['status'] = { 'replicas' => 1, 'readyReplicas' => 1 }
          object.dig('spec', 'template', 'metadata', 'labels').merge!(@pod_labels) if @pod_labels
        end
      end
      objects
    end

    def live_pod
      @events << :pod
      { 'metadata' => { 'uid' => 'original-mailpit-pod' } }
    end

    def verify_http_route
      @events << :route
    end

    def run_smoke_job
      @events << :smoke
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

  def test_ready_flux_release_passes_native_manifest_live_spec_and_smoke_gates
    %w[verify smoke].each do |mode|
      runner = FakeMailpit.new
      output = completed(runner, mode)
      assert_includes runner.events, :live_objects
      assert_includes runner.events, :pod
      assert_includes runner.events, mode == 'verify' ? :route : :smoke
      assert_includes output, 'Helm ownership' if mode == 'verify'
      refute runner.events.any? { |event| event.is_a?(Array) && event.first == :command && event.last.first == 'helm' }
    end
  end

  def test_helmrelease_existence_blocks_install_and_adopt_before_chart_or_prerequisite_work
    %w[install adopt].each do |mode|
      [:ready, :suspended, :not_ready, :deleting].each do |state|
        runner = FakeMailpit.new
        # Neither an absent native release nor a failed adoption revision
        # transfers permission back from an existing HelmRelease to the CLI.
        runner.release_present = false if mode == 'install'
        runner.release_status = 'failed' if mode == 'adopt'
        case state
        when :suspended then runner.helmrelease['spec']['suspend'] = true
        when :not_ready then runner.helmrelease['status']['conditions'].first['status'] = 'False'
        when :deleting then runner.helmrelease['metadata']['deletionTimestamp'] = '2026-10-04T00:00:00Z'
        end
        assert_includes stopped(runner, mode), 'owned by Flux HelmRelease flux-system/clouddsp-mailpit'
        refute_includes runner.events, :chart
        refute_includes runner.events, :live_objects
        refute runner.events.any? { |event| event.is_a?(Array) && event.first == :helm_read }
      end
    end
  end

  def test_stale_or_non_ready_flux_status_does_not_allow_verification_or_smoke
    %w[verify smoke].each do |mode|
      [:status_generation, :condition_generation, :ready].each do |stale_field|
        runner = FakeMailpit.new
        case stale_field
        when :status_generation then runner.helmrelease['status']['observedGeneration'] = 1
        when :condition_generation then runner.helmrelease['status']['conditions'].first['observedGeneration'] = 1
        when :ready then runner.helmrelease['status']['conditions'].first['status'] = 'False'
        end
        assert_includes stopped(runner, mode), 'not Ready for its current generation'
        refute_includes runner.events, :chart
        refute_includes runner.events, :smoke
      end
    end
  end

  def test_flux_must_reference_the_existing_native_release_and_storage_namespace
    %w[releaseName targetNamespace storageNamespace].each do |field|
      runner = FakeMailpit.new
      runner.helmrelease['spec'][field] = 'unexpected-release-or-namespace'
      assert_includes stopped(runner), "spec.#{field} must be"
    end
    runner = FakeMailpit.new
    runner.helmrelease['status']['storageNamespace'] = 'flux-system'
    assert_includes stopped(runner), 'status.storageNamespace must be clouddsp-data'
  end

  def test_native_helm_chart_must_equal_the_exact_current_flux_revision
    ['mailpit-0.1.1+abcdef123456.1', 'mailpit-0.1.0+abcdef123456.2', 'mailpit-0.1.0'].each do |chart|
      runner = FakeMailpit.new
      runner.chart_record = chart
      assert_includes stopped(runner), 'expected mailpit Helm chart/release is not deployed'
    end
    ['0.1.1+abcdef123456.1', '0.1.0+abcdef123456', '0.1.0+abcdef123456.0'].each do |revision|
      runner = FakeMailpit.new
      runner.helmrelease['status']['lastAttemptedRevision'] = revision
      assert_includes stopped(runner), 'Flux chart revision differs from the reviewed chart version'
    end
  end

  def test_native_release_namespace_and_helm_annotations_stay_exact
    runner = FakeMailpit.new
    runner.record_namespace = 'flux-system'
    assert_includes stopped(runner), 'native Helm release namespace must be clouddsp-data'

    runner = FakeMailpit.new
    runner.live_annotations['meta.helm.sh/release-namespace'] = 'flux-system'
    assert_includes stopped(runner), 'unexpected Helm release annotations'
  end

  def test_missing_wrong_partial_or_extra_flux_manifest_labels_are_rejected
    [{},
     { 'helm.toolkit.fluxcd.io/name' => 'clouddsp-mailpit' },
     MailpitFluxOwnership::ORIGIN_LABELS.merge('helm.toolkit.fluxcd.io/namespace' => 'other-namespace'),
     MailpitFluxOwnership::ORIGIN_LABELS.merge('unreviewed-label' => 'added')].each do |labels|
      runner = FakeMailpit.new
      runner.installed_labels = labels
      assert_includes stopped(runner), 'installed mailpit release manifest differs from the reviewed chart'
    end
  end

  def test_live_label_drift_and_pod_template_drift_are_rejected
    [{ 'helm.toolkit.fluxcd.io/name' => 'other-release' },
     { 'helm.toolkit.fluxcd.io/namespace' => 'flux-system' },
     MailpitFluxOwnership::ORIGIN_LABELS.merge('unreviewed-label' => 'added')].each do |labels|
      runner = FakeMailpit.new
      runner.live_labels = labels
      assert_includes stopped(runner), 'labels differ from expected Helm ownership'
    end
    runner = FakeMailpit.new
    runner.pod_labels = MailpitFluxOwnership::ORIGIN_LABELS
    assert_includes stopped(runner), 'spec differs from source at /template/metadata/labels/'
  end

  def test_absent_helmrelease_retains_strict_manual_helm_verification
    runner = FakeMailpit.new.manual_release
    completed(runner)

    runner = FakeMailpit.new.manual_release
    runner.chart_record = "mailpit-#{VERSION}"
    assert_includes stopped(runner), 'expected mailpit Helm chart/release is not deployed'
  end

  def test_absent_crd_is_distinct_from_api_failures
    runner = FakeMailpit.new.manual_release
    runner.crd_present = false
    completed(runner)
    refute runner.events.any? { |event|
      event.is_a?(Array) && event.first == :command && event.last.include?('helmrelease.helm.toolkit.fluxcd.io/clouddsp-mailpit')
    }

    [:crd, :helmrelease].each do |lookup|
      runner = FakeMailpit.new
      runner.api_failure = lookup
      assert_includes stopped(runner, 'install'), 'Flux API lookup failed'
      refute_includes runner.events, :chart
      refute runner.events.any? { |event| event.is_a?(Array) && event.first == :helm_read }
    end
  end
end
