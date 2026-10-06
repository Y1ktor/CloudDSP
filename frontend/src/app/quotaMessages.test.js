/** Verify quota reset wording and error messages across limit and fallback cases. */
import assert from 'node:assert/strict';
import test from 'node:test';
import { quotaErrorMessage, quotaResetMessage } from './quotaMessages.js';

const NOW = Date.UTC(2026, 9, 3, 12);

test('quota reset text handles invalid, imminent, and rounded-up hour boundaries', () => {
    assert.equal(quotaResetMessage('invalid', NOW), 'Resets at midnight UTC.');
    for (const remaining of [-1, 0, 59_999]) {
        assert.equal(quotaResetMessage(NOW + remaining, NOW), 'Resets shortly.');
    }
    for (const remaining of [60_000, 3_600_000]) {
        assert.equal(quotaResetMessage(NOW + remaining, NOW), 'Resets in 1 hour.');
    }
    assert.equal(quotaResetMessage(new Date(NOW + 3_600_001).toISOString(), NOW), 'Resets in 2 hours.');
});

test('quota HTTP 429 errors include the correct submission label, limits, and reset time', (t) => {
    t.mock.method(Date, 'now', () => NOW);
    const payload = { statusCode: '429', quota: {
        direct_uploads: { used: 5, limit: 5 },
        ytdlp: { used: 3, limit: 3 },
        resets_at: new Date(NOW + 3_600_000).toISOString(),
    } };
    assert.equal(quotaErrorMessage(payload, 'direct_uploads', 'fallback'), 'Daily local-upload quota reached (5/5). Resets in 1 hour.');
    assert.equal(quotaErrorMessage(payload, 'ytdlp', 'fallback'), 'Daily URL-import quota reached (3/3). Resets in 1 hour.');
});

test('non-quota errors preserve backend text or the supplied fallback', () => {
    assert.equal(quotaErrorMessage({ statusCode: 500, error: 'Service unavailable', quota: { direct_uploads: {} } }, 'direct_uploads', 'fallback'), 'Service unavailable');
    assert.equal(quotaErrorMessage({ statusCode: 429, error: 'Other throttle' }, 'direct_uploads', 'fallback'), 'Other throttle');
    assert.equal(quotaErrorMessage({ statusCode: 429, quota: {} }, 'direct_uploads', 'fallback'), 'fallback');
    assert.equal(quotaErrorMessage(null, 'direct_uploads', 'fallback'), 'fallback');
});
