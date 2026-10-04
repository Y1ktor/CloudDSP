/**
 * Shared account presentation with a build-selected guest sign-in flow.
 * Cognito owns the cloud dialog; Keycloak owns the local redirect screens.
 * Account history and quotas use this same UI after either provider signs in.
 */
import React from 'react';
import SignInPanel from '@platform/SignInPanel';
import AccountMenu from './AccountMenu';

export default function AuthPanel(props) {
    if (!props.configured) {
        return (
            <div style={{ color: 'var(--studio-warning)', fontSize: '13px', fontWeight: '600' }}>
                Authentication is not configured for this environment.
            </div>
        );
    }
    if (props.session) return <AccountMenu {...props} />;
    return <SignInPanel {...props} />;
}
