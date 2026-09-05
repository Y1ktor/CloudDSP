import React from 'react';

/**
 * Authentication is deliberately delegated to Keycloak instead of collecting
 * an email or password in this React application. Clicking the guest button
 * starts an OIDC Authorization Code + PKCE redirect. Keycloak's own screen
 * exposes self-registration, email verification, password recovery, and MFA
 * according to the realm policy configured in Kubernetes.
 */
export default function AuthPanel({
    configured,
    session,
    quota,
    onSignIn,
    onSignOut,
    onOpenHistory,
}) {
    const [isAccountMenuOpen, setIsAccountMenuOpen] = React.useState(false);
    const [busy, setBusy] = React.useState(false);
    const [error, setError] = React.useState('');
    const [clock, setClock] = React.useState(Date.now());
    const accountMenuRef = React.useRef(null);

    React.useEffect(() => {
        const intervalId = window.setInterval(() => setClock(Date.now()), 60_000);
        return () => window.clearInterval(intervalId);
    }, []);

    React.useEffect(() => {
        if (!isAccountMenuOpen) return undefined;

        const closeForOutsidePointer = (event) => {
            if (!accountMenuRef.current?.contains(event.target)) setIsAccountMenuOpen(false);
        };
        const closeForEscape = (event) => {
            if (event.key === 'Escape') setIsAccountMenuOpen(false);
        };
        document.addEventListener('pointerdown', closeForOutsidePointer);
        document.addEventListener('keydown', closeForEscape);
        return () => {
            document.removeEventListener('pointerdown', closeForOutsidePointer);
            document.removeEventListener('keydown', closeForEscape);
        };
    }, [isAccountMenuOpen]);

    React.useEffect(() => {
        if (!session) setIsAccountMenuOpen(false);
    }, [session]);

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

    if (!configured) {
        return (
            <div style={{ color: 'var(--studio-warning)', fontSize: '13px', fontWeight: '600' }}>
                Authentication is not configured for this environment.
            </div>
        );
    }

    if (session) {
        const quotaResetAt = new Date(quota?.resets_at).getTime();
        const remainingMs = Math.max(0, quotaResetAt - clock);
        const resetHours = Number.isFinite(quotaResetAt) ? Math.ceil(remainingMs / 3_600_000) : null;
        const resetLabel = resetHours === null
            ? 'Daily quota refreshes at midnight UTC'
            : resetHours < 1
                ? 'Resets shortly'
                : `Resets in ${resetHours} ${resetHours === 1 ? 'hour' : 'hours'}`;
        const directQuota = quota?.direct_uploads;
        const ytdlpQuota = quota?.ytdlp;

        return (
            <div ref={accountMenuRef} style={accountMenuContainerStyle}>
                <button
                    type="button"
                    style={accountTriggerStyle}
                    aria-haspopup="menu"
                    aria-expanded={isAccountMenuOpen}
                    onClick={() => {
                        setClock(Date.now());
                        setIsAccountMenuOpen((open) => !open);
                    }}
                    title={session.email || session.username}
                >
                    <span>Hi, {session.displayName || 'there'}</span>
                    <svg aria-hidden="true" viewBox="0 0 16 16" width="13" height="13" fill="currentColor" style={{ transform: isAccountMenuOpen ? 'rotate(180deg)' : 'none', transition: 'transform 150ms ease' }}>
                        <path d="M3.1 5.7 8 10.6l4.9-4.9 1.1 1.1L8 12.7 2 6.8l1.1-1.1Z" />
                    </svg>
                </button>

                {isAccountMenuOpen && (
                    <div role="menu" aria-label="Account menu" style={accountMenuStyle}>
                        <div aria-disabled="true" style={quotaSectionStyle}>
                            <div style={quotaHeadingStyle}>Daily quotas <span style={quotaUtcStyle}>UTC</span></div>
                            <div style={quotaRowStyle}>
                                <span>Local</span>
                                <strong>{directQuota ? `${directQuota.used}/${directQuota.limit}` : '—'}</strong>
                            </div>
                            <div style={quotaRowStyle}>
                                <span>URL</span>
                                <strong>{ytdlpQuota ? `${ytdlpQuota.used}/${ytdlpQuota.limit}` : '—'}</strong>
                            </div>
                            <div style={quotaResetStyle}>{resetLabel}</div>
                        </div>
                        <div style={menuDividerStyle} />
                        {onOpenHistory && (
                            <button type="button" role="menuitem" onClick={() => { setIsAccountMenuOpen(false); onOpenHistory(); }} style={menuActionStyle}>
                                <svg aria-hidden="true" viewBox="0 0 24 24" width="15" height="15" fill="currentColor">
                                    <path d="M13 3a9 9 0 1 0 8.94 10H20a7 7 0 1 1-2.05-4.95L15 11h6V5l-1.63 1.63A8.96 8.96 0 0 0 13 3Zm-1 5v5l4.25 2.52 1-1.64L14 12V8h-2Z" />
                                </svg>
                                History
                            </button>
                        )}
                        <button type="button" role="menuitem" onClick={() => { setIsAccountMenuOpen(false); onSignOut(); }} style={{ ...menuActionStyle, ...signOutMenuActionStyle }}>
                            Sign out
                        </button>
                    </div>
                )}
            </div>
        );
    }

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
const accountMenuContainerStyle = { position: 'relative', color: 'var(--studio-text)', fontSize: '13px', fontWeight: '600' };
const accountTriggerStyle = { display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '7px 9px 7px 11px', border: '1px solid var(--studio-border-strong)', borderRadius: '7px', background: 'var(--studio-surface-raised)', color: 'var(--studio-text)', cursor: 'pointer', fontSize: '13px', fontWeight: '700' };
const accountMenuStyle = { position: 'absolute', top: 'calc(100% + 8px)', right: 0, zIndex: 2100, width: '205px', overflow: 'hidden', border: '1px solid var(--studio-border-strong)', borderRadius: '8px', background: 'var(--studio-surface)', boxShadow: '0 14px 30px rgba(39, 61, 83, 0.20)' };
const quotaSectionStyle = { display: 'grid', gap: '7px', padding: '12px 13px 11px', background: 'var(--studio-surface-muted)', color: 'var(--studio-text-muted)', cursor: 'default', userSelect: 'none' };
const quotaHeadingStyle = { color: 'var(--studio-text-secondary)', fontSize: '11px', fontWeight: '800', letterSpacing: '0.06em', textTransform: 'uppercase' };
const quotaUtcStyle = { marginLeft: '4px', color: 'var(--studio-text-muted)', fontSize: '10px', fontWeight: '700', letterSpacing: 0 };
const quotaRowStyle = { display: 'flex', justifyContent: 'space-between', gap: '12px', fontSize: '12px', lineHeight: 1.25 };
const quotaResetStyle = { marginTop: '2px', color: 'var(--studio-text-muted)', fontSize: '11px', fontWeight: '500' };
const menuDividerStyle = { height: '1px', background: 'var(--studio-border)' };
const menuActionStyle = { width: '100%', display: 'flex', alignItems: 'center', gap: '8px', padding: '10px 13px', border: 0, background: 'transparent', color: 'var(--studio-text)', cursor: 'pointer', fontSize: '12px', fontWeight: '700', textAlign: 'left' };
const signOutMenuActionStyle = { color: 'var(--studio-danger)' };
