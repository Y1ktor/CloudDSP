require 'minitest/autorun'
require 'minitest/mock'

require_relative '../../scripts/lib/helm-release'

class FrontendBrowserRoutesTest < Minitest::Test
  ROUTES = %w[/architecture /architecture/ /k8 /cost].freeze
  SHELL = '<html><div id="root"></div><script src="/assets/app.js"></script><link href="/assets/app.css"></html>'.freeze
  CSP = "default-src 'self'; object-src 'none'".freeze

  Response = Struct.new(:code, :body, :headers) do
    def [](name)
      headers[name]
    end
  end

  class FakeHttp
    attr_accessor :open_timeout, :read_timeout
    attr_reader :requests

    def initialize(responses)
      @responses = responses
      @requests = []
    end

    def request(request)
      @requests << [request.method, request.path, request['Host']]
      @responses.fetch([request.method, request.path])
    end
  end

  def response(status: '200', body: '', csp: nil)
    Response.new(status, body, csp ? { 'Content-Security-Policy' => csp } : {})
  end

  def browser_responses
    responses = {
      ['GET', '/healthz'] => response,
      ['GET', '/'] => response(body: SHELL, csp: CSP),
      ['HEAD', '/assets/app.js'] => response,
      ['HEAD', '/assets/app.css'] => response
    }
    ROUTES.each { |path| responses[['GET', path]] = response(body: SHELL, csp: CSP) }
    responses
  end

  def release(**options)
    HelmRelease.new(
      component: 'frontend', namespace: 'clouddsp-app', release: 'clouddsp-frontend',
      source_files: [], resources: [], pod_selector: 'app.kubernetes.io/name=clouddsp-frontend',
      health_host: 'clouddsp.localhost', health_path: '/healthz',
      **options
    )
  end

  def verify_http(release, responses)
    http = FakeHttp.new(responses)
    factory = lambda do |host, port, proxy|
      assert_equal '127.0.0.1', host
      assert_equal 8080, port
      assert_nil proxy
      http
    end
    Net::HTTP.stub(:new, factory) { release.send(:verify_http_route) }
    http
  end

  def test_direct_routes_serve_the_same_shell_and_policy_with_assets_available
    http = verify_http(release(browser_shell: true, browser_routes: ROUTES), browser_responses)

    assert_equal [
      ['GET', '/healthz'], ['GET', '/'],
      *ROUTES.map { |path| ['GET', path] },
      ['HEAD', '/assets/app.js'], ['HEAD', '/assets/app.css']
    ], http.requests.map { |method, path, _host| [method, path] }
    assert http.requests.all? { |_method, _path, host| host == 'clouddsp.localhost' }
  end

  def test_redirects_are_rejected_without_following_the_location
    ROUTES.each do |path|
      responses = browser_responses
      responses[['GET', path]] = Response.new('301', '', { 'Location' => "#{path}/" })

      error = assert_raises(RuntimeError) do
        verify_http(release(browser_shell: true, browser_routes: ROUTES), responses)
      end

      assert_includes error.message, "app route #{path} returned HTTP 301, expected 200"
    end
  end

  def test_architecture_directory_forbidden_response_fails_even_when_root_is_healthy
    responses = browser_responses
    responses[['GET', '/architecture/']] = response(status: '403', body: 'directory index forbidden')

    error = assert_raises(RuntimeError) do
      verify_http(release(browser_shell: true, browser_routes: ROUTES), responses)
    end

    assert_includes error.message, 'app route /architecture/ returned HTTP 403, expected 200'
  end

  def test_a_successful_response_must_be_the_same_app_shell
    responses = browser_responses
    responses[['GET', '/k8']] = response(body: '<div id="root"></div>', csp: CSP)

    error = assert_raises(RuntimeError) do
      verify_http(release(browser_shell: true, browser_routes: ROUTES), responses)
    end

    assert_includes error.message, 'app route /k8 does not serve the reviewed app shell'
  end

  def test_route_policy_must_match_the_root_policy
    responses = browser_responses
    responses[['GET', '/cost']] = response(body: SHELL, csp: "default-src *; object-src 'none'")

    error = assert_raises(RuntimeError) do
      verify_http(release(browser_shell: true, browser_routes: ROUTES), responses)
    end

    assert_includes error.message, 'app route /cost has a different CSP from the app shell'
  end

  def test_browser_routes_are_optional_for_existing_shell_checks
    responses = browser_responses.reject { |(_method, path), _response| ROUTES.include?(path) }
    http = verify_http(release(browser_shell: true), responses)

    assert_equal ['/healthz', '/', '/assets/app.js', '/assets/app.css'], http.requests.map { |_method, path, _host| path }
  end

  def test_non_browser_releases_preserve_protected_http_route_checks
    responses = {
      ['GET', '/auth/me'] => response(status: '401'),
      ['GET', '/jobs'] => response(status: '401')
    }
    api = release(health_path: '/auth/me', health_status: '401', additional_http_checks: [['/jobs', '401']])
    http = verify_http(api, responses)

    assert_equal ['/auth/me', '/jobs'], http.requests.map { |_method, path, _host| path }
  end
end
