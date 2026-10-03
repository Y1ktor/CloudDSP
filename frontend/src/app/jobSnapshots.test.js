import assert from 'node:assert/strict';
import test from 'node:test';
import {
    hasTerminalMidiArtifacts,
    isJobPending,
    messageForJob,
    needsJobRefresh,
    preserveReadyArtifactUrls,
    presignedUrlIsUsable,
    readyArtifactNames,
    sourceUrlForJob,
    urlsForReadyArtifacts,
} from './jobSnapshots.js';

const NOW = Date.UTC(2026, 9, 3, 12);
const legacyUrl = (secondsLeft, path = 'source.wav') => `https://storage.test/${path}?Expires=${NOW / 1_000 + secondsLeft}`;
const signedUrl = (secondsLeft, path = 'source.wav') => `https://storage.test/${path}?X-Amz-Date=20261003T120000Z&X-Amz-Expires=${secondsLeft}`;

function freezeSnapshot(snapshot) {
    for (const collection of [snapshot.stems, snapshot.midi]) {
        for (const artifact of Object.values(collection || {})) Object.freeze(artifact);
        if (collection) Object.freeze(collection);
    }
    return Object.freeze(snapshot);
}

test('job refresh waits for every stem to have terminal MIDI and a successful-result URL', () => {
    const completed = { status: 'completed', stems: { piano: {}, drums: {} }, midi: {} };
    assert.equal(hasTerminalMidiArtifacts(completed), false);
    assert.equal(needsJobRefresh(completed), true);
    assert.equal(hasTerminalMidiArtifacts({ ...completed, midi: { piano: { status: 'ready', url: 'piano.mid' } } }), false);
    assert.equal(hasTerminalMidiArtifacts({ ...completed, midi: { piano: { status: 'ready' }, drums: { status: 'failed' } } }), false);
    const terminal = { ...completed, midi: { piano: { status: 'ready', url: 'piano.mid' }, drums: { status: 'failed' } } };
    assert.equal(hasTerminalMidiArtifacts(terminal), true);
    assert.equal(needsJobRefresh(terminal), false);
    assert.equal(hasTerminalMidiArtifacts({ status: 'completed', stems: {}, midi: {} }), false);
    assert.equal(needsJobRefresh({ status: 'completed', stems: {}, midi: {} }), true);
});

test('failed ingestion stops polling without stems, while pending and absent jobs need refresh', () => {
    assert.equal(needsJobRefresh({ status: 'failed', error: 'Source ingestion failed.' }), false);
    assert.equal(isJobPending({ status: 'failed' }), false);
    assert.equal(isJobPending({ status: 'completed' }), false);
    for (const status of ['upload_pending', 'source_ingestion', 'stem_processing', 'midi_processing']) {
        assert.equal(isJobPending({ status }), true);
        assert.equal(needsJobRefresh({ status }), true);
    }
    assert.equal(needsJobRefresh(null), true);
    assert.equal(needsJobRefresh(undefined), true);
});

test('source URLs stay unavailable until linked ingestion has uploaded its durable input', () => {
    const original_url = 'https://storage.test/linked-audio.wav';
    assert.equal(sourceUrlForJob({ status: 'source_ingestion', source_type: 'yt-dlp', original_url }), null);
    for (const source_uploaded of [undefined, false]) {
        assert.equal(sourceUrlForJob({ status: 'failed', source_type: 'yt-dlp', source_uploaded, original_url }), null);
    }
    assert.equal(sourceUrlForJob({ status: 'failed', source_type: 'yt-dlp', source_uploaded: true, original_url }), original_url);
    assert.equal(sourceUrlForJob({ status: 'stem_processing', source_type: 'yt-dlp', source_uploaded: true, original_url }), original_url);
    assert.equal(sourceUrlForJob({ status: 'failed', source_type: 'direct', original_url }), original_url);
    assert.equal(sourceUrlForJob({ status: 'completed', original_url }), original_url);
    assert.equal(sourceUrlForJob(null), undefined);
});

test('job messages retain supplied platform labels and terminal/error messages', () => {
    const messages = { uploadPending: 'profile upload', stemProcessing: 'profile stems', midiProcessing: 'profile MIDI' };
    const expected = {
        source_ingestion: 'Downloading audio from the linked source…',
        upload_pending: messages.uploadPending,
        stem_processing: messages.stemProcessing,
        midi_processing: messages.midiProcessing,
        completed: 'Stems and MIDI extraction are complete.',
        failed: 'Processing failed. See the job status for details.',
    };
    for (const [status, message] of Object.entries(expected)) {
        assert.equal(messageForJob({ status }, 'fallback', messages), message);
    }
    assert.equal(messageForJob({ status: 'failed', error: 'Specific error' }, 'fallback', messages), 'Specific error');
    assert.equal(messageForJob({ status: 'unknown' }, 'fallback', messages), 'fallback');
    assert.equal(messageForJob(null, 'fallback', messages), 'fallback');
});

