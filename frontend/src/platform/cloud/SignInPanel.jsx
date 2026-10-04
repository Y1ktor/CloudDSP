/**
 * A dismissible authentication dialog. Verification identity is deliberately
 * owned by App/localStorage rather than this component, so closing the dialog
 * never abandons a confirmation email that Cognito has already sent.
 */
import React from 'react';

const DISPLAY_NAME_PATTERN = /^[A-Za-z0-9_.-]{3,32}$/;
const PASSWORD_SYMBOL_PATTERN = /[^A-Za-z0-9\s]/;

function passwordIsValid(password) {
    return password.length >= 8 && PASSWORD_SYMBOL_PATTERN.test(password);
}

export default function SignInPanel({
    pendingVerification,
    onSignIn,
    onSignUp,
    onConfirmSignUp,
    onDialogOpenChange,
}) {
    const [isOpen, setIsOpen] = React.useState(false);
    const [mode, setMode] = React.useState('sign-in');
    const [email, setEmail] = React.useState('');
    const [displayName, setDisplayName] = React.useState('');
    const [password, setPassword] = React.useState('');
    const [code, setCode] = React.useState('');
    const [message, setMessage] = React.useState('');
    const [error, setError] = React.useState('');
    const [busy, setBusy] = React.useState(false);
    const openDialog = (nextMode = pendingVerification ? 'confirm' : 'sign-in') => {
        setError('');
        setMessage('');
        setCode('');
        if (nextMode === 'confirm' && pendingVerification) {
            setEmail(pendingVerification.email);
            setDisplayName(pendingVerification.displayName || '');
        }
        setMode(nextMode);
        setIsOpen(true);
        onDialogOpenChange?.(true);
    };

    const closeDialog = () => {
        if (!busy) {
            setIsOpen(false);
            onDialogOpenChange?.(false);
        }
    };

    const submit = async (event) => {
        event.preventDefault();
        setError('');
        setMessage('');

        if (mode === 'sign-up') {
            const normalisedName = displayName.trim();
            if (!DISPLAY_NAME_PATTERN.test(normalisedName)) {
                setError('Username must be 3–32 characters: letters, numbers, dots, dashes, or underscores.');
                return;
            }
            if (!passwordIsValid(password)) {
                setError('Password must have at least 8 characters and 1 special symbol.');
                return;
            }
        }

        setBusy(true);
        try {
            if (mode === 'sign-in') {
                await onSignIn(email, password);
                setIsOpen(false);
                onDialogOpenChange?.(false);
                return;
            }
            if (mode === 'sign-up') {
                const result = await onSignUp(email, password, displayName);
                if (result.confirmed) {
                    setMode('sign-in');
                    setMessage('Account created. Sign in to continue.');
                } else {
                    setEmail(email.trim());
                    setPassword('');
                    setMode('confirm');
                    setMessage('We sent a verification code to your email.');
                }
                return;
            }

            await onConfirmSignUp(pendingVerification?.email || email, code);
            setMode('sign-in');
            setCode('');
            setMessage('Email verified. Sign in to continue.');
        } catch (requestError) {
            setError(requestError.message || 'Authentication failed.');
        } finally {
            setBusy(false);
        }
    };

    return (
        <>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                {pendingVerification && (
                    <button type="button" onClick={() => openDialog('confirm')} style={verifyButtonStyle}>
                        Verify email
                    </button>
                )}
                <button type="button" onClick={() => openDialog('sign-in')} style={primaryButtonStyle}>
                    Sign in
                </button>
            </div>

            {isOpen && (
                <div role="presentation" style={overlayStyle} onMouseDown={closeDialog}>
                    <section role="dialog" aria-modal="true" aria-labelledby="auth-dialog-title" style={dialogStyle} onMouseDown={(event) => event.stopPropagation()}>
                        <button type="button" onClick={closeDialog} style={closeButtonStyle} aria-label="Close authentication dialog">×</button>
                        <div style={eyebrowStyle}>CloudDSP account</div>
                        <h2 id="auth-dialog-title" style={titleStyle}>
                            {mode === 'sign-in' ? 'Welcome back' : mode === 'sign-up' ? 'Create your workspace' : 'Verify your email'}
                        </h2>
                        <p style={descriptionStyle}>
                            {mode === 'sign-in' && 'Sign in to manage jobs, recover projects, and keep your workspace private.'}
                            {mode === 'sign-up' && 'Create a profile to save your projects and access them from this browser.'}
                            {mode === 'confirm' && 'Your pending registration is saved. Enter the code from your email whenever you are ready.'}
                        </p>

                        <form onSubmit={submit} style={formStyle}>
                            {mode === 'sign-up' && (
                                <label style={fieldStyle}>
                                    <span style={labelStyle}>Username</span>
                                    <input
                                        type="text"
                                        required
                                        autoComplete="username"
                                        value={displayName}
                                        onChange={(event) => setDisplayName(event.target.value)}
                                        placeholder="e.g. madeline.audio"
                                        style={inputStyle}
                                    />
                                    <span style={hintStyle}>Used only as your CloudDSP display name.</span>
                                </label>
                            )}

                            {mode !== 'confirm' ? (
                                <label style={fieldStyle}>
                                    <span style={labelStyle}>Email</span>
                                    <input
                                        type="email"
                                        required
                                        autoComplete="email"
                                        value={email}
                                        onChange={(event) => setEmail(event.target.value)}
                                        placeholder="you@example.com"
                                        style={inputStyle}
                                    />
                                </label>
                            ) : (
                                <div style={verificationAddressStyle}>
                                    <span style={labelStyle}>Verification code sent to</span>
                                    <strong>{pendingVerification?.email || email}</strong>
                                </div>
                            )}

                            {mode !== 'confirm' ? (
                                <label style={fieldStyle}>
                                    <span style={labelStyle}>Password</span>
                                    <input
                                        type="password"
                                        required
                                        minLength="8"
                                        autoComplete={mode === 'sign-up' ? 'new-password' : 'current-password'}
                                        value={password}
                                        onChange={(event) => setPassword(event.target.value)}
                                        placeholder="••••••••"
                                        style={inputStyle}
                                    />
                                    {mode === 'sign-up' && <span style={hintStyle}>At least 8 characters and 1 special symbol.</span>}
                                </label>
                            ) : (
                                <label style={fieldStyle}>
                                    <span style={labelStyle}>Verification code</span>
                                    <input
                                        type="text"
                                        required
                                        inputMode="numeric"
                                        autoComplete="one-time-code"
                                        value={code}
                                        onChange={(event) => setCode(event.target.value)}
                                        placeholder="Enter the code"
                                        style={inputStyle}
                                    />
                                </label>
                            )}

                            {(error || message) && <div role="status" style={{ ...noticeStyle, ...(error ? errorNoticeStyle : successNoticeStyle) }}>{error || message}</div>}
                            <button disabled={busy} type="submit" style={{ ...primaryButtonStyle, ...submitButtonStyle, ...(busy ? disabledButtonStyle : {}) }}>
                                {busy ? 'Please wait…' : mode === 'sign-in' ? 'Sign in' : mode === 'sign-up' ? 'Create account' : 'Verify email'}
                            </button>
                        </form>

                        <div style={switcherStyle}>
                            {mode === 'sign-in' && <><span>New to CloudDSP?</span><button type="button" onClick={() => { setError(''); setMessage(''); setMode('sign-up'); }} style={linkButtonStyle}>Create an account</button></>}
                            {mode === 'sign-up' && <><span>Already have an account?</span><button type="button" onClick={() => { setError(''); setMessage(''); setMode('sign-in'); }} style={linkButtonStyle}>Sign in</button></>}
                            {mode === 'confirm' && <button type="button" onClick={() => { setError(''); setMessage(''); setMode('sign-in'); }} style={linkButtonStyle}>Sign in to another account</button>}
                        </div>
                    </section>
                </div>
            )}
        </>
    );
}

