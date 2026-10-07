# Cross-check the reviewed source boundaries without contacting a live cluster.
require 'json'
require 'minitest/autorun'
require 'pathname'
require 'yaml'

class ScoreUploadWiringTest < Minitest::Test
  ROOT = Pathname.new(__dir__).join('../..').realpath

  def yaml(path)
    YAML.load_file(ROOT.join(path).to_s)
  end

  def test_only_score_prefix_reaches_dedicated_queue
    topology = yaml('services/rabbitmq/rabbitmq-score-intake-topology-v001-configmap.yaml')
    definition = JSON.parse(topology.fetch('data').fetch('rabbitmq-score-intake-topology-v001.json'))
    binding = definition.fetch('bindings').find { |item| item['destination'] == 'clouddsp.score-intake' }
    assert_equal('clouddsp.source-events', binding.fetch('source'))
    assert_equal('score.upload.created', binding.fetch('routing_key'))
    assert_equal('quorum', definition.fetch('queues').find { |item| item['name'] == 'clouddsp.score-intake' }
                                         .fetch('arguments').fetch('x-queue-type'))
    refute(definition.fetch('bindings').any? { |item| item['routing_key'] == 'source.upload.created' })
  end

  def test_minio_target_and_rule_match_score_queue_without_changing_audio_target
    statefulset = yaml('services/minio/minio-statefulset.yaml')
    env = statefulset.dig('spec', 'template', 'spec', 'containers', 0, 'env').to_h { |item| [item['name'], item] }
    assert_equal('score.upload.created', env.dig('MINIO_NOTIFY_AMQP_ROUTING_KEY_SCORE', 'value'))
    assert_equal('source.upload.created', env.dig('MINIO_NOTIFY_AMQP_ROUTING_KEY_INTAKE', 'value'))
    assert_equal('on', env.dig('MINIO_NOTIFY_AMQP_PUBLISHING_CONFIRMS_SCORE', 'value'))
    job = yaml('services/minio/score/minio-score-notification-bootstrap-v001-job.yaml')
    rule = job.dig('spec', 'template', 'spec', 'initContainers')
              .find { |step| step['name'] == 'add-score-prefix-rule' }.fetch('args')
    assert_equal('score-inputs/', rule.fetch(rule.index('--prefix') + 1))
    assert_includes(rule, 'arn:minio:sqs::SCORE:amqp')
  end

  def test_job_api_policy_allows_only_score_puts
    source = yaml('services/minio/score/minio-job-api-score-uploads-policy-v001-configmap.yaml')
    policy = JSON.parse(source.fetch('data').fetch('score-uploads-policy.json'))
    statement = policy.fetch('Statement').fetch(0)
    assert_equal('s3:PutObject', statement.fetch('Action'))
    assert_equal('arn:aws:s3:::clouddsp-uploads/score-inputs/*', statement.fetch('Resource'))
    refute_includes(policy.to_json, 's3:DeleteObject')
  end

  def test_smoke_job_runs_reviewed_script_and_has_no_worker
    config = yaml('tests/score-upload-smoke/score-upload-smoke-configmap.yaml')
    job = yaml('tests/score-upload-smoke/score-upload-smoke-job.yaml')
    assert_equal(['python3', '/smoke/score_upload_smoke.py'],
                 job.dig('spec', 'template', 'spec', 'containers', 0, 'command'))
    assert_includes(config.fetch('data').fetch('score_upload_smoke.py'), 'score.upload.created')
    assert_equal(false, job.dig('spec', 'template', 'spec', 'automountServiceAccountToken'))
  end
end
