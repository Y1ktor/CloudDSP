require 'minitest/autorun'
require 'stringio'

require_relative '../../scripts/stages/keycloak/keycloak-config-verify'

class KeycloakConfigVerifyTest < Minitest::Test
  def setup
    @verifier = KeycloakConfigVerify.new(output: StringIO.new, error: StringIO.new)
  end

  def test_committed_bootstrap_payloads_form_the_reviewed_contract
    desired = @verifier.load_desired

    assert_equal 'clouddsp', desired.fetch(:realm)
    assert_equal true, desired.fetch(:registration).fetch('verifyEmail')
    assert_equal 7 * 24 * 60 * 60, desired.fetch(:session).fetch('ssoSessionMaxLifespan')
    assert_equal 300, desired.fetch(:session).fetch('accessTokenLifespan')
    assert_equal true, desired.fetch(:session).fetch('rememberMe')
    assert_equal 'clouddsp-mailpit-smtp', desired.fetch(:smtp).fetch('host')
    assert_equal ['http://clouddsp.localhost:8080/'], desired.fetch(:react).fetch('redirectUris')
    assert_equal false, desired.fetch(:resource).fetch('standardFlowEnabled')
    assert_equal 'clouddsp-job-api', desired.fetch(:mapper).dig('config', 'included.client.audience')
    assert_equal 'true', desired.fetch(:password_form).dig('config', 'always_set_password_on_register_form')
    refute desired.to_s.include?('KC_BOOTSTRAP_ADMIN_PASSWORD')
  end

  def test_client_comparison_accepts_keycloaks_omitted_false_field
    expected = { 'publicClient' => true, 'authorizationServicesEnabled' => false,
                 'attributes' => { 'pkce.code.challenge.method' => 'S256' } }
    actual = { 'publicClient' => true,
               'attributes' => { 'pkce.code.challenge.method' => 'S256', 'server-generated' => 'ignored' } }

    @verifier.send(:compare_fields, 'React client', actual, expected)
  end

  def test_client_drift_reports_a_field_label_without_values
    expected = { 'redirectUris' => ['http://clouddsp.localhost:8080/'] }
    actual = { 'redirectUris' => ['http://unexpected.example/'] }

    error = assert_raises(RuntimeError) { @verifier.send(:compare_fields, 'React client', actual, expected) }
    assert_equal 'React client redirectUris drifted', error.message
    refute_includes error.message, 'unexpected.example'
  end

  def test_admin_api_failure_does_not_include_response_body
    response = Struct.new(:code, :body).new('500', 'private-token-in-body')
    http = Object.new
    http.define_singleton_method(:request) { |_request| response }

    error = assert_raises(RuntimeError) { @verifier.send(:api, http, '/admin/realms/clouddsp') }
    assert_match(/HTTP 500/, error.message)
    refute_includes error.message, 'private-token-in-body'
  end

  def test_session_policy_accepts_inherited_client_timeouts
    policy = @verifier.load_desired.fetch(:session)
    @verifier.send(:compare_session_policy, policy, { 'attributes' => {} }, policy)
    @verifier.send(:compare_session_policy, policy, { 'attributes' => {
      'client.session.idle.timeout' => '0', 'client.session.max.lifespan' => '0'
    } }, policy)
  end

  def test_shorter_client_timeout_cannot_silently_override_seven_days
    policy = @verifier.load_desired.fetch(:session)
    error = assert_raises(RuntimeError) do
      @verifier.send(:compare_session_policy, policy,
                     { 'attributes' => { 'client.session.idle.timeout' => '1800' } }, policy)
    end
    assert_equal 'React client client.session.idle.timeout must inherit the realm', error.message
    refute_includes error.message, '1800'
  end

  def test_long_lived_access_token_and_short_realm_session_are_detected
    policy = @verifier.load_desired.fetch(:session)
    %w[accessTokenLifespan ssoSessionIdleTimeout ssoSessionMaxLifespan].each do |key|
      drifted = policy.merge(key => 123)
      error = assert_raises(RuntimeError) do
        @verifier.send(:compare_session_policy, drifted, { 'attributes' => {} }, policy)
      end
      assert_equal "realm #{key} drifted", error.message
    end
    error = assert_raises(RuntimeError) do
      @verifier.send(:compare_session_policy, policy,
                     { 'attributes' => { 'access.token.lifespan' => '604800' } }, policy)
    end
    assert_includes error.message, 'five-minute access tokens'
  end
end
