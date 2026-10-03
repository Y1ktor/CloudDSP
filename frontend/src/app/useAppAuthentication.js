import { useCallback, useEffect } from 'react';
import { auth } from '@platform/auth';
import { profile } from '@platform/profile';
import { JOB_API_URL } from './config';

const PENDING_SIGN_UP_STORAGE_KEY = 'clouddsp.pendingSignUp';

export function readPendingSignUp() {
    try {
        const value = window.localStorage.getItem(PENDING_SIGN_UP_STORAGE_KEY);
        if (!value) return null;
        const pending = JSON.parse(value);
        return typeof pending?.email === 'string' && pending.email
            ? { email: pending.email, displayName: pending.displayName || '' }
            : null;
    } catch {
        return null;
    }
}

/** Provider session actions; the controller owns their shared UI state. */
export function useAppAuthentication({
    setAuthSession, setAuthLoading, setPendingSignUp,
    setStatusMessage, setErrorMsg, setAccountQuota,
    setIsPreviousJobsOpen, setIsRestoringHistoryJob, setIsHistoryJob,
}) {
    const restoreSession = useCallback(async () => {
        try {
            const session = await auth.restoreSession(setStatusMessage);
            setAuthSession(session);
            return session;
        } catch (error) {
            console.warn(profile.messages.sessionRestoreFailed, error);
            setAuthSession(null);
            if (profile.messages.sessionRestoreError) {
                setErrorMsg(error.message || profile.messages.sessionRestoreError);
            }
            return null;
        } finally {
            setAuthLoading(false);
        }
    }, [setAuthSession, setAuthLoading, setStatusMessage, setErrorMsg]);

    useEffect(() => {
        restoreSession();
    }, [restoreSession]);

    const authenticatedFetch = useCallback(async (path, options = {}) => {
        if (!JOB_API_URL) throw new Error('VITE_JOB_API_URL is not configured.');
        const session = await auth.getCurrentSession();
        if (!session) throw new Error('Your session has expired. Sign in again to continue.');
        const response = await fetch(`${JOB_API_URL}${path}`, {
            ...options,
            headers: {
                Authorization: `Bearer ${auth.getBearerToken(session)}`,
                ...(options.headers || {}),
            },
        });
        return response;
    }, []);

    const handleSignIn = async (email, password) => {
        setErrorMsg('');
        if (profile.messages.signInStarting) setStatusMessage(profile.messages.signInStarting);
        const session = await auth.signIn(email, password);
        if (session) {
            setAuthSession(session);
            setStatusMessage(profile.messages.signedIn);
        }
    };

    const savePendingSignUp = useCallback((pending) => {
        setPendingSignUp(pending);
        try {
            if (pending) {
                window.localStorage.setItem(PENDING_SIGN_UP_STORAGE_KEY, JSON.stringify(pending));
            } else {
                window.localStorage.removeItem(PENDING_SIGN_UP_STORAGE_KEY);
            }
        } catch (error) {
            // The dialog remains usable if private browsing prevents persistent
            // storage; it will simply not survive a page reload.
            console.warn('Could not persist pending CloudDSP email verification:', error);
        }
    }, [setPendingSignUp]);

    const handleSignUp = async (email, password, displayName) => {
        const result = await auth.signUp(email, password, displayName);
        if (!result.confirmed) {
            savePendingSignUp({ email: email.trim(), displayName: displayName.trim() });
        }
        return result;
    };

    const handleConfirmSignUp = async (email, code) => {
        const result = await auth.confirmSignUp(email, code);
        savePendingSignUp(null);
        return result;
    };

    const handleSignOut = () => {
        setAuthSession(null);
        setAccountQuota(null);
        setIsPreviousJobsOpen(false);
        setIsRestoringHistoryJob(false);
        setIsHistoryJob(false);
        setErrorMsg('');
        setStatusMessage('Signed out.');
        // Clear workspace state before a Keycloak logout navigates away. Both
        // providers expose one promise boundary for failure reporting.
        void Promise.resolve().then(() => auth.signOut()).catch((error) => {
            console.warn('Could not complete CloudDSP sign-out:', error);
            setErrorMsg(error.message || auth.signOutErrorMessage);
        });
    };

    return {
        authenticatedFetch,
        handleSignIn,
        handleSignUp,
        handleConfirmSignUp,
        handleSignOut,
    };
}
