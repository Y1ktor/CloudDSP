/*
 * Local browser authentication is an OpenID Connect public-client flow:
 *
 *   React SPA -> Keycloak authorization endpoint -> React callback
 *             -> Keycloak token endpoint -> sessionStorage
 *
 * The browser never sees a Keycloak administrator credential or client secret.
 * This module uses Authorization Code + S256 PKCE because a JavaScript SPA
 * cannot safely keep a client secret. The resource server will later validate
 * the access token cryptographically; browser-side claim decoding below exists
 * only to display a signed-in user's name and expiry.
 */

import {
    isKeycloakPostActionRedirect,
    isRecoverableCrossTabAuthorizationCallback,
} from './keycloakPostActionRedirect';

// The issuer names one exact Keycloak realm. Removing a trailing slash keeps
// this value comparable with the `issuer` field Keycloak returns from discovery.
const oidcIssuer = import.meta.env.VITE_OIDC_ISSUER?.replace(/\/$/, '');
const oidcClientId = import.meta.env.VITE_OIDC_CLIENT_ID;
const redirectUri = import.meta.env.VITE_OIDC_REDIRECT_URI;
const postLogoutRedirectUri = import.meta.env.VITE_OIDC_POST_LOGOUT_REDIRECT_URI;

const OIDC_TRANSACTION_STORAGE_KEY = 'clouddsp.oidc.transaction.v1';
const OIDC_SESSION_STORAGE_KEY = 'clouddsp.oidc.session.v1';
// A Symbol cannot collide with a normalized session object. It lets App.jsx
// distinguish a safe retry instruction from an authentication error without
// ever treating an unverified response as a signed-in user.
export const OIDC_SIGN_IN_RESTART_REQUIRED = Symbol('oidc-sign-in-restart-required');
const TRANSACTION_MAX_AGE_MS = 10 * 60 * 1_000;
// Refresh slightly before expiry so an API call is not sent with a token that
// expires while the request is in flight.
const TOKEN_EXPIRY_SAFETY_MS = 30 * 1_000;
// Cache one discovery request per page load. It avoids three separate metadata
// requests when the page restores a session, begins login, then later logs out.
let discoveryPromise;

// This is deliberately exported so React can show a clear configuration error
// instead of displaying a sign-in button that cannot redirect anywhere.
export const isOidcConfigured = Boolean(
    oidcIssuer && oidcClientId && redirectUri && postLogoutRedirectUri,
);

/**
 * Stop an authentication operation before it makes a malformed network
 * request. `getCurrentSession` is intentionally more forgiving because a
 * missing configuration should simply mean "no signed-in session" at startup.
 */
function requireConfiguration() {
    if (!isOidcConfigured) {
        throw new Error(
            'OIDC is not configured. Set VITE_OIDC_ISSUER, VITE_OIDC_CLIENT_ID, '
            + 'VITE_OIDC_REDIRECT_URI, and VITE_OIDC_POST_LOGOUT_REDIRECT_URI.',
        );
    }
}

/**
 * Read a small, JSON-encoded per-tab record. This is used for either the short
 * login transaction (`state` + PKCE verifier) or the token session. Returning
 * null lets callers treat unavailable/corrupt browser storage as no record.
 */
function readJsonFromSessionStorage(key) {
    try {
        const value = window.sessionStorage.getItem(key);
        return value ? JSON.parse(value) : null;
    } catch {
        // Private-browsing storage failures and malformed stale values must not
        // prevent a visitor from opening the public portions of the app.
        return null;
    }
}

/**
 * Persist a per-tab authentication record. `sessionStorage` survives the
 * round-trip browser redirect to Keycloak but is cleared when the tab closes,
 * unlike `localStorage`, which would retain tokens beyond the browser session.
 */
function writeJsonToSessionStorage(key, value) {
    try {
        window.sessionStorage.setItem(key, JSON.stringify(value));
    } catch {
        throw new Error('This browser blocked session storage required for secure sign-in.');
    }
}

