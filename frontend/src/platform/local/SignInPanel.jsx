/**
 * Authentication is deliberately delegated to Keycloak instead of collecting
 * an email or password in this React application. Clicking the guest button
 * starts an OIDC Authorization Code + PKCE redirect. Keycloak's own screen
 * exposes self-registration, email verification, password recovery, and MFA
 * according to the realm policy configured in Kubernetes.
 */
import React from 'react';

export default function SignInPanel({ onSignIn }) {
    const [busy, setBusy] = React.useState(false);
    const [error, setError] = React.useState('');

    const beginSignIn = async () => {
        setBusy(true);
        setError('');
        try {
            await onSignIn();
            // Normally the browser is navigating to Keycloak now. Keeping this
            // assignment makes a failed/blocked navigation recoverable instead.
            setBusy(false);
        } catch (requestError) {
            setError(requestError.message || 'Could not start Keycloak sign-in.');
            setBusy(false);
        }
    };

    return (
        <div style={{ display: 'grid', justifyItems: 'end', gap: '5px' }}>
            <button type="button" disabled={busy} onClick={beginSignIn} style={{ ...primaryButtonStyle, ...(busy ? disabledButtonStyle : {}) }}>
                {busy ? 'Opening Keycloak…' : 'Sign in or create account'}
            </button>
            <span style={guestHintStyle}>Registration and email verification happen in Keycloak.</span>
            {error && <span role="status" style={errorStyle}>{error}</span>}
        </div>
    );
}

const primaryButtonStyle = { padding: '8px 13px', borderRadius: '7px', border: '1px solid #1b6a48', background: 'var(--studio-midi)', color: '#fff', cursor: 'pointer', fontSize: '13px', fontWeight: '700', boxShadow: '0 1px 1px rgba(23, 58, 39, 0.14)' };
const disabledButtonStyle = { cursor: 'wait', opacity: 0.65 };
const guestHintStyle = { color: 'var(--studio-text-muted)', fontSize: '10px', fontWeight: '600', textAlign: 'right' };
const errorStyle = { maxWidth: '240px', color: 'var(--studio-danger)', fontSize: '11px', fontWeight: '600', textAlign: 'right' };
