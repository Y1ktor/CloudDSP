/*
 * Keycloak sends this small URL handoff after a required action such as email
 * verification completes:
 *
 *   ?session_state=<opaque value>&iss=<realm issuer>
 *
 * That is deliberately *not* an OAuth authorization response: it contains no
 * authorization `code`, no PKCE `state`, and no browser access token. The SPA
 * must therefore start its ordinary Authorization Code + PKCE redirect after
 * recognizing it. Keeping this parser separate from browser APIs makes the
 * security decision directly unit-testable without a DOM or Keycloak server.
 */

/**
 * Return true only for the Keycloak post-action handoff we intentionally
 * support. A real OAuth response always wins: when it has a `code` or `error`,
 * `oidc.js` must validate/handle that response instead of initiating a second
 * sign-in transaction. The opaque session-state value is never trusted as a
 * token or sent to an API; it only tells us that Keycloak has just returned to
 * this known browser client.
 */
export function isKeycloakPostActionRedirect(search, expectedIssuer) {
    const parameters = new URLSearchParams(search);

    if (!expectedIssuer || parameters.has('code') || parameters.has('error')) {
        return false;
    }

    return isExpectedKeycloakSessionReturn(parameters, expectedIssuer);
}

/**
 * Detect the related cross-tab case. A user can open the Mailpit verification
 * link in a new tab, where `sessionStorage` cannot contain the original PKCE
 * verifier. Keycloak may then finish the earlier browser authorization and
 * include a code, but this tab must *not* exchange that code. It is safe to
 * discard it and initiate a completely new PKCE request only when Keycloak's
 * expected issuer and opaque session marker are both present.
 */
export function isRecoverableCrossTabAuthorizationCallback(search, expectedIssuer) {
    const parameters = new URLSearchParams(search);

    return Boolean(expectedIssuer)
        && parameters.has('code')
        && isExpectedKeycloakSessionReturn(parameters, expectedIssuer);
}

/**
 * Keep the issuer/session validation in one place. The session marker is only
 * a routing hint from Keycloak, never an application credential or token.
 */
function isExpectedKeycloakSessionReturn(parameters, expectedIssuer) {
    return parameters.get('iss') === expectedIssuer
        && Boolean(parameters.get('session_state'));
}