/**
 * Delete a consumed PKCE transaction or a completed local token session. This
 * deliberately ignores browser-storage cleanup failures: retaining a local
 * record is less harmful than turning a logout screen into an application crash.
 */
function removeFromSessionStorage(key) {
    try {
        window.sessionStorage.removeItem(key);
    } catch {
        // Local cleanup is best-effort when browser storage is unavailable.
    }
}

/**
 * Encode bytes using the URL-safe Base64 alphabet required by PKCE and OAuth
 * parameters. Ordinary Base64 includes `+`, `/`, and padding `=`, which are
 * inconvenient or ambiguous inside query strings without additional escaping.
 */
function base64Url(bytes) {
    let binary = '';
    bytes.forEach((byte) => {
        binary += String.fromCharCode(byte);
    });
    return window.btoa(binary)
        .replace(/\+/g, '-')
        .replace(/\//g, '_')
        .replace(/=+$/g, '');
}

/**
 * Generate cryptographically strong URL-safe text from the browser's Web
 * Crypto random source. It produces the unpredictable OIDC `state` value and
 * PKCE verifier; `Math.random()` would not be suitable for either purpose.
 */
function randomBase64Url(byteLength) {
    const bytes = new Uint8Array(byteLength);
    window.crypto.getRandomValues(bytes);
    return base64Url(bytes);
}

/**
 * Produce the S256 PKCE challenge that is sent to Keycloak. The original
 * verifier remains private in sessionStorage; Keycloak later hashes the
 * verifier supplied to the token endpoint and compares it with this value.
 */
async function sha256Base64Url(value) {
    const digest = await window.crypto.subtle.digest(
        'SHA-256',
        new TextEncoder().encode(value),
    );
    return base64Url(new Uint8Array(digest));
}

/**
 * Decode the JSON claims section of a JWT without verifying its signature.
 * This is only for local UI details such as `preferred_username` and `exp`.
 * The future Job API is still responsible for signature, issuer, audience,
 * expiry, and ownership validation before it authorizes any request.
 */
function decodeJwtPayload(token) {
    const [, encodedPayload] = token.split('.');
    if (!encodedPayload) throw new Error('OIDC returned a malformed JSON Web Token.');

    const base64 = encodedPayload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, '=');
    const binary = window.atob(padded);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
}

/**
 * Read one endpoint URL from Keycloak's realm discovery document and ensure it
 * stays on the configured Keycloak origin. This creates a narrow trust boundary
 * around discovery metadata before the browser redirects or sends a token.
 */
function endpointFromDiscovery(discovery, field) {
    const endpoint = discovery?.[field];
    if (typeof endpoint !== 'string' || !endpoint) {
        throw new Error(`OIDC discovery did not provide ${field}.`);
    }

    const issuerUrl = new URL(oidcIssuer);
    const endpointUrl = new URL(endpoint);
    // This local Keycloak setup publishes all OIDC endpoints under one origin.
    // Restricting discovery to that reviewed origin prevents a bad discovery
    // response from directing passwords/codes/tokens to another host.
    if (endpointUrl.origin !== issuerUrl.origin) {
        throw new Error(`OIDC discovery returned ${field} on an unexpected origin.`);
    }
    return endpoint;
}

/**
 * Fetch the OIDC discovery document for the configured `clouddsp` realm.
 * Keycloak publishes realm-specific endpoint URLs here; the SPA uses them
 * rather than hard-coding `/auth`, `/token`, and `/logout` path conventions.
 */
