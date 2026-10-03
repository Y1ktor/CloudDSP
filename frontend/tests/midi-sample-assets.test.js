import assert from 'node:assert/strict';
import test from 'node:test';
import { localPianoStorage, midiSampleUrl, soundfontFormat } from '../src/utils/midiSampleAssets.js';
import * as cloud from '../src/platform/cloud/samples.js';
import * as local from '../src/platform/local/samples.js';

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

test('sample providers preserve banks and bounded piano notes without allocating instruments', () => {
    assert.deepEqual(cloud.soundfontOptions('acoustic_bass'), { instrument: 'acoustic_bass', kit: 'FluidR3_GM' });
    const notes = [48, 60, 72];
    assert.deepEqual(cloud.pianoOptions(notes), { notesToLoad: { notes, velocityRange: [0, 127] } });
    assert.equal(cloud.pianoOptions([]), undefined);
    const localOptions = local.pianoOptions(notes, 'http://minio.localhost:8080');
    assert.deepEqual(localOptions.notesToLoad, { notes, velocityRange: [0, 127] });
    assert.equal(localOptions.baseUrl, 'http://minio.localhost:8080/clouddsp-midi-samples/piano');
    assert.equal(localOptions.storage, localPianoStorage);
    assert.ok(!('notesToLoad' in local.pianoOptions([], 'http://minio.localhost:8080')));
    const cloudDrums = cloud.drumSampleBuffers();
    const localDrums = local.drumSampleBuffers('http://minio.localhost:8080');
    assert.deepEqual(Object.keys(localDrums), Object.keys(cloudDrums));
    for (const sample of Object.values(localDrums)) {
        assert.match(sample, /^http:\/\/minio\.localhost:8080\/clouddsp-midi-samples\/drums\//);
        assert.ok(!sample.includes('github.io'));
    }
});
