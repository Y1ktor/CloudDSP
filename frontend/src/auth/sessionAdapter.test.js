import assert from 'node:assert/strict';
import test from 'node:test';
import { createOidcSessionRestore, createSessionAdapter } from './sessionAdapter.js';

test('cloud and local adapters select their existing resource-server token contracts', () => {
    const session = { idToken: 'identity-assertion', accessToken: 'api-access-credential' };
    assert.equal(createSessionAdapter({ tokenField: 'idToken' }).getBearerToken(session), session.idToken);
    assert.equal(createSessionAdapter({ tokenField: 'accessToken' }).getBearerToken(session), session.accessToken);
    assert.throws(() => createSessionAdapter({ tokenField: 'accessToken' }).getBearerToken({ idToken: 'identity-only' }), /session has expired/);
    assert.throws(() => createSessionAdapter({ tokenField: 'idToken' }).getBearerToken(null), /session has expired/);
    assert.throws(() => createSessionAdapter({ tokenField: 'unreviewedToken' }), /token contract/);
});

function oidcFixture(callback, postAction = false) {
    const calls = [];
    const restartRequired = Symbol('restart');
    const storedSession = { accessToken: 'stored-session' };
    const restore = createOidcSessionRestore({
        completeSignInFromCallback: async () => { calls.push('callback'); return callback === 'restart' ? restartRequired : callback; },
        restartRequired,
        consumePostActionRedirect: () => { calls.push('post-action'); return postAction; },
        beginSignIn: async () => { calls.push('begin-sign-in'); },
        getCurrentSession: async () => { calls.push('stored-session'); return storedSession; },
    });
    return { calls, storedSession, restore };
}

test('a validated callback session wins over cached session restoration', async () => {
    const callbackSession = { accessToken: 'validated-callback-session' };
    const fixture = oidcFixture(callbackSession);
    assert.equal(await fixture.restore(), callbackSession);
    assert.deepEqual(fixture.calls, ['callback']);
});

test('ordinary local startup restores the session only after checking redirects', async () => {
    const fixture = oidcFixture(null);
    assert.equal(await fixture.restore(), fixture.storedSession);
    assert.deepEqual(fixture.calls, ['callback', 'post-action', 'stored-session']);
});

test('cross-tab email verification starts fresh PKCE without accepting the returned code', async () => {
    const fixture = oidcFixture('restart');
    const messages = [];
    assert.equal(await fixture.restore((message) => messages.push(message)), null);
    assert.deepEqual(fixture.calls, ['callback', 'begin-sign-in']);
    assert.deepEqual(messages, ['Email verified. Completing sign-in…']);
});

test('Keycloak required-action return restarts sign-in without restoring stale credentials', async () => {
    const fixture = oidcFixture(null, true);
    assert.equal(await fixture.restore(), null);
    assert.deepEqual(fixture.calls, ['callback', 'post-action', 'begin-sign-in']);
});

test('callback rejection propagates without falling back to cached credentials', async () => {
    let readCached = false;
    const restore = createOidcSessionRestore({
        completeSignInFromCallback: async () => { throw new Error('Callback validation failed'); },
        getCurrentSession: async () => { readCached = true; },
    });
    await assert.rejects(restore(), /Callback validation failed/);
    assert.equal(readCached, false);
});
