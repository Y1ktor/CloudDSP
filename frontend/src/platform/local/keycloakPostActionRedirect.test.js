/**
 * Test recognition of Keycloak required-action and cross-tab authorization
 * returns, including issuer checks and precedence for real OAuth responses.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import {
    isKeycloakPostActionRedirect,
    isRecoverableCrossTabAuthorizationCallback,
} from './keycloakPostActionRedirect.js';

const issuer = 'http://keycloak.localhost:8080/realms/clouddsp';

test('recognizes Keycloak email-verification returns without an OAuth code', () => {
    assert.equal(
        isKeycloakPostActionRedirect(
            `?session_state=opaque-browser-session&iss=${encodeURIComponent(issuer)}`,
            issuer,
        ),
        true,
    );
});

test('does not override a real authorization-code or error response', () => {
    const postActionParameters = `session_state=opaque-browser-session&iss=${encodeURIComponent(issuer)}`;

    assert.equal(isKeycloakPostActionRedirect(`?code=one-time-code&${postActionParameters}`, issuer), false);
    assert.equal(isKeycloakPostActionRedirect(`?error=access_denied&${postActionParameters}`, issuer), false);
});

test('requires the configured Keycloak issuer and a non-empty session marker', () => {
    assert.equal(
        isKeycloakPostActionRedirect('?session_state=opaque-browser-session&iss=http://untrusted.example/realm', issuer),
        false,
    );
    assert.equal(isKeycloakPostActionRedirect(`?iss=${encodeURIComponent(issuer)}`, issuer), false);
});

test('recognizes only an expected Keycloak code return as recoverable cross-tab state loss', () => {
    const expected = `?code=one-time-code&session_state=opaque-browser-session&iss=${encodeURIComponent(issuer)}`;

    assert.equal(isRecoverableCrossTabAuthorizationCallback(expected, issuer), true);
    assert.equal(isRecoverableCrossTabAuthorizationCallback(`?code=one-time-code&iss=${encodeURIComponent(issuer)}`, issuer), false);
    assert.equal(
        isRecoverableCrossTabAuthorizationCallback(
            '?code=one-time-code&session_state=opaque-browser-session&iss=http://untrusted.example/realm',
            issuer,
        ),
        false,
    );
});
