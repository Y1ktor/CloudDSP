# Exercise upload-intake's actual runner without cluster mutations. Only CLI
# responses are replaced; strict source/render/stored/live parity, running
# digest, Pod readiness, and Flux ownership checks execute production code.
require 'minitest/autorun'
require_relative '../../scripts/releases/upload-intake-release'

class UploadIntakeFluxOwnershipTest < Minitest::Test
  REVISION = '0.1.0+abcdef123456.1'.freeze

  module ExternalResponses
    attr_accessor :record, :api_failure, :native_chart, :origin_labels,
                  :live_change, :manifest_change, :pod_digest, :pod_ready,
                  :release_present, :objects_present
    attr_reader :commands

    def prepare
      @commands = []
      @record = {
        'kind' => 'HelmRelease',
        'metadata' => { 'name' => 'clouddsp-upload-intake', 'namespace' => 'flux-system', 'generation' => 1 },
        'spec' => { 'releaseName' => 'clouddsp-upload-intake', 'targetNamespace' => 'clouddsp-app',
                    'storageNamespace' => 'clouddsp-app' },
        'status' => { 'observedGeneration' => 1, 'storageNamespace' => 'clouddsp-app',
                      'lastAttemptedRevision' => REVISION,
                      'conditions' => [{ 'type' => 'Ready', 'status' => 'True', 'observedGeneration' => 1 }] }
      }
      @release_present = @objects_present = @pod_ready = true
      @native_chart = "upload-intake-#{REVISION}"
      @origin_labels = UploadIntakeFluxOwnership::ORIGIN_LABELS.dup
      @plain = YAML.load_file(HelmRelease::ROOT.join('services', 'upload-intake', 'upload-intake-deployment.yaml'))
      @plain.dig('metadata', 'labels')['app.kubernetes.io/managed-by'] = 'Helm'
      @pod_digest = @plain.dig('spec', 'template', 'spec', 'containers').first.fetch('image')
      self
    end

    def object
      Marshal.load(Marshal.dump(@plain)).tap { |o| o.dig('metadata', 'labels').merge!(@origin_labels) }
    end

    private

    def command(*args, stdin_data: nil)
      @commands << args
      return "k3d-clouddsp-local\n" if args.first(3) == %w[kubectl config get-contexts]
      if args.include?('customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io')
        raise 'Flux API failed' if @api_failure == :crd
        return "customresourcedefinition.apiextensions.k8s.io/helmreleases.helm.toolkit.fluxcd.io\n"
      end
      if args.include?('helmrelease.helm.toolkit.fluxcd.io/clouddsp-upload-intake')
        raise 'Flux API failed' if @api_failure == :helmrelease
        return @record ? JSON.generate(@record) : ''
      end
      return 'lint passed' if args.first(2) == %w[helm lint]
      return @plain.to_yaml if args.first(2) == %w[helm template]
      return '' if args.include?('--dry-run=server')
      if args.first == 'helm' && args.include?('list')
        return @release_present ? JSON.generate([{ 'name' => 'clouddsp-upload-intake', 'namespace' => 'clouddsp-app',
                                                  'status' => 'deployed', 'chart' => @native_chart }]) : '[]'
      end
      if args.first == 'helm' && args.include?('manifest')
        data = object
        @manifest_change&.call(data)
        return data.to_yaml
      end
      if args.include?('pods') && args.include?('get')
        return JSON.generate('items' => [{ 'metadata' => { 'uid' => 'original-intake-pod' },
                                          'status' => { 'conditions' => [{ 'type' => 'Ready', 'status' => @pod_ready ? 'True' : 'False' }],
                                                        'containerStatuses' => [{ 'imageID' => @pod_digest }] } }])
      end
      return @objects_present ? "present\n" : '' if args.first == 'kubectl' && args.include?('--ignore-not-found')
      return '' if args.first == 'ruby'
      raise 'authorized manual Helm write reached' if args.first == 'helm' && %w[install upgrade].include?(args[1])
      raise "unexpected command: #{args.inspect}"
    end

    def live_objects
      data = object
      data['metadata']['annotations'] = { 'meta.helm.sh/release-name' => 'clouddsp-upload-intake',
                                          'meta.helm.sh/release-namespace' => 'clouddsp-app' }
      data['status'] = { 'replicas' => 1, 'readyReplicas' => 1 }
      @live_change&.call(data)
      indexed([data])
    end
  end

  def runner
    CloudDSPUploadIntakeRelease.build.extend(ExternalResponses).prepare
  end

  def execute(intake, mode = 'verify')
    status = nil
    output, error = capture_io do
      begin
        intake.run(mode)
      rescue SystemExit => stopped
        status = stopped.status
      end
    end
    [status, output, error]
  end

  def rejected(intake, expected, mode = 'verify')
    status, _output, error = execute(intake, mode)
    assert_equal 1, status
    assert_includes error, expected
  end

  def test_ready_flux_release_retains_native_spec_and_runtime_checks
    intake = runner
    status, output, error = execute(intake)
    assert_nil status, error
    assert_includes output, 'Pod readiness, and running image digest verified'
    assert_includes intake.singleton_class.ancestors, UploadIntakeFluxOwnership
  end

  def test_presence_blocks_direct_writes_before_chart_and_all_dependencies
    %w[install adopt].each do |mode|
      [:ready, :failed, :suspended, :deleting].each do |state|
        intake = runner
        intake.record['status']['conditions'].first['status'] = 'False' if state == :failed
        intake.record['spec']['suspend'] = true if state == :suspended
        intake.record['metadata']['deletionTimestamp'] = '2026-10-05T00:00:00Z' if state == :deleting
        rejected(intake, 'owned by Flux HelmRelease flux-system/clouddsp-upload-intake', mode)
        assert intake.commands.all? { |command| command.first == 'kubectl' }
      end
    end
  end

  def test_exact_helmrelease_identity_storage_and_current_generation_are_required
    intake = runner
    intake.record['metadata']['name'] = 'clouddsp-job-api'
    rejected(intake, 'unexpected HelmRelease identity')
    %w[releaseName targetNamespace storageNamespace].each do |field|
      intake = runner
      intake.record['spec'][field] = 'wrong'
      rejected(intake, "spec.#{field} must be")
    end
    intake = runner
    intake.record['status']['conditions'].first['observedGeneration'] = 0
    rejected(intake, 'not Ready for its current generation')
    intake = runner
    intake.record['status']['storageNamespace'] = 'flux-system'
    rejected(intake, 'status.storageNamespace must be clouddsp-app')
  end

  def test_reviewed_chart_base_and_exact_native_revision_are_required
    intake = runner
    intake.record['status']['lastAttemptedRevision'] = '0.1.1+abcdef123456.1'
    rejected(intake, 'Flux chart revision differs from the reviewed chart version')
    intake = runner
    intake.native_chart = 'upload-intake-0.1.0+123456abcdef.1'
    rejected(intake, 'expected upload-intake Helm chart/release is not deployed')
  end

  def test_origin_labels_secret_references_and_pod_template_are_still_strict
    intake = runner
    intake.origin_labels['helm.toolkit.fluxcd.io/name'] = 'clouddsp-job-api'
    rejected(intake, 'installed upload-intake release manifest differs')
    intake = runner
    intake.manifest_change = ->(object) { object.dig('spec', 'template', 'spec', 'containers').first['image'] = 'wrong-image' }
    rejected(intake, 'installed upload-intake release manifest differs')
    intake = runner
    intake.live_change = lambda do |object|
      env = object.dig('spec', 'template', 'spec', 'containers').first['env']
      env.find { |item| item['valueFrom'] }.dig('valueFrom', 'secretKeyRef')['name'] = 'wrong-secret'
    end
    rejected(intake, 'spec differs from source')
    intake = runner
    intake.live_change = ->(object) { object.dig('spec', 'template', 'metadata', 'labels').merge!(UploadIntakeFluxOwnership::ORIGIN_LABELS) }
    rejected(intake, 'spec differs from source')
  end

  def test_running_digest_and_ready_pod_remain_required
    intake = runner
    intake.pod_digest = 'sha256:wrong'
    rejected(intake, 'running Pod imageID does not match its locked digest')
    intake = runner
    intake.pod_ready = false
    rejected(intake, 'Pod is not Ready')
  end

  def test_api_failures_never_authorize_direct_install_adopt_or_verification
    [:crd, :helmrelease].each do |failure|
      %w[verify install adopt].each do |mode|
        intake = runner
        intake.api_failure = failure
        rejected(intake, 'Flux API failed', mode)
        assert intake.commands.all? { |command| command.first == 'kubectl' }
      end
    end
  end

  def test_absent_helmrelease_preserves_manual_verification_and_six_fresh_gates
    intake = runner
    intake.record = nil
    intake.native_chart = 'upload-intake-0.1.0'
    intake.origin_labels = {}
    status, _output, error = execute(intake)
    assert_nil status, error
    intake.release_present = intake.objects_present = false
    rejected(intake, 'authorized manual Helm write reached', 'install')
    dependencies = intake.commands.select { |command| command.first == 'ruby' }
    assert_equal %w[job-api-release.rb application-identity-stage.rb upload-intake-rabbitmq-secret-stage.rb rabbitmq-source-intake-bootstrap.rb upload-intake-minio-secret-stage.rb minio-upload-intake-iam-stage.rb],
                 dependencies.map { |command| File.basename(command[1]) }
    assert_equal %w[database upload-intake verify], dependencies[1].last(3)
    assert dependencies.all? { |command| command.last == 'verify' }
  end
end
