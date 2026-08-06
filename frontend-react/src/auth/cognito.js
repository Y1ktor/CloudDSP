import {
    AuthenticationDetails,
    CognitoUser,
    CognitoUserAttribute,
    CognitoUserPool,
} from 'amazon-cognito-identity-js';

const userPoolId = import.meta.env.VITE_COGNITO_USER_POOL_ID;
const clientId = import.meta.env.VITE_COGNITO_BROWSER_CLIENT_ID;

export const isCognitoConfigured = Boolean(userPoolId && clientId);

function getUserPool() {
    if (!isCognitoConfigured) {
        throw new Error('Cognito is not configured. Set VITE_COGNITO_USER_POOL_ID and VITE_COGNITO_BROWSER_CLIENT_ID.');
    }
    return new CognitoUserPool({ UserPoolId: userPoolId, ClientId: clientId });
}

function sessionDetails(user, session) {
    return {
        username: user.getUsername(),
        idToken: session.getIdToken().getJwtToken(),
        expiresAt: session.getIdToken().getExpiration() * 1000,
    };
}

export function getCurrentSession() {
    if (!isCognitoConfigured) return Promise.resolve(null);
    const user = getUserPool().getCurrentUser();
    if (!user) return Promise.resolve(null);

    return new Promise((resolve, reject) => {
        user.getSession((error, session) => {
            if (error) {
                reject(error);
                return;
            }
            resolve(sessionDetails(user, session));
        });
    });
}

export function signIn(email, password) {
    const user = new CognitoUser({ Username: email.trim(), Pool: getUserPool() });
    const details = new AuthenticationDetails({ Username: email.trim(), Password: password });
    return new Promise((resolve, reject) => {
        user.authenticateUser(details, {
            onSuccess: (session) => resolve(sessionDetails(user, session)),
            onFailure: reject,
            newPasswordRequired: () => reject(new Error('A new password is required before continuing.')),
        });
    });
}

export function signUp(email, password) {
    return new Promise((resolve, reject) => {
        getUserPool().signUp(
            email.trim(),
            password,
            [new CognitoUserAttribute({ Name: 'email', Value: email.trim() })],
            [],
            (error, result) => {
                if (error) {
                    reject(error);
                    return;
                }
                resolve({ username: result.user.getUsername(), confirmed: result.userConfirmed });
            },
        );
    });
}

export function confirmSignUp(email, code) {
    const user = new CognitoUser({ Username: email.trim(), Pool: getUserPool() });
    return new Promise((resolve, reject) => {
        user.confirmRegistration(code.trim(), true, (error, result) => {
            if (error) {
                reject(error);
                return;
            }
            resolve(result);
        });
    });
}

export function signOut() {
    if (!isCognitoConfigured) return;
    getUserPool().getCurrentUser()?.signOut();
}