const primaryButtonStyle = { padding: '8px 13px', borderRadius: '7px', border: '1px solid #1b6a48', background: 'var(--studio-midi)', color: '#fff', cursor: 'pointer', fontSize: '13px', fontWeight: '700', boxShadow: '0 1px 1px rgba(23, 58, 39, 0.14)' };
const verifyButtonStyle = { padding: '7px 10px', borderRadius: '6px', border: '1px solid #d7a33c', background: 'var(--studio-warning-soft)', color: '#80510a', cursor: 'pointer', fontSize: '12px', fontWeight: '700' };
const overlayStyle = { position: 'fixed', inset: 0, zIndex: 2000, display: 'grid', placeItems: 'center', padding: '24px', background: 'rgba(36, 54, 73, 0.36)', backdropFilter: 'blur(7px)' };
const dialogStyle = { position: 'relative', boxSizing: 'border-box', width: 'min(100%, 430px)', padding: '34px', border: '1px solid var(--studio-border-strong)', borderRadius: '14px', background: 'linear-gradient(145deg, var(--studio-surface), var(--studio-surface-raised))', color: 'var(--studio-text)', boxShadow: '0 24px 70px rgba(39, 61, 83, 0.28)' };
const closeButtonStyle = { position: 'absolute', top: '13px', right: '15px', width: '28px', height: '28px', border: '1px solid transparent', borderRadius: '50%', background: 'transparent', color: 'var(--studio-text-secondary)', cursor: 'pointer', fontSize: '25px', lineHeight: 1 };
const eyebrowStyle = { color: 'var(--studio-midi)', fontSize: '11px', fontWeight: '800', letterSpacing: '0.13em', textTransform: 'uppercase' };
const titleStyle = { margin: '8px 0 9px', color: 'var(--studio-text)', fontSize: '27px', letterSpacing: '-0.035em' };
const descriptionStyle = { margin: '0 0 25px', color: 'var(--studio-text-secondary)', fontSize: '14px', lineHeight: 1.55 };
const formStyle = { display: 'grid', gap: '16px' };
const fieldStyle = { display: 'grid', gap: '7px' };
const labelStyle = { color: 'var(--studio-text)', fontSize: '12px', fontWeight: '700' };
const inputStyle = { boxSizing: 'border-box', width: '100%', padding: '11px 12px', borderRadius: '7px', border: '1px solid var(--studio-border-strong)', outline: 'none', background: 'var(--studio-page)', color: 'var(--studio-text)', fontSize: '14px', boxShadow: 'inset 0 1px 1px rgba(32, 50, 70, 0.05)' };
const hintStyle = { color: 'var(--studio-text-muted)', fontSize: '11px', lineHeight: 1.35 };
const verificationAddressStyle = { display: 'grid', gap: '4px', padding: '12px', borderRadius: '7px', border: '1px solid var(--studio-border)', background: 'var(--studio-surface-sunken)', color: 'var(--studio-text-secondary)', fontSize: '13px', overflowWrap: 'anywhere' };
const noticeStyle = { padding: '10px 11px', borderRadius: '7px', fontSize: '12px', lineHeight: 1.4 };
const errorNoticeStyle = { background: 'var(--studio-danger-soft)', border: '1px solid #dfa3aa', color: '#8e2632' };
const successNoticeStyle = { background: 'var(--studio-midi-soft)', border: '1px solid #8fc9a8', color: '#176441' };
const submitButtonStyle = { width: '100%', marginTop: '3px', padding: '11px 13px' };
const disabledButtonStyle = { cursor: 'wait', opacity: 0.65 };
const switcherStyle = { display: 'flex', justifyContent: 'center', flexWrap: 'wrap', gap: '6px', marginTop: '22px', color: 'var(--studio-text-secondary)', fontSize: '12px' };
const linkButtonStyle = { padding: 0, border: 0, background: 'transparent', color: 'var(--studio-accent)', cursor: 'pointer', fontSize: '12px', fontWeight: '700' };
