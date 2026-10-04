/**
 * Adapt Keycloak OIDC authentication to the shared workspace session interface,
 * completing redirects before restoring sessions and using OAuth access tokens.
 */
import {
    beginSignIn,
    completeSignInFromCallback,
    consumeKeycloakPostActionRedirect,
    getCurrentSession,
    isOidcConfigured,
    OIDC_SIGN_IN_RESTART_REQUIRED,
    signOut,
} from './oidc.js';
import { createOidcSessionRestore, createSessionAdapter } from '../../auth/sessionAdapter.js';

// Local resource servers validate the OAuth access token. The public OIDC
// client and its PKCE transaction remain local-profile behavior; Cognito's
// browser SDK is excluded from this build by the Vite platform alias.
export const auth = createSessionAdapter({
    tokenField: 'accessToken',
    isConfigured: isOidcConfigured,
    getCurrentSession,
    restoreSession: createOidcSessionRestore({
        completeSignInFromCallback,
        restartRequired: OIDC_SIGN_IN_RESTART_REQUIRED,
        consumePostActionRedirect: consumeKeycloakPostActionRedirect,
        beginSignIn,
        getCurrentSession,
    }),
    signIn: beginSignIn,
    signOut,
    signOutErrorMessage: 'Signed out locally, but Keycloak logout did not complete.',
});
