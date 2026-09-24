import assert from 'node:assert/strict';
import test from 'node:test';
import { localPianoStorage, midiSampleUrl, soundfontFormat } from './midiSampleAssets.js';

test('local MIDI sample URLs stay inside the dedicated MinIO bucket', () => {
    assert.equal(
        midiSampleUrl('soundfonts/FluidR3_GM/acoustic_bass-ogg.js', 'http://minio.localhost:8080'),
        'http://minio.localhost:8080/clouddsp-midi-samples/soundfonts/FluidR3_GM/acoustic_bass-ogg.js',
    );
    assert.throws(() => midiSampleUrl('../clouddsp-uploads/private', 'http://minio.localhost:8080'));
    assert.throws(() => midiSampleUrl('piano', 'file:///tmp'));
});

test('soundfont fallback retains MP3 for Safari or browsers without OGG', () => {
    const supported = { canPlayType: () => 'maybe' };
    const unsupported = { canPlayType: () => '' };
    assert.equal(soundfontFormat(supported, 'Firefox'), 'ogg');
    assert.equal(soundfontFormat(supported, 'Safari'), 'mp3');
    assert.equal(soundfontFormat(unsupported, 'Firefox'), 'mp3');
});

test('piano sample storage percent-encodes sharp-note filenames', async () => {
    const previousFetch = globalThis.fetch;
    let requestedUrl;
    globalThis.fetch = async (url) => {
        requestedUrl = url;
        return { status: 200 };
    };
    try {
        await localPianoStorage.fetch('http://minio.localhost:8080/clouddsp-midi-samples/piano/PP C#1.ogg');
        assert.equal(requestedUrl, 'http://minio.localhost:8080/clouddsp-midi-samples/piano/PP%20C%231.ogg');
    } finally {
        globalThis.fetch = previousFetch;
    }
});
