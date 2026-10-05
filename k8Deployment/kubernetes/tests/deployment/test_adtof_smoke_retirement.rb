# The administrator retirement Jobs may remove only reserved test access after
# evidence cleanup. Protect that destructive boundary without live DB/S3 calls.
require 'minitest/autorun'
require 'yaml'
require_relative '../../scripts/lib/paths'

class AdtofSmokeRetirementTest < Minitest::Test
  ROOT = CloudDSPPaths::KUBERNETES_ROOT

  def jobs
    YAML.load_stream(ROOT.join('tests/adtof-worker-smoke/adtof-worker-smoke-retire-jobs.yaml').read).compact
  end

  def test_finite_administrator_jobs_have_no_kubernetes_token_or_unpinned_images
    assert_equal 2, jobs.length
    jobs.each do |job|
      assert_equal 'Job', job['kind']
      assert_equal 'clouddsp-data', job.dig('metadata', 'namespace')
      assert_equal 0, job.dig('spec', 'backoffLimit')
      assert_equal 180, job.dig('spec', 'activeDeadlineSeconds')
      pod = job.dig('spec', 'template', 'spec')
      assert_equal false, pod['automountServiceAccountToken']
      assert_equal true, pod.dig('securityContext', 'runAsNonRoot')
      container = pod['containers'].first
      assert_match(/@sha256:[0-9a-f]{64}\z/, container['image'])
      assert_equal false, container.dig('securityContext', 'allowPrivilegeEscalation')
      assert_equal true, container.dig('securityContext', 'readOnlyRootFilesystem')
    end
  end

  def test_database_refuses_remaining_evidence_and_drops_only_reserved_access
    script = jobs.first.dig('spec', 'template', 'spec', 'containers').first['args'].first
    %w[jobs processing_tasks outbox_events].each { |table| assert_includes script, "EXISTS (SELECT 1 FROM public.#{table}" }
    assert_operator script.index('RAISE EXCEPTION'), :<, script.index('DROP FUNCTION')
    assert_includes script, 'BEGIN;'
    assert_includes script, 'COMMIT;'
    assert_equal %w[clouddsp_adtof_worker_smoke_prepare clouddsp_adtof_worker_smoke_observe clouddsp_adtof_worker_smoke_cleanup], script.scan(/DROP FUNCTION public\.(\w+)/).flatten
    assert_equal ['clouddsp-adtof-worker-smoke'], script.scan(/DROP ROLE "([^"]+)"/).flatten
    refute_match(/\b(?:DELETE\s+FROM|DROP\s+OWNED|CASCADE)\b/, script.lines.reject { |l| l.lstrip.start_with?('#', '--') }.join)
  end

  def test_minio_checks_empty_prefixes_before_removing_exact_identity_and_policy
    script = jobs.last.dig('spec', 'template', 'spec', 'containers').first['args'].first
    assert_includes script, 'for prefix in stems midi'
    assert_includes script, 'if [ -s /mc-config/remaining.json ]; then'
    assert_operator script.index('mc ls --recursive --json'), :<, script.index('mc admin user remove')
    assert_includes script, 'mc admin user remove smoke-admin clouddsp-adtof-worker-smoke'
    assert_includes script, 'mc admin policy remove smoke-admin clouddsp-adtof-worker-smoke-objects-v001'
    refute_match(/mc\s+(?:rm|remove|rb)\b/, script)
    assert_includes script, "trap 'mc alias remove"
  end
end