async function discover() {
    requireConfiguration();
    if (!discoveryPromise) {
        discoveryPromise = (async () => {
            const response = await fetch(`${oidcIssuer}/.well-known/openid-configuration`, {
                headers: { Accept: 'application/json' },
                cache: 'no-store',
                credentials: 'omit',
            });
            if (!response.ok) {
                throw new Error(`OIDC discovery failed (${response.status}).`);
            }
            const metadata = await response.json();
            if (metadata?.issuer !== oidcIssuer) {
                throw new Error('OIDC discovery issuer does not match VITE_OIDC_ISSUER.');
            }
            // Validate every endpoint the SPA will use while the metadata is
            // still untrusted input from the network.
            endpointFromDiscovery(metadata, 'authorization_endpoint');
            endpointFromDiscovery(metadata, 'token_endpoint');
            endpointFromDiscovery(metadata, 'end_session_endpoint');
            return metadata;
        })().catch((error) => {
            // A temporary failed fetch must not poison later sign-in attempts.
            discoveryPromise = undefined;
            throw error;
        });
    }
    return discoveryPromise;
}

/**
 * Remove temporary OIDC and Keycloak post-action values from the current
 * browser history entry. The app stays on the same React route, but a refresh
 * no longer leaks a code to history, attempts a second token exchange, or
 * looks like an incomplete authentication callback after email verification.
 */
function cleanCallbackParameters() {
    const url = new URL(window.location.href);
    let changed = false;
    for (const name of [
        'code',
        'state',
        'error',
        'error_description',
        'kc_action',
        'kc_action_status',
        // Keycloak sends these after its email-verification required action.
        // They describe the browser's Keycloak session, not an OAuth token.
        'iss',
        'session_state',
    ]) {
        if (url.searchParams.has(name)) {
            url.searchParams.delete(name);
            changed = true;
        }
    }
    if (changed) {
        // The authorization code is single-use. Remove it from history so a
        // browser refresh cannot accidentally try to exchange it a second time.
        window.history.replaceState({}, document.title, `${url.pathname}${url.search}${url.hash}`);
    }
}

/**
 * Normalize a successful Keycloak token response into the small session shape
 * used by `App.jsx`. A refresh response may omit `id_token` or `refresh_token`,
 * so the previous value is retained only when Keycloak does not replace it.
 */
function sessionFromTokenResponse(tokenResponse, previousSession = null) {
    if (typeof tokenResponse?.access_token !== 'string' || !tokenResponse.access_token) {
        throw new Error('OIDC token response did not include an access token.');
    }

    const accessClaims = decodeJwtPayload(tokenResponse.access_token);
    const idToken = typeof tokenResponse.id_token === 'string'
        ? tokenResponse.id_token
        : previousSession?.idToken;
    const idClaims = idToken ? decodeJwtPayload(idToken) : {};
    const claims = idClaims.sub ? idClaims : accessClaims;
    const expiresAt = Number(accessClaims.exp) * 1_000;

    if (claims.iss && claims.iss !== oidcIssuer) {
        throw new Error('OIDC token issuer does not match VITE_OIDC_ISSUER.');
    }
    if (!claims.sub || !Number.isFinite(expiresAt)) {
        throw new Error('OIDC token is missing a subject or expiry.');
    }

    const email = typeof claims.email === 'string' ? claims.email : '';
    const preferredName = typeof claims.preferred_username === 'string'
        ? claims.preferred_username.trim()
        : '';
    const emailName = email.includes('@') ? email.split('@')[0] : '';

    return {
        // `sub` is the immutable identity used by APIs. `username` is a
        // readable UI value and must not be treated as an authorization key.
        subject: claims.sub,
        username: preferredName || email || claims.sub,
        displayName: preferredName || emailName || 'there',
        email,
        // APIs and authenticated WebSockets receive the OAuth access token.
        // An ID token describes the login event and is not a bearer API token.
        accessToken: tokenResponse.access_token,
        idToken,
        refreshToken: typeof tokenResponse.refresh_token === 'string'
            ? tokenResponse.refresh_token
            : previousSession?.refreshToken,
        expiresAt,
    };
}

/**
 * Load an existing per-tab token record and reject records that are missing the
 * bearer access token. Detailed expiry handling is intentionally deferred to
 * `getCurrentSession`, which can refresh an otherwise valid session.
 */
function loadStoredSession() {
    const session = readJsonFromSessionStorage(OIDC_SESSION_STORAGE_KEY);
    return session && typeof session.accessToken === 'string' ? session : null;
}

