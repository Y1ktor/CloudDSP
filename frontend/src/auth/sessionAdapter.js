/**
 * One workspace contract for independently configured identity providers.
 * The selected adapter supplies the token field its resource servers expect;
 * HTTP requests and WebSockets must use the same accessor rather than guessing
 * between an OIDC identity assertion and an OAuth access credential.
 */
export function createSessionAdapter({ tokenField, ...operations }) {
    if (!['idToken', 'accessToken'].includes(tokenField)) {
        throw new Error('Authentication adapter must declare its API token contract.');
    }
    return Object.freeze({
        ...operations,
        getBearerToken(session) {
            const token = session?.[tokenField];
            if (typeof token !== 'string' || !token) {
                throw new Error('Your session has expired. Sign in again to continue.');
            }
            return token;
        },
    });
}

/**
 * Complete Keycloak redirects before reading a cached session. A verification
 * email can return in another tab, without that tab's original PKCE verifier;
 * restart a fresh authorization request instead of accepting its unverified
 * code. Dependencies are explicit so these security paths can be tested without
 * storing credentials or contacting the identity provider.
 */
export function createOidcSessionRestore({
    completeSignInFromCallback,
    restartRequired,
    consumePostActionRedirect,
    beginSignIn,
    getCurrentSession,
}) {
    return async (onStatusChange = () => {}) => {
        const callbackSession = await completeSignInFromCallback();
        if (callbackSession === restartRequired) {
            onStatusChange('Email verified. Completing sign-in…');
            await beginSignIn();
            return null;
        }
        if (callbackSession) return callbackSession;
        if (consumePostActionRedirect()) {
            onStatusChange('Email verified. Completing sign-in…');
            await beginSignIn();
            return null;
        }
        return getCurrentSession();
    };
}
