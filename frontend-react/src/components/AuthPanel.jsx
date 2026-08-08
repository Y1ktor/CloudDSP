import React from 'react';

export default function AuthPanel({ configured, session, onSignIn, onSignUp, onConfirmSignUp, onSignOut, onOpenHistory }) {
    const [mode, setMode] = React.useState('sign-in');
    const [email, setEmail] = React.useState('');
    const [password, setPassword] = React.useState('');
    const [code, setCode] = React.useState('');
    const [message, setMessage] = React.useState('');
    const [error, setError] = React.useState('');
    const [busy, setBusy] = React.useState(false);

    const submit = async (event) => {
        event.preventDefault();
        setBusy(true);
        setError('');
        setMessage('');
        try {
            if (mode === 'sign-in') {
                await onSignIn(email, password);
            } else if (mode === 'sign-up') {
                const result = await onSignUp(email, password);
                if (result.confirmed) {
                    setMode('sign-in');
                    setMessage('Account created. Sign in to continue.');
                } else {
                    setMode('confirm');
                    setMessage('Check your email for the verification code.');
                }
            } else {
                await onConfirmSignUp(email, code);
                setMode('sign-in');
                setMessage('Email verified. Sign in to continue.');
            }
        } catch (requestError) {
            setError(requestError.message || 'Authentication failed.');
        } finally {
            setBusy(false);
        }
    };

    if (!configured) {
        return (
            <div style={{ color: '#f5c451', fontSize: '13px' }}>
                Authentication is not configured for this environment.
            </div>
        );
    }

    if (session) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: '#d8e6d9', fontSize: '13px' }}>
                <span title={session.username}>Signed in as {session.username}</span>
                {onOpenHistory && (
                    <button onClick={onOpenHistory} style={historyButtonStyle} title="Open job history">
                        <svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
                            <path d="M13 3a9 9 0 1 0 8.94 10H20a7 7 0 1 1-2.05-4.95L15 11h6V5l-1.63 1.63A8.96 8.96 0 0 0 13 3Zm-1 5v5l4.25 2.52 1-1.64L14 12V8h-2Z" />
                        </svg>
                        History
                    </button>
                )}
                <button onClick={onSignOut} style={buttonStyle}>Sign out</button>
            </div>
        );
    }

    return (
        <form onSubmit={submit} style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <input
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="Email"
                style={inputStyle}
            />
            {mode !== 'confirm' && (
                <input
                    type="password"
                    required
                    minLength="12"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="Password"
                    style={inputStyle}
                />
            )}
            {mode === 'confirm' && (
                <input
                    type="text"
                    required
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    placeholder="Verification code"
                    style={inputStyle}
                />
            )}
            <button disabled={busy} type="submit" style={buttonStyle}>
                {busy ? 'Please wait…' : mode === 'sign-in' ? 'Sign in' : mode === 'sign-up' ? 'Create account' : 'Verify'}
            </button>
            {mode === 'sign-in' && <button type="button" onClick={() => setMode('sign-up')} style={linkStyle}>Create account</button>}
            {mode !== 'sign-in' && <button type="button" onClick={() => setMode('sign-in')} style={linkStyle}>Back to sign in</button>}
            {(error || message) && <span style={{ color: error ? '#ff8b8b' : '#9fd8a1', fontSize: '12px' }}>{error || message}</span>}
        </form>
    );
}

const inputStyle = {
    padding: '7px 9px', borderRadius: '4px', border: '1px solid #555', background: '#171717', color: '#fff', fontSize: '12px',
};

const buttonStyle = {
    padding: '7px 10px', borderRadius: '4px', border: '1px solid #4CAF50', background: '#326d37', color: '#fff', cursor: 'pointer', fontSize: '12px',
};

const historyButtonStyle = {
    padding: '7px 10px', borderRadius: '4px', border: '1px solid #5a6b80', background: '#374452', color: '#fff',
    cursor: 'pointer', fontSize: '12px', display: 'inline-flex', alignItems: 'center', gap: '5px',
};

const linkStyle = {
    padding: 0, border: 0, background: 'transparent', color: '#9ac6ff', cursor: 'pointer', fontSize: '12px', textDecoration: 'underline',
};
