import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { profile as cloud } from '../src/platform/cloud/profile.js';
import { profile as local } from '../src/platform/local/profile.js';
import { profileForMode, publicEnvironmentForMode } from '../vite.config.js';

test('profiles preserve existing auth identities, tutorial preferences, and provider wording', () => {
    assert.equal(cloud.authKind, 'cognito');
    assert.equal(local.authKind, 'oidc');
    assert.equal(cloud.requiresWebSocket, true);
    assert.equal(local.requiresWebSocket, false);
    assert.equal(cloud.tutorialStorageKey, 'clouddsp.welcomeTutorial.seen.v1');
    assert.equal(local.tutorialStorageKey, 'clouddsp.local.welcomeTutorial.seen.v1');
    assert.deepEqual(Object.keys(cloud.messages), Object.keys(local.messages));
    assert.match(cloud.messages.uploadPending, /AWS Batch/);
    assert.ok(!local.messages.uploadPending.includes('AWS'));
    assert.ok(cloud.messages.configurationWarning.includes('VITE_WEBSOCKET_URL'));
    assert.ok(!local.messages.configurationWarning.includes('VITE_WEBSOCKET_URL'));
    assert.match(cloud.messages.uploadFailed(413), /S3 upload failed \(413\)/);
    assert.match(local.messages.uploadFailed(413), /Audio upload failed \(413\)/);
    assert.equal(profileForMode('cloud'), 'cloud');
    assert.equal(profileForMode('k8'), 'local');
    assert.throws(() => profileForMode('local'));
    assert.throws(() => profileForMode('production'));
});

test('environment selection isolates profiles and ignores generic legacy files', () => {
    const directory = mkdtempSync(join(tmpdir(), 'clouddsp-profile-test-'));
    try {
        writeFileSync(join(directory, '.env'), 'VITE_JOB_API_URL=https://legacy.example.com\n');
        writeFileSync(join(directory, '.env.production'), 'VITE_JOB_API_URL=https://legacy-production.example.com\n');
        writeFileSync(join(directory, '.env.cloud'), 'VITE_JOB_API_URL=https://base.example.com\n');
        writeFileSync(join(directory, '.env.cloud.local'), 'VITE_JOB_API_URL=https://cloud.example.com\nVITE_COGNITO_USER_POOL_ID=us-east-1_example\nVITE_OIDC_ISSUER=https://should-be-ignored.example.com\nPRIVATE_TEST_VALUE=not-a-real-secret\n');
        writeFileSync(join(directory, '.env.k8.local'), 'VITE_JOB_API_URL=http://clouddsp.localhost:8080\nVITE_OIDC_ISSUER=http://keycloak.localhost:8080/realms/clouddsp\nVITE_COGNITO_USER_POOL_ID=should-be-ignored\n');
        const cloudEnvironment = publicEnvironmentForMode('cloud', directory, {});
        assert.equal(cloudEnvironment.VITE_JOB_API_URL, 'https://cloud.example.com');
        assert.equal(cloudEnvironment.VITE_COGNITO_USER_POOL_ID, 'us-east-1_example');
        assert.ok(!('VITE_OIDC_ISSUER' in cloudEnvironment));
        assert.ok(!('PRIVATE_TEST_VALUE' in cloudEnvironment));
        const localEnvironment = publicEnvironmentForMode('k8', directory, {});
        assert.equal(localEnvironment.VITE_JOB_API_URL, 'http://clouddsp.localhost:8080');
        assert.ok(!('VITE_COGNITO_USER_POOL_ID' in localEnvironment));
        assert.equal(publicEnvironmentForMode('k8', directory, {
            VITE_JOB_API_URL: 'http://override.localhost:8080/api',
        }).VITE_JOB_API_URL, 'http://override.localhost:8080/api');
    } finally {
        rmSync(directory, { recursive: true, force: true });
    }
});
