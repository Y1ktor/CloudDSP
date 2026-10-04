/**
 * Adapt Cognito authentication to the shared workspace session interface while
 * preserving the cloud API and WebSocket contract of using ID tokens.
 */
import {
    confirmSignUp,
    getCurrentSession,
    isCognitoConfigured,
    signIn,
    signOut,
    signUp,
} from '../../auth/cognito.js';
import { createSessionAdapter } from '../../auth/sessionAdapter.js';

// The deployed Cognito API and WebSocket authorizers currently validate ID
// tokens. Retain that backend contract while sharing the workspace with OIDC.
export const auth = createSessionAdapter({
    tokenField: 'idToken',
    isConfigured: isCognitoConfigured,
    restoreSession: getCurrentSession,
    getCurrentSession,
    signIn,
    signUp,
    confirmSignUp,
    signOut,
    signOutErrorMessage: 'Could not complete sign-out.',
});
