# Exercise the actual Job API runner with read-only CLI/HTTP responses replaced.
# Its source/render/stored/live parity, Pod digest, protected routes, dependency
# order, and common Flux ownership gates still execute through production code.
require 'minitest/autorun'
require 'minitest/mock'
require_relative '../../scripts/releases/job-api-release'

class JobApiFluxOwnershipTest < Minitest::Test
  REVISION = '0.1.0+abcdef123456.1'.freeze

  # This module replaces external I/O only. It deliberately does not override
  # check_tools/check_chart_and_source/check_cluster/live_pod, which must retain
  # both the production adapter and shared Helm checks on the real runner.
  module ExternalResponses
    attr_accessor :record, :crd_present, :api_error, :native_chart, :native_namespace,
                  :origin_labels, :live_change, :manifest_change, :pod_digest,
                  :pod_ready, :http_code, :release_present, :objects_present
    attr_reader :commands, :requests

    def prepare
      @commands, @requests = [], []
      @record = {
        'kind' => 'HelmRelease',
        'metadata' => { 'name' => 'clouddsp-job-api', 'namespace' => 'flux-system', 'generation' => 1 },
        'spec' => { 'releaseName' => 'clouddsp-job-api', 'targetNamespace' => 'clouddsp-app',
                    'storageNamespace' => 'clouddsp-app' },
        'status' => { 'observedGeneration' => 1, 'storageNamespace' => 'clouddsp-app',
                      'lastAttemptedRevision' => REVISION,
                      'conditions' => [{ 'type' => 'Ready', 'status' => 'True', 'observedGeneration' => 1 }] }
      }
      @crd_present = @release_present = @objects_present = @pod_ready = true
      @native_chart = "job-api-#{REVISION}"
      @native_namespace = 'clouddsp-app'
      @origin_labels = JobApiFluxOwnership::ORIGIN_LABELS.dup
      @http_code = '401'
      @plain = @source_files.map do |file|
        YAML.load_file(HelmRelease::ROOT.join('services', 'job-api', file))
      end
      @plain.each { |object| object.dig('metadata', 'labels')['app.kubernetes.io/managed-by'] = 'Helm' }
      @pod_digest = @plain.first.dig('spec', 'template', 'spec', 'containers').first.fetch('image')
      self
    end

    def objects
      Marshal.load(Marshal.dump(@plain)).each do |object|
        object.dig('metadata', 'labels').merge!(@origin_labels)
      end
    end

    private

    def command(*args, stdin_data: nil)
      @commands << args
      return "k3d-clouddsp-local\n" if args.first(3) == %w[kubectl config get-contexts]
      if args.include?('customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io')
        raise 'Flux API failed' if @api_error == :crd
        return @crd_present ? "customresourcedefinition.apiextensions.k8s.io/helmreleases.helm.toolkit.fluxcd.io\n" : ''
      end
      if args.include?('helmrelease.helm.toolkit.fluxcd.io/clouddsp-job-api')
        raise 'Flux API failed' if @api_error == :helmrelease
        return '{invalid-json' if @api_error == :json
        return @record ? JSON.generate(@record) : ''
      end
      return 'lint passed' if args.first(2) == %w[helm lint]
      return @plain.map(&:to_yaml).join if args.first(2) == %w[helm template]
      return '' if args.include?('--dry-run=server')
      if args.first == 'helm' && args.include?('list')
        return @release_present ? JSON.generate([{ 'name' => 'clouddsp-job-api', 'namespace' => @native_namespace,
                                                  'status' => 'deployed', 'chart' => @native_chart }]) : '[]'
      end
      if args.first == 'helm' && args.include?('manifest')
        data = objects
        @manifest_change&.call(data)
        return data.map(&:to_yaml).join
      end
      if args.include?('pods') && args.include?('get')
        return JSON.generate('items' => [{ 'metadata' => { 'uid' => 'original-api-pod' },
                                          'status' => { 'conditions' => [{ 'type' => 'Ready', 'status' => @pod_ready ? 'True' : 'False' }],
                                                        'containerStatuses' => [{ 'imageID' => @pod_digest }] } }])
      end
      return @objects_present ? "present\n" : '' if args.first == 'kubectl' && args.include?('--ignore-not-found')
      return '' if args.first == 'ruby'
      raise 'authorized manual Helm write reached' if args.first == 'helm' && %w[install upgrade].include?(args[1])
      raise "unexpected command: #{args.inspect}"
    end

    def live_objects
      data = objects
      data.each do |object|
        object['metadata']['annotations'] = object['metadata'].fetch('annotations', {}).merge(
          'meta.helm.sh/release-name' => 'clouddsp-job-api',
          'meta.helm.sh/release-namespace' => 'clouddsp-app'
        )
        object['status'] = { 'replicas' => 1, 'readyReplicas' => 1 } if object['kind'] == 'Deployment'
      end
      @live_change&.call(data)
      indexed(data)
    end

    def verify_http_route
      runner = self
      http = Object.new
      http.define_singleton_method(:open_timeout=) { |_value| }
      http.define_singleton_method(:read_timeout=) { |_value| }
      http.define_singleton_method(:request) do |request|
        runner.requests << [request.path, request['Host']]
        Struct.new(:code).new(runner.http_code)
      end
      Net::HTTP.stub(:new, http) { super }
    end
  end

  def runner
    CloudDSPJobApiRelease.build.extend(ExternalResponses).prepare
  end

  def run_release(api, mode = 'verify')
    status = nil
    output, error = capture_io do
      begin
        api.run(mode)
      rescue SystemExit => stopped
        status = stopped.status
      end
    end
    [status, output, error]
  end

  def rejected(api, expected, mode = 'verify')
    status, _output, error = run_release(api, mode)
    assert_equal 1, status
    assert_includes error, expected
  end

  def test_ready_flux_api_keeps_running_digest_and_both_protected_route_checks
    api = runner
    status, output, error = run_release(api)
    assert_nil status, error
    assert_includes output, 'running image digest and protected browser routes verified'
    assert_equal [['/auth/me', 'clouddsp.localhost'], ['/jobs', 'clouddsp.localhost']], api.requests
    assert_includes api.singleton_class.ancestors, JobApiFluxOwnership
  end

  def test_presence_blocks_direct_install_and_adopt_before_chart_or_prerequisite_commands
    %w[install adopt].each do |mode|
      [:ready, :failed, :suspended, :deleting].each do |state|
        api = runner
        api.record['status']['conditions'].first['status'] = 'False' if state == :failed
        api.record['spec']['suspend'] = true if state == :suspended
        api.record['metadata']['deletionTimestamp'] = '2026-10-05T00:00:00Z' if state == :deleting
        rejected(api, 'owned by Flux HelmRelease flux-system/clouddsp-job-api', mode)
        assert api.commands.all? { |cmd| cmd.first == 'kubectl' }
      end
    end
  end

  def test_identity_storage_and_current_ready_generation_are_exact
    [[:metadata, 'name'], [:metadata, 'namespace'], [:spec, 'releaseName'], [:spec, 'targetNamespace'],
     [:spec, 'storageNamespace'], [:status, 'storageNamespace']].each do |section, field|
      api = runner
      api.record[section.to_s][field] = 'unexpected'
      rejected(api, section == :metadata ? 'unexpected HelmRelease identity' : "#{section}.#{field} must be")
    end
    [:status, :condition].each do |location|
      api = runner
      target = location == :status ? api.record['status'] : api.record['status']['conditions'].first
      target['observedGeneration'] = 0
      rejected(api, 'not Ready for its current generation')
    end
    api = runner
    api.native_namespace = 'flux-system'
    rejected(api, 'native Helm release namespace must be clouddsp-app')
  end

  def test_chart_revision_must_match_the_reviewed_base_and_native_release
    ['0.1.1+abcdef123456.1', '0.1.0+abcdef123456.0', '0.1.0+abcdef123456'].each do |revision|
      api = runner
      api.record['status']['lastAttemptedRevision'] = revision
      rejected(api, 'Flux chart revision differs from the reviewed chart version')
    end
    api = runner
    api.native_chart = 'job-api-0.1.0+123456abcdef.1'
    rejected(api, 'expected job-api Helm chart/release is not deployed')
  end

  def test_spec_secret_references_and_labels_cannot_drift
    api = runner
    api.origin_labels['helm.toolkit.fluxcd.io/name'] = 'clouddsp-frontend'
    rejected(api, 'installed job-api release manifest differs')
    api = runner
    api.manifest_change = ->(objects) { objects.first.dig('spec', 'template', 'spec', 'containers').first['image'] = 'wrong-image' }
    rejected(api, 'installed job-api release manifest differs')
    api = runner
    api.live_change = lambda do |objects|
      env = objects.first.dig('spec', 'template', 'spec', 'containers').first['env']
      env.find { |e| e['name'] == 'JOB_API_DB_PASSWORD' }.dig('valueFrom', 'secretKeyRef')['name'] = 'wrong-secret'
    end
    rejected(api, 'spec differs from source')
    api = runner
    api.live_change = ->(objects) { objects.first.dig('spec', 'template', 'metadata', 'labels').merge!(JobApiFluxOwnership::ORIGIN_LABELS) }
    rejected(api, 'spec differs from source')
  end

  def test_runtime_authentication_and_readiness_gates_are_not_weakened
    api = runner
    api.pod_digest = 'sha256:wrong-image'
    rejected(api, 'running Pod imageID does not match its locked digest')
    api = runner
    api.pod_ready = false
    rejected(api, 'Pod is not Ready')
    %w[200 404 503].each do |code|
      api = runner
      api.http_code = code
      rejected(api, "browser route /auth/me returned HTTP #{code}, expected 401")
    end
  end

  def test_api_errors_fail_closed_for_every_operation
    [:crd, :helmrelease, :json].each do |failure|
      %w[verify install adopt].each do |mode|
        api = runner
        api.api_error = failure
        rejected(api, failure == :json ? 'unexpected' : 'Flux API failed', mode)
        assert api.commands.all? { |cmd| cmd.first == 'kubectl' }
      end
    end
  end

  def test_absence_preserves_manual_verification_and_all_five_fresh_prerequisites
    api = runner
    api.record = nil
    api.native_chart = 'job-api-0.1.0'
    api.origin_labels = {}
    status, _output, error = run_release(api)
    assert_nil status, error
    api = runner
    api.crd_present = false
    api.native_chart = 'job-api-0.1.0'
    api.origin_labels = {}
    api.release_present = api.objects_present = false
    rejected(api, 'authorized manual Helm write reached', 'install')
    prerequisites = api.commands.select { |cmd| cmd.first == 'ruby' }
    assert_equal %w[job-api-database-secret-stage.rb job-api-postgresql-stage.rb job-api-minio-secret-stage.rb minio-job-api-iam-stage.rb keycloak-config-verify.rb],
                 prerequisites.map { |cmd| File.basename(cmd[1]) }
    assert prerequisites.all? { |cmd| cmd.last == 'verify' }
  end
end