/**
 * Save and return a normalized session. Returning it keeps callback and refresh
 * call sites compact without duplicating persistence logic.
 */
function saveSession(session) {
    writeJsonToSessionStorage(OIDC_SESSION_STORAGE_KEY, session);
    return session;
}

/**
 * POST an OAuth grant to the realm's token endpoint. Both the authorization
 * code grant and refresh-token grant use this same form-encoded protocol. No
 * client secret is sent because `clouddsp-react` is a Keycloak public client.
 */
async function exchangeToken(parameters) {
    const discovery = await discover();
    const response = await fetch(endpointFromDiscovery(discovery, 'token_endpoint'), {
        method: 'POST',
        headers: {
            Accept: 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams(parameters),
        cache: 'no-store',
        credentials: 'omit',
    });
    if (!response.ok) {
        // Do not surface server response text: it can contain provider-specific
        // detail and should never be confused with a safe user-facing message.
        throw new Error(`OIDC token request failed (${response.status}).`);
    }
    return response.json();
}

/**
 * Trade the current refresh token for fresh short-lived tokens when the access
 * token is near expiry. If Keycloak rejects the refresh token, discard the
 * local record so the next protected operation asks the user to sign in again.
 */
async function refreshStoredSession(session) {
    if (!session?.refreshToken) return null;
    try {
        const tokenResponse = await exchangeToken({
            grant_type: 'refresh_token',
            client_id: oidcClientId,
            refresh_token: session.refreshToken,
        });
        return saveSession(sessionFromTokenResponse(tokenResponse, session));
    } catch {
        // A revoked/expired refresh token means the next protected action must
        // start a fresh authorization redirect. Do not retain stale tokens.
        removeFromSessionStorage(OIDC_SESSION_STORAGE_KEY);
        return null;
    }
}

/**
 * Return an immediately usable session for React. It either reuses an access
 * token with at least 30 seconds remaining, refreshes one near expiry, or
 * returns null when no valid per-tab Keycloak session exists.
 */
export async function getCurrentSession() {
    if (!isOidcConfigured) return null;
    const session = loadStoredSession();
    if (!session) return null;
    if (Number(session.expiresAt) > Date.now() + TOKEN_EXPIRY_SAFETY_MS) return session;
    return refreshStoredSession(session);
}

/**
 * Start browser sign-in. This is the only function that redirects away from
 * React: it discovers the realm endpoints, creates a fresh state/verifier,
 * stores them per-tab, and navigates to Keycloak's authorization endpoint.
 */
export async function beginSignIn() {
    requireConfiguration();
    const discovery = await discover();
    const state = randomBase64Url(32);
    // RFC 7636 permits 43–128 characters. 64 random bytes encode to an
    // 86-character verifier, comfortably inside that boundary.
    const codeVerifier = randomBase64Url(64);
    const codeChallenge = await sha256Base64Url(codeVerifier);

    writeJsonToSessionStorage(OIDC_TRANSACTION_STORAGE_KEY, {
        state,
        codeVerifier,
        createdAt: Date.now(),
    });

    const authorizationUrl = new URL(endpointFromDiscovery(discovery, 'authorization_endpoint'));
    authorizationUrl.search = new URLSearchParams({
        client_id: oidcClientId,
        redirect_uri: redirectUri,
        response_type: 'code',
        scope: 'openid profile email',
        state,
        code_challenge: codeChallenge,
        code_challenge_method: 'S256',
    }).toString();

    window.location.assign(authorizationUrl.toString());
}

/**
 * Finish an authorization response after Keycloak redirects the browser back
 * to `VITE_OIDC_REDIRECT_URI`. It validates `state` before using the code,
 * then submits the original PKCE verifier to obtain tokens for this same tab.
 */
export async function completeSignInFromCallback() {
    const parameters = new URLSearchParams(window.location.search);
    const authorizationCode = parameters.get('code');
    const returnedState = parameters.get('state');
    const returnedError = parameters.get('error');

    if (!authorizationCode && !returnedError) return null;

    const transaction = readJsonFromSessionStorage(OIDC_TRANSACTION_STORAGE_KEY);
    // Evaluate the recovery condition before URL cleanup removes the opaque
    // Keycloak routing values that identify this cross-tab return.
    const crossTabRecoveryRequired = !transaction
        && isRecoverableCrossTabAuthorizationCallback(window.location.search, oidcIssuer);
    removeFromSessionStorage(OIDC_TRANSACTION_STORAGE_KEY);
    cleanCallbackParameters();

    if (returnedError) {
        // Keycloak can return access_denied when a user cancels login or
        // registration. The code deliberately does not render an untrusted
        // provider error_description in the application UI.
        throw new Error('Keycloak sign-in was cancelled or denied.');
    }
    if (!returnedState || !transaction || returnedState !== transaction.state) {
        // sessionStorage is intentionally scoped to one browser tab. If a
        // Mailpit email-verification link opened in a second tab, this tab has
        // no original PKCE verifier and cannot safely exchange Keycloak's code.
        // Discard it and let React begin a brand-new PKCE transaction instead.
        // A present-but-mismatched transaction remains a hard state-validation
        // failure, preserving the normal OAuth CSRF protection boundary.
        if (crossTabRecoveryRequired) {
            return OIDC_SIGN_IN_RESTART_REQUIRED;
        }
        throw new Error('OIDC callback state did not match the sign-in request.');
    }
    if (Date.now() - Number(transaction.createdAt) > TRANSACTION_MAX_AGE_MS) {
        throw new Error('The OIDC sign-in request expired. Start sign-in again.');
    }

    const tokenResponse = await exchangeToken({
        grant_type: 'authorization_code',
        client_id: oidcClientId,
        code: authorizationCode,
        redirect_uri: redirectUri,
        code_verifier: transaction.codeVerifier,
    });
    return saveSession(sessionFromTokenResponse(tokenResponse));
}

/**
 * Consume Keycloak's return URL after a required action such as email
 * verification. Unlike an OAuth callback it has no code to exchange, so this
 * function only verifies the expected issuer, clears the harmless routing
 * markers, and reports that React should begin a fresh PKCE sign-in redirect.
 *
 * The `session_state` query value is intentionally never persisted or used as
 * a credential. `beginSignIn` creates a new unpredictable PKCE transaction;
 * Keycloak then reuses its browser SSO session to return a real code safely.
 */
export function consumeKeycloakPostActionRedirect() {
    if (!isKeycloakPostActionRedirect(window.location.search, oidcIssuer)) {
        return false;
    }

    cleanCallbackParameters();
    return true;
}

/**
 * End both layers of sign-in: clear this tab's local tokens first, then ask
 * Keycloak to end its own SSO browser session and redirect to the registered
 * post-logout URL. Clearing local state first is intentional fail-safe behavior.
 */
export async function signOut() {
    const session = loadStoredSession();
    removeFromSessionStorage(OIDC_SESSION_STORAGE_KEY);
    removeFromSessionStorage(OIDC_TRANSACTION_STORAGE_KEY);
    if (!isOidcConfigured) return;

    try {
        const discovery = await discover();
        const logoutUrl = new URL(endpointFromDiscovery(discovery, 'end_session_endpoint'));
        logoutUrl.search = new URLSearchParams({
            client_id: oidcClientId,
            post_logout_redirect_uri: postLogoutRedirectUri,
            // Keycloak accepts an expired ID token as a session hint. It is
            // optional because an interrupted token response may not include it.
            ...(session?.idToken ? { id_token_hint: session.idToken } : {}),
        }).toString();
        window.location.assign(logoutUrl.toString());
    } catch (error) {
        // Local sign-out is already complete. Re-throw only to let App explain
        // that the Keycloak SSO session could not be ended in this browser.
        throw new Error(error.message || 'Could not end the Keycloak session.');
    }
}