test('ready-artifact selectors exclude pending, failed, and missing-URL entries', () => {
    const artifacts = freezeSnapshot({ stems: {
        piano: { status: 'ready', url: 'piano.wav' },
        drums: { status: 'ready', url: 'drums.wav' },
        absent: { status: 'ready' },
        processing: { status: 'processing', url: 'pending.wav' },
        failed: { status: 'failed', url: 'failed.wav' },
        unknown: null,
    } }).stems;
    assert.deepEqual(urlsForReadyArtifacts(artifacts), { piano: 'piano.wav', drums: 'drums.wav' });
    assert.deepEqual(readyArtifactNames(artifacts), ['piano', 'drums']);
    assert.deepEqual(urlsForReadyArtifacts(undefined), {});
    assert.deepEqual(readyArtifactNames(null), []);
});

test('legacy and SigV4 signatures are usable only when more than sixty seconds remain', (t) => {
    t.mock.method(Date, 'now', () => NOW);
    for (const createUrl of [legacyUrl, signedUrl]) {
        assert.equal(presignedUrlIsUsable(createUrl(61)), true);
        assert.equal(presignedUrlIsUsable(createUrl(60)), false);
        assert.equal(presignedUrlIsUsable(createUrl(59)), false);
        assert.equal(presignedUrlIsUsable(createUrl(-1)), false);
    }
    assert.equal(presignedUrlIsUsable('https://storage.test/file.wav'), true);
    assert.equal(presignedUrlIsUsable('https://storage.test/file.wav?X-Amz-Date=invalid&X-Amz-Expires=3600'), false);
    assert.equal(presignedUrlIsUsable('/relative.wav'), false);
    assert.equal(presignedUrlIsUsable('invalid URL'), false);
});

test('snapshot hydration preserves usable source, stem, and MIDI URLs without mutating inputs', (t) => {
    t.mock.method(Date, 'now', () => NOW);
    const previous = freezeSnapshot({
        original_url: legacyUrl(120),
        stems: { piano: { status: 'ready', url: legacyUrl(120, 'piano.wav'), revision: 1 } },
        midi: { piano: { status: 'ready', url: signedUrl(120, 'piano.mid'), revision: 1 } },
    });
    const snapshot = freezeSnapshot({
        original_url: legacyUrl(3_600), revision: 2,
        stems: { piano: { status: 'ready', url: legacyUrl(3_600, 'piano.wav'), revision: 2 } },
        midi: { piano: { status: 'ready', url: signedUrl(3_600, 'piano.mid'), revision: 2 } },
    });
    const previousBefore = structuredClone(previous);
    const snapshotBefore = structuredClone(snapshot);
    const result = preserveReadyArtifactUrls(previous, snapshot);
    assert.notEqual(result, snapshot);
    assert.equal(result.original_url, previous.original_url);
    for (const collection of ['stems', 'midi']) {
        assert.notEqual(result[collection], snapshot[collection]);
        assert.notEqual(result[collection].piano, snapshot[collection].piano);
        assert.equal(result[collection].piano.url, previous[collection].piano.url);
        assert.equal(result[collection].piano.revision, 2);
    }
    assert.equal(result.revision, 2);
    assert.deepEqual(previous, previousBefore);
    assert.deepEqual(snapshot, snapshotBefore);
});

test('snapshot hydration accepts fresh signatures once prior URLs enter the safety window', (t) => {
    t.mock.method(Date, 'now', () => NOW);
    const snapshot = freezeSnapshot({
        original_url: signedUrl(3_600),
        stems: { piano: { status: 'ready', url: legacyUrl(3_600, 'piano.wav') } },
        midi: { piano: { status: 'ready', url: signedUrl(3_600, 'piano.mid') } },
    });
    for (const remaining of [60, 59, 0, -60]) {
        const previous = freezeSnapshot({
            original_url: signedUrl(remaining),
            stems: { piano: { status: 'ready', url: legacyUrl(remaining, 'piano.wav') } },
            midi: { piano: { status: 'ready', url: signedUrl(remaining, 'piano.mid') } },
        });
        assert.deepEqual(preserveReadyArtifactUrls(previous, snapshot), snapshot);
    }
    assert.equal(preserveReadyArtifactUrls(null, snapshot), snapshot);
});

test('snapshot hydration retains fresh artifact membership and failure state', (t) => {
    t.mock.method(Date, 'now', () => NOW);
    const previous = freezeSnapshot({ stems: {
        removed: { status: 'ready', url: legacyUrl(120, 'removed.wav') },
        failed: { status: 'ready', url: legacyUrl(120, 'failed.wav') },
        pending: { status: 'processing', url: legacyUrl(120, 'pending.wav') },
    } });
    const failed = Object.freeze({ status: 'failed', error: 'Extraction failed.' });
    const pending = Object.freeze({ status: 'ready', url: legacyUrl(3_600, 'pending.wav') });
    const snapshot = freezeSnapshot({ stems: { failed, pending } });
    const result = preserveReadyArtifactUrls(previous, snapshot);
    assert.deepEqual(Object.keys(result.stems), ['failed', 'pending']);
    assert.equal(result.stems.failed, failed);
    assert.equal(result.stems.pending, pending);
    assert.deepEqual(result.midi, {});
});
