require 'minitest/autorun'
require 'fileutils'
require 'json'
require 'open3'
require 'rbconfig'
require 'tmpdir'

class BuildFrontendImageTest < Minitest::Test
  SCRIPT = File.expand_path('../../scripts/images/build-frontend-image.sh', __dir__)
  IMAGE = 'clouddsp-registry.localhost:5001/frontend:0.6.0-shared-profiles'

  def setup
    @temporary_directory = Dir.mktmpdir('clouddsp-shared-frontend')
    @repository = File.join(@temporary_directory, 'repository')
    @scripts = File.join(@repository, 'k8Deployment/kubernetes/scripts')
    @images = File.join(@scripts, 'images')
    @libraries = File.join(@scripts, 'lib')
    @build_script = File.join(@images, 'build-frontend-image.sh')
    @service = File.join(@repository, 'k8Deployment/kubernetes/services/frontend')
    @shared = File.join(@repository, 'frontend')
    @local = File.join(@repository, 'k8Deployment/.local')
    @bin = File.join(@temporary_directory, 'bin')
    @log = File.join(@temporary_directory, 'commands.jsonl')
    [@scripts, @images, @libraries, @service, @shared, @local, @bin].each { |path| FileUtils.mkdir_p(path) }
    FileUtils.cp(SCRIPT, @build_script)
    FileUtils.cp(File.expand_path('../../scripts/lib/paths.sh', __dir__), File.join(@libraries, 'paths.sh'))
    File.write(File.join(@service, 'Dockerfile'), '')
    %w[package.json package-lock.json index.html csp.js vite.config.js].each do |name|
      File.write(File.join(@shared, name), '')
    end
    %w[src public].each { |name| FileUtils.mkdir_p(File.join(@shared, name)) }
    @configuration = File.join(@local, 'frontend.env.production')
    File.write(@configuration, <<~ENVIRONMENT)
      VITE_OIDC_ISSUER=http://keycloak.localhost:8080/realms/clouddsp
      VITE_OIDC_CLIENT_ID=clouddsp-react
      VITE_OIDC_REDIRECT_URI=http://clouddsp.localhost:8080/
      VITE_OIDC_POST_LOGOUT_REDIRECT_URI=http://clouddsp.localhost:8080/
      VITE_JOB_API_URL=/api
      VITE_OBJECT_STORAGE_URL=http://minio.localhost:8080
      VITE_WEBSOCKET_URL=
      VITE_DEMO_ASSET_ORIGIN=
    ENVIRONMENT

    # Fake Docker and k3d keep this test at the build-wiring boundary. Their
    # argv records prove source/config selection without contacting a daemon,
    # compiling an image, pushing to a registry, or reading real local files.
    fake_command = <<~RUBY
      #!#{RbConfig.ruby}
      require 'json'
      name = File.basename($PROGRAM_NAME)
      File.open(ENV.fetch('CLOUDDSP_FRONTEND_TEST_LOG'), 'a') { |log| log.puts JSON.generate([name, *ARGV]) }
      if name == 'k3d'
        puts 'clouddsp-registry.localhost registry running'
      elsif ARGV.first == 'build' && ENV['CLOUDDSP_FRONTEND_TEST_BUILD_FAIL'] == '1'
        exit 1
      elsif ARGV.first(2) == ['image', 'inspect']
        puts ARGV.include?('{{.Size}}') ? '4096' : 'clouddsp-registry.localhost:5001/frontend@sha256:' + 'a' * 64
      end
    RUBY
    %w[docker k3d].each do |name|
      path = File.join(@bin, name)
      File.write(path, fake_command)
      File.chmod(0o700, path)
    end
  end

  def teardown
    FileUtils.remove_entry(@temporary_directory)
  end

  def run_build(fail_build: false)
    File.write(@log, '')
    output, error, status = Open3.capture3(
      { 'PATH' => "#{@bin}:#{ENV.fetch('PATH', '')}", 'CLOUDDSP_FRONTEND_TEST_LOG' => @log,
        'CLOUDDSP_FRONTEND_TEST_BUILD_FAIL' => fail_build ? '1' : '0' },
      '/bin/bash', @build_script, chdir: @temporary_directory
    )
    commands = File.readlines(@log).map { |line| JSON.parse(line) }
    [status, commands, output, error]
  end

  def build_command(commands)
    commands.find { |command| command.first(2) == %w[docker build] }
  end

  def test_build_uses_shared_source_and_separate_delivery_context_from_any_directory
    status, commands, output, error = run_build

    assert status.success?, error
    build = build_command(commands)
    assert_equal @service, build.last
    assert_equal "frontend=#{@shared}", build.fetch(build.index('--build-context') + 1)
    assert_equal File.join(@service, 'Dockerfile'), build.fetch(build.index('--file') + 1)
    assert_equal IMAGE, build.fetch(build.index('--tag') + 1)
    assert_includes build, 'VITE_OIDC_CLIENT_ID=clouddsp-react'
    assert_includes build, 'VITE_OBJECT_STORAGE_URL=http://minio.localhost:8080'
    assert_equal 8, build.count('--build-arg')
    assert_includes commands, ['docker', 'push', IMAGE]
    assert_includes output, 'frontend@sha256:' + 'a' * 64
  end

  def test_custom_public_api_url_reaches_vite_build_arguments
    File.write(@configuration, File.read(@configuration).sub('VITE_JOB_API_URL=/api', 'VITE_JOB_API_URL=/custom-api'))
    status, commands, _output, error = run_build

    assert status.success?, error
    assert_includes build_command(commands), 'VITE_JOB_API_URL=/custom-api'
  end

  def test_missing_shared_source_stops_before_any_build_or_push
    FileUtils.rm(File.join(@shared, 'package-lock.json'))
    status, commands, _output, error = run_build

    refute status.success?
    assert_includes error, 'Shared frontend build input is missing'
    assert_nil build_command(commands)
    refute commands.any? { |command| command.first(2) == %w[docker push] }
  end

  def test_duplicate_public_configuration_stops_before_build
    File.open(@configuration, 'a') { |file| file.puts 'VITE_OIDC_CLIENT_ID=another-client' }
    status, commands, _output, error = run_build

    refute status.success?
    assert_includes error, 'Expected exactly one VITE_OIDC_CLIENT_ID'
    assert_nil build_command(commands)
  end

  def test_failed_build_does_not_push_an_older_tag
    status, commands = run_build(fail_build: true)

    refute status.success?
    refute_nil build_command(commands)
    refute commands.any? { |command| command.first(2) == %w[docker push] }
  end
end
