/** Verifies deployment-specific CSP origins and rejection of unsafe endpoint settings. */
import assert from 'node:assert/strict';
import test from 'node:test';
import { contentSecurityPolicy } from '../csp.js';

const endpoints = {
    jobApiUrl: 'https://api.example.com/jobs',
    webSocketUrl: 'wss://live.example.com/ws',
    userPoolId: 'us-east-1_example',
    objectStorageUrl: 'http://minio.localhost:8080',
    oidcIssuer: 'http://keycloak.localhost:8080/realms/clouddsp',
};

test('cloud policy retains cloud integrations and excludes configured local services', () => {
    const policy = contentSecurityPolicy({ ...endpoints, profile: 'cloud' });
    for (const origin of ['https://api.example.com', 'wss://live.example.com',
        'https://cognito-idp.us-east-1.amazonaws.com', 'https://*.s3.amazonaws.com',
        'https://gleitz.github.io', 'https://smpldsnds.github.io']) {
        assert.ok(policy.includes(origin));
    }
    assert.ok(!policy.includes('minio.localhost'));
    assert.ok(!policy.includes('keycloak.localhost'));
    assert.ok(!policy.includes('unsafe-eval'));
    assert.ok(policy.includes("script-src 'self'"));
});

test('local policy permits exact MinIO/Keycloak/API/WebSocket origins without cloud hosts', () => {
    const policy = contentSecurityPolicy({
        ...endpoints, profile: 'local',
        jobApiUrl: 'http://clouddsp.localhost:8080/api',
        webSocketUrl: 'ws://clouddsp.localhost:8080/ws',
    });
    for (const origin of ['http://clouddsp.localhost:8080', 'ws://clouddsp.localhost:8080',
        'http://minio.localhost:8080', 'http://keycloak.localhost:8080']) {
        assert.ok(policy.includes(origin));
    }
    for (const excluded of ['s3.amazonaws.com', 'cognito-idp', 'gleitz.github.io',
        'smpldsnds.github.io', 'http://*', 'ws://*']) {
        assert.ok(!policy.includes(excluded));
    }
});

test('cloud policy rejects insecure endpoint schemes and URL credentials', () => {
    const policy = contentSecurityPolicy({
        profile: 'cloud', jobApiUrl: 'http://api.example.com',
        webSocketUrl: 'ws://live.example.com',
    });
    assert.ok(!policy.includes('api.example.com'));
    assert.ok(!policy.includes('live.example.com'));
    const invalid = contentSecurityPolicy({ profile: 'cloud', jobApiUrl: 'https://user:invalid@example.com' });
    assert.ok(!invalid.includes('example.com'));
    assert.throws(() => contentSecurityPolicy({ profile: 'unknown' }));
});
