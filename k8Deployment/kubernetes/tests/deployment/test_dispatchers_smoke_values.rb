# Protect the two-manifest staging boundary: unknown identity/overrides must
# fail before a partial pause is written; restoration preserves original text.
require 'minitest/autorun'
require 'fileutils'
require 'rbconfig'
require 'tmpdir'
require_relative '../../scripts/gitops/dispatchers-smoke-values'

class DispatchersSmokeValuesTest < Minitest::Test
  def texts
    DispatcherSmokeValues::CONFIG.to_h { |component, config| [component, config.fetch(:manifest).read] }
  end

  def test_both_publishers_pause_and_restore_only_their_reviewed_values_line
    normal = texts
    paused = DispatcherSmokeValues.prepared_changes(normal, 'pause')
    assert_equal %w[dispatcher generic-dispatcher], paused.keys
    paused.each do |component, text|
      expected = YAML.safe_load(normal.fetch(component))
      expected.dig('spec', 'chart', 'spec', 'valuesFiles') << DispatcherSmokeValues::CONFIG.fetch(component).fetch(:pause)
      assert_equal expected, YAML.safe_load(text)
    end
    assert_equal normal, DispatcherSmokeValues.prepared_changes(paused, 'restore')
    assert_equal paused, DispatcherSmokeValues.prepared_changes(paused, 'pause')
  end

  def test_one_invalid_publisher_rejects_the_whole_preparation
    %w[dispatcher generic-dispatcher].each do |component|
      %w[targetNamespace storageNamespace].each do |field|
        input = texts
        input[component] = input.fetch(component).sub("#{field}: clouddsp-app", "#{field}: other")
        assert_raises(RuntimeError) { DispatcherSmokeValues.prepared_changes(input, 'pause') }
      end
      input = texts
      input[component] = input.fetch(component).sub('valuesFiles:', "valuesFiles:\n        - ./other.yaml")
      assert_raises(RuntimeError) { DispatcherSmokeValues.prepared_changes(input, 'pause') }
      input = texts
      input[component] = input.fetch(component) + "  values:\n    replicas: 1\n"
      assert_raises(RuntimeError) { DispatcherSmokeValues.prepared_changes(input, 'pause') }
    end
    assert_raises(KeyError) { DispatcherSmokeValues.prepared_changes({ 'frontend' => texts.fetch('dispatcher') }, 'pause') }
    assert_raises(RuntimeError) { DispatcherSmokeValues.prepared_changes({}, 'pause') }
    assert_raises(RuntimeError) { DispatcherSmokeValues.prepared_changes(texts, 'delete') }
  end
end

