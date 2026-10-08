#!/usr/bin/env ruby
# Exercise the real React client's PKCE login and refresh lifetimes using one
# disposable, email-verified identity. Existing users/clients are never edited.
# Secrets, cookies, codes and tokens remain in memory and never reach logs.
require 'base64'
require 'cgi'
require 'digest'
require 'json'
require 'net/http'
require 'securerandom'
require 'uri'
require_relative '../../scripts/stages/keycloak/keycloak-config-verify'

class KeycloakSessionLifetimeSmoke
  REALM = '/realms/clouddsp'.freeze
  REDIRECT = 'http://clouddsp.localhost:8080/'.freeze

  def run
    @http = Net::HTTP.new('127.0.0.1', 8080, nil)
    @http.open_timeout = 8
    @http.read_timeout = 15
    @cookies = {}
    @http.start
    verifier = KeycloakConfigVerify.new
    expected = verifier.load_desired.fetch(:session)
    credentials = verifier.send(:admin_credentials)
    admin = token('/realms/master', {
      'grant_type' => 'password', 'client_id' => 'admin-cli',
      'username' => credentials.fetch('KC_BOOTSTRAP_ADMIN_USERNAME'),
      'password' => credentials.fetch('KC_BOOTSTRAP_ADMIN_PASSWORD')
    }).fetch('access_token')
    @admin_headers = { 'Authorization' => "Bearer #{admin}" }
    username = "keycloak-session-smoke-#{SecureRandom.hex(6)}"
    password = SecureRandom.urlsafe_base64(32)
    created = request('test identity creation', '/admin/realms/clouddsp/users', method: 'POST', statuses: [201],
      headers: @admin_headers, payload: { username: username, email: "#{username}@clouddsp.test",
        firstName: 'Session', lastName: 'Smoke', enabled: true, emailVerified: true,
        credentials: [{ type: 'password', temporary: false, value: password }] })
    created_id = URI(created['location']).path.split('/').last
    raise 'test identity ID missing' unless created_id&.match?(/\A[0-9a-f-]{36}\z/)
    @user_id = created_id

    # Use the unchanged production browser client, including its required S256
    # challenge. No password-grant test client or grant-policy relaxation.
    verifier_text = SecureRandom.urlsafe_base64(48)
    challenge = Base64.urlsafe_encode64(Digest::SHA256.digest(verifier_text), padding: false)
    state = SecureRandom.hex(16)
    query = URI.encode_www_form(client_id: 'clouddsp-react', response_type: 'code', redirect_uri: REDIRECT,
      scope: 'openid profile email', state: state, code_challenge: challenge, code_challenge_method: 'S256')
    form = request('PKCE login form', "#{REALM}/protocol/openid-connect/auth?#{query}", cookies: true)
    raise 'Remember me checkbox missing' unless form.body.match?(/name=["']rememberMe["']/)
    tag = form.body.scan(/<form\b[^>]*>/).find { |value| value.include?('kc-form-login') }
    action = tag&.match(/\baction=["']([^"']+)["']/)&.captures&.first
    action_uri = URI(CGI.unescapeHTML(action || ''))
    raise 'unexpected login destination' unless action_uri.host == 'keycloak.localhost' &&
      action_uri.path == "#{REALM}/login-actions/authenticate"
    login = request('test PKCE login', action_uri.request_uri, method: 'POST', statuses: [302, 303], cookies: true,
      form: { username: username, password: password, rememberMe: 'on', credentialId: '' })
    callback = URI(login['location'])
    raise 'unexpected callback' unless callback.scheme == 'http' && callback.host == 'clouddsp.localhost' && callback.path == '/'
    params = URI.decode_www_form(callback.query || '').to_h
    raise 'PKCE state mismatch' unless params['state'] == state && params['code']
    identity_cookie = login.get_fields('set-cookie')&.find { |cookie| cookie.start_with?('KEYCLOAK_IDENTITY=') }
    cookie_age = identity_cookie&.match(/Max-Age=(\d+)/i)&.captures&.first.to_i
    raise 'Remember me cookie lifetime mismatch' unless cookie_age == expected.fetch('ssoSessionMaxLifespanRememberMe')
    issued = token(REALM, grant_type: 'authorization_code', client_id: 'clouddsp-react',
      code: params.fetch('code'), redirect_uri: REDIRECT, code_verifier: verifier_text)
    assert_lifetimes(issued, expected)
    refreshed = token(REALM, grant_type: 'refresh_token', client_id: 'clouddsp-react', refresh_token: issued.fetch('refresh_token'))
    assert_lifetimes(refreshed, expected)
    identity = request('refreshed API identity', '/auth/me', host: 'clouddsp.localhost',
      headers: { 'Authorization' => "Bearer #{refreshed.fetch('access_token')}" })
    raise 'refreshed API owner mismatch' unless JSON.parse(identity.body) == { 'subject' => @user_id }
    cleanup
    # Deleting this temporary identity must revoke its longer refresh session.
    request('revoked refresh rejection', "#{REALM}/protocol/openid-connect/token", method: 'POST', statuses: [400],
      form: { grant_type: 'refresh_token', client_id: 'clouddsp-react', refresh_token: refreshed.fetch('refresh_token') })
    puts 'Session lifetime smoke passed: access 300s, refresh approximately 604800s, persistent SSO cookie 604800s; refresh, API owner, revocation and cleanup verified.'
    0
  rescue StandardError => error
    # Exceptions may carry request/response data. Only fixed operation errors
    # raised by this adapter are safe; other failures expose their type alone.
    puts "Session lifetime smoke failed (#{error.class})"
    1
  ensure
    begin
      cleanup if @user_id
    rescue StandardError => error
      # A cleanup failure must remain a failed smoke, with no token-bearing
      # exception trace. The test-only username prefix identifies leftovers.
      warn "Session lifetime test identity cleanup failed (#{error.class})"
      return 1
    ensure
      @http&.finish if @http&.started?
    end
  end

  private

  def assert_lifetimes(value, expected)
    raise 'access lifetime mismatch' unless value['expires_in'] == expected.fetch('accessTokenLifespan')
    remaining = value['refresh_expires_in']
    maximum = expected.fetch('ssoSessionMaxLifespan')
    raise 'refresh lifetime mismatch' unless remaining.is_a?(Integer) && (maximum - 120..maximum).cover?(remaining)
  end

  def cleanup
    request('test identity cleanup', "/admin/realms/clouddsp/users/#{@user_id}", method: 'DELETE',
      statuses: [204], headers: @admin_headers)
    @user_id = nil
  end

  def token(realm, form)
    JSON.parse(request('token exchange', "#{realm}/protocol/openid-connect/token", method: 'POST', form: form).body)
  end

  def request(operation, path, method: 'GET', statuses: [200], headers: {}, payload: nil, form: nil,
              host: 'keycloak.localhost', cookies: false)
    req = Net::HTTP.const_get(method.capitalize).new(path)
    req['Host'] = "#{host}:8080"
    headers.each { |key, value| req[key] = value }
    req['Cookie'] = @cookies.map { |key, value| "#{key}=#{value}" }.join('; ') if cookies
    req.set_form_data(form) if form
    if payload
      req['Content-Type'] = 'application/json'
      req.body = JSON.generate(payload)
    end
    response = @http.request(req)
    raise "#{operation} HTTP failure" unless statuses.include?(response.code.to_i)
    if cookies
      response.get_fields('set-cookie')&.each do |cookie|
        key, value = cookie.split(';').first.split('=', 2)
        @cookies[key] = value
      end
    end
    response
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: session-lifetime.rb verify' unless ARGV == ['verify']
  exit KeycloakSessionLifetimeSmoke.new.run
end
