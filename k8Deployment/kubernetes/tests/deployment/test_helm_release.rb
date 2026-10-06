require 'minitest/autorun'
require 'json'

require_relative '../../scripts/lib/helm-release'

class HelmReleaseTest < Minitest::Test
  class FakeRelease < HelmRelease
    attr_reader :pod_list_reads

    def initialize(responses)
      super(component: 'demucs', namespace: 'clouddsp-app', release: 'clouddsp-demucs',
            source_files: [], resources: [], pod_selector: 'app.kubernetes.io/name=demucs',
            expected_replicas: 0)
      @responses = responses
      @pod_list_reads = 0
      @clock = 0
    end

    private

    def kubectl(*arguments)
      raise 'unexpected kubectl call' unless arguments == ['get', 'pods', '--selector', @pod_selector, '--output', 'json']

      @pod_list_reads += 1
      @responses.fetch([@pod_list_reads - 1, @responses.length - 1].min)
    end

    def monotonic_time
      @clock
    end

    def sleep(seconds)
      @clock += seconds
    end
  end

  def test_accepts_a_terminating_pod_after_the_deployment_scales_to_zero
    terminating_pod = { 'metadata' => { 'name' => 'demucs-abc', 'deletionTimestamp' => '2026-09-29T00:00:00Z' } }
    release = FakeRelease.new([JSON.generate('items' => [terminating_pod])])

    assert_nil release.send(:wait_for_idle_pods)
    assert_equal 1, release.pod_list_reads
  end

  def test_waits_for_a_non_terminating_pod_even_if_another_is_draining
    terminating_pod = { 'metadata' => { 'name' => 'demucs-old', 'deletionTimestamp' => '2026-09-29T00:00:00Z' } }
    active_pod = { 'metadata' => { 'name' => 'demucs-new' } }
    release = FakeRelease.new([
      JSON.generate('items' => [terminating_pod, active_pod]),
      JSON.generate('items' => [terminating_pod])
    ])

    assert_nil release.send(:wait_for_idle_pods)
    assert_equal 2, release.pod_list_reads
  end

  def test_idle_pod_wait_is_bounded_and_reports_pods_that_do_not_drain
    pod = { 'metadata' => { 'name' => 'demucs-abc' } }
    release = FakeRelease.new([JSON.generate('items' => [pod])])

    error = assert_raises(RuntimeError) { release.send(:wait_for_idle_pods) }

    assert_includes error.message, 'expected no non-terminating demucs Pods, found 1 after scale-to-zero'
    assert_equal HelmRelease::IDLE_POD_DRAIN_TIMEOUT_SECONDS + 1, release.pod_list_reads
  end
end