# Run the real editor in disposable Git repos. These checks exercise HEAD,
# index/worktree differences and branch selection without touching live Flux.
class DispatchersSmokeValuesGitTest < Minitest::Test
  SCRIPT = 'k8Deployment/kubernetes/scripts/gitops/dispatchers-smoke-values.rb'.freeze
  PATHS_LIBRARY = 'k8Deployment/kubernetes/scripts/lib/paths.rb'.freeze

  def git(repo, *arguments)
    output, error, status = Open3.capture3('git', '-C', repo.to_s, *arguments)
    assert status.success?, "fixture git #{arguments.first} failed: #{error}"
    output.strip
  end

  def manifests(repo)
    DispatcherSmokeValues::CONFIG.to_h do |component, config|
      [component, repo.join(config.fetch(:manifest).relative_path_from(CloudDSPPaths::REPOSITORY_ROOT))]
    end
  end

  def with_repository(branch: 'main', ref: { 'branch' => 'main' }, commit_source: true, source_last: false)
    Dir.mktmpdir('clouddsp-dispatcher-smoke-') do |directory|
      repo = Pathname.new(directory)
      files = [SCRIPT, PATHS_LIBRARY] + DispatcherSmokeValues::CONFIG.values.map do |config|
        config.fetch(:manifest).relative_path_from(CloudDSPPaths::REPOSITORY_ROOT).to_s
      end
      files.each do |file|
        FileUtils.mkdir_p(repo.join(file).parent)
        FileUtils.cp(CloudDSPPaths::REPOSITORY_ROOT.join(file), repo.join(file))
      end
      source = {
        'apiVersion' => 'source.toolkit.fluxcd.io/v1', 'kind' => 'GitRepository',
        'metadata' => { 'name' => 'flux-system', 'namespace' => 'flux-system' },
        'spec' => { 'url' => 'https://github.com/example/CloudDSP-fork.git', 'ref' => ref }
      }
      root = { 'kind' => 'Kustomization', 'metadata' => { 'name' => 'flux-system', 'namespace' => 'flux-system' } }
      documents = source_last ? [root, source] : [source, root]
      source_path = repo.join(DispatcherSmokeValues::SOURCE_MANIFEST)
      FileUtils.mkdir_p(source_path.parent)
      source_path.write(documents.map(&:to_yaml).join)
      git(repo, 'init', '--quiet')
      git(repo, 'symbolic-ref', 'HEAD', "refs/heads/#{branch}")
      git(repo, 'add', '--', *files)
      git(repo, 'add', '--', DispatcherSmokeValues::SOURCE_MANIFEST) if commit_source
      git(repo, '-c', 'user.name=Smoke fixture', '-c', 'user.email=fixture@example.invalid',
          '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'initial smoke fixture')
      yield repo
    end
  end

  def run_editor(repo, mode)
    Open3.capture3(RbConfig.ruby, repo.join(SCRIPT).to_s, mode, chdir: repo.to_s)
  end

  def assert_refusal_without_writes(repo, message)
    paths = manifests(repo)
    before = paths.transform_values(&:read)
    %w[pause restore].each do |mode|
      output, error, status = run_editor(repo, mode)
      refute status.success?
      assert_empty output
      assert_includes error, message
      assert_equal before, paths.transform_values(&:read)
    end
  end

  def test_main_pause_and_restore_change_only_the_reviewed_files_without_committing
    with_repository do |repo|
      paths = manifests(repo)
      before = paths.transform_values(&:read)
      initial_head = git(repo, 'rev-parse', 'HEAD')
      output, error, status = run_editor(repo, 'pause')
      assert status.success?, error
      assert_includes output, 'to "main"'
      paths.each do |component, path|
        assert_equal before.fetch(component).sub(DispatcherSmokeValues.inactive_line(component),
                                                DispatcherSmokeValues.active_line(component)), path.read
      end
      assert_equal paths.values.map { |path| path.relative_path_from(repo).to_s }.sort,
                   git(repo, 'diff', '--name-only').lines.map(&:strip).sort
      _, error, status = run_editor(repo, 'restore')
      assert status.success?, error
      assert_equal before, paths.transform_values(&:read)
      assert_equal '', git(repo, 'status', '--porcelain')
      assert_equal initial_head, git(repo, 'rev-parse', 'HEAD')
    end
  end

  def test_custom_fork_branch_and_reordered_source_documents_work
    with_repository(branch: 'gitops/local', ref: { 'branch' => 'gitops/local' }, source_last: true) do |repo|
      output, error, status = run_editor(repo, 'pause')
      assert status.success?, error
      assert_includes output, 'to "gitops/local"'
      manifests(repo).each do |component, path|
        assert_includes path.read, DispatcherSmokeValues.active_line(component)
      end
    end
  end

  def test_mismatched_branch_cannot_stage_either_manifest
    with_repository(branch: 'feature/unwatched') do |repo|
      assert_refusal_without_writes(repo, 'use the Flux watched branch "main"; current branch is "feature/unwatched"')
    end
  end

  def test_detached_head_cannot_stage_either_manifest
    with_repository do |repo|
      git(repo, 'checkout', '--quiet', '--detach')
      assert_refusal_without_writes(repo, 'detached HEAD is not supported')
    end
  end

  def test_uncommitted_source_change_cannot_bypass_branch_check_even_when_staged
    with_repository(branch: 'feature/unwatched') do |repo|
      source = repo.join(DispatcherSmokeValues::SOURCE_MANIFEST)
      committed_source = source.read
      source.write(committed_source.sub('branch: main', 'branch: feature/unwatched'))
      assert_refusal_without_writes(repo, 'commit or discard Flux Git source changes')
      git(repo, 'add', '--', DispatcherSmokeValues::SOURCE_MANIFEST)
      assert_refusal_without_writes(repo, 'commit or discard Flux Git source changes')
      # A staged change hidden by an inverse worktree edit is still pending
      # and could be published with the smoke commit; reject that state too.
      source.write(committed_source)
      assert_refusal_without_writes(repo, 'commit or discard Flux Git source changes')
    end
  end

  def test_source_must_exist_in_head
    with_repository(commit_source: false) do |repo|
      assert_refusal_without_writes(repo, 'commit the Flux Git source configuration')
    end
  end

  def test_nonbranch_refs_cannot_stage_a_change_flux_would_ignore
    [{}, { 'branch' => '' }, { 'branch' => 123 }, { 'tag' => 'v1.0.0' },
     { 'branch' => 'main', 'tag' => 'v1.0.0' }, { 'branch' => 'main', 'semver' => '1.x' },
     { 'branch' => 'main', 'name' => 'refs/heads/main' }, { 'branch' => 'main', 'commit' => 'a' * 40 }].each do |ref|
      with_repository(ref: ref) do |repo|
        assert_refusal_without_writes(repo, 'Flux Git source must select only a nonempty spec.ref.branch')
      end
    end
  end

  def test_invalid_second_manifest_prevents_partial_cli_write
    with_repository do |repo|
      path = manifests(repo).fetch('generic-dispatcher')
      path.write(path.read.sub('targetNamespace: clouddsp-app', 'targetNamespace: other'))
      assert_refusal_without_writes(repo, 'unexpected dispatcher HelmRelease identity')
    end
  end
end
