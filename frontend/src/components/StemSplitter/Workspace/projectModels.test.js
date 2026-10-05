/**
 * Tests source upload validation and workspace track, MIDI status, timeline,
 * and download artifact models.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { validateSourceUpload } from './sourceUpload.js';
import { getMidiStatusByTrack, getTimelineRows, getTracksToRender } from './projectTracks.js';
import { filenameFromDownloadUrl, getProjectDownloadArtifacts, projectFolderName } from './projectDownloads.js';

test('source validation accepts the supported extensions at the exact byte limit', () => {
    for (const extension of ['wav', 'mp3', 'flac', 'm4a', 'aac', 'ogg', 'opus', 'aiff', 'aif', 'webm']) {
        assert.equal(validateSourceUpload({ name: `source.${extension.toUpperCase()}`, size: 256 * 1024 * 1024 }), null);
    }
    assert.equal(validateSourceUpload({ name: 'many.dots.wav', size: 1 }), null);
});

test('source validation checks extension before size and does not round byte limits', () => {
    const extensionError = 'Choose WAV, MP3, FLAC, M4A, AAC, OGG, Opus, AIFF, or WebM audio.';
    const sizeError = 'Choose an audio file no larger than 256 MiB.';
    for (const name of ['source', 'source.wav.exe', 'source.mp4', 'source.']) {
        assert.equal(validateSourceUpload({ name, size: 0 }), extensionError);
    }
    for (const size of [0, -1, NaN, Infinity, '1024', 256 * 1024 * 1024 + 1]) {
        assert.equal(validateSourceUpload({ name: 'source.wav', size }), sizeError);
    }
});

test('track construction retains real-source priority and the first-stem legacy fallback', () => {
    const stems = Object.freeze({ vocals: 'vocals-url', drums: 'drums-url' });
    assert.deepEqual(getTracksToRender({}, 'source-url', stems), { Original: null, ...stems });
    assert.deepEqual(getTracksToRender(null, 'source-url', stems), { Original: 'source-url', ...stems });
    assert.deepEqual(getTracksToRender(null, null, stems), { Original: 'vocals-url', ...stems });
    assert.deepEqual(getTracksToRender(null, null, {}), {});
    // Preserve the existing merge contract for snapshots that explicitly name Original.
    assert.deepEqual(getTracksToRender({}, 'source-url', { Original: 'snapshot-source' }), { Original: 'snapshot-source' });
});

test('MIDI status prioritizes parsed data, failures, received URLs, and original audio', () => {
    const stems = { Original: 'original', parsed: 'a', failed: 'b', loading: 'c', waiting: 'd' };
    const statuses = getMidiStatusByTrack(
        stems,
        { parsed: 'parsed-url', failed: 'failed-url', loading: 'loading-url', absent: 'not-a-stem' },
        { parsed: { status: 'failed' }, failed: { status: 'failed' } },
        { parsed: {} },
    );
    assert.deepEqual(statuses, { Original: 'unavailable', parsed: 'ready', failed: 'failed', loading: 'loading', waiting: 'processing' });
    assert.deepEqual(getMidiStatusByTrack({ Original: 'url' }, { Original: 'midi-url' }, {}, {}), { Original: 'loading' });
    assert.deepEqual(getMidiStatusByTrack(null, {}, {}, {}), {});
});

test('timeline expansion keeps audio on the drum parent and retains canonical voice references', () => {
    const voices = Object.freeze(['kick', 'snare', 'tom', 'hihat', 'cymbal'].map((id) => Object.freeze({ id })));
    const tracks = Object.freeze({ Original: 'source', drums: 'drum-audio', vocals: 'vocal-audio' });
    const parsed = Object.freeze({ drums: Object.freeze({ isAdtofDrum: true }) });
    const rows = getTimelineRows(tracks, parsed, { drums: true }, voices);
    assert.deepEqual(rows.map((row) => row.id), ['Original', 'drums', 'drums:kick', 'drums:snare', 'drums:tom', 'drums:hihat', 'drums:cymbal', 'vocals']);
    assert.equal(rows[1].url, 'drum-audio');
    assert.equal(rows[1].hasDrumSubtracks, true);
    assert.equal(rows[1].isDrumExpanded, true);
    voices.forEach((voice, index) => {
        assert.equal(rows[index + 2].drumVoice, voice);
        assert.equal(rows[index + 2].kind, 'drum-lane');
        assert.equal(rows[index + 2].trackName, 'drums');
        assert.equal(Object.hasOwn(rows[index + 2], 'url'), false);
    });
    assert.equal(getTimelineRows(tracks, parsed, {}, voices).length, 3);
    assert.equal(getTimelineRows(tracks, { drums: { isAdtofDrum: false } }, { drums: true }, voices).length, 3);
});

test('download filenames decode URL paths and fall back for invalid or empty paths', () => {
    assert.equal(filenameFromDownloadUrl('https://storage.test/objects/my%20song.wav?signature=x#fragment', 'fallback.wav'), 'my song.wav');
    for (const url of [null, undefined, '/relative.wav', 'not a url', 'https://storage.test/', 'https://storage.test/%ZZ.wav']) {
        assert.equal(filenameFromDownloadUrl(url, 'fallback.wav'), 'fallback.wav');
    }
    assert.equal(filenameFromDownloadUrl('https://storage.test/folder/song.wav/', 'fallback.wav'), 'song.wav');
});

test('project archive folders strip one extension and preserve the existing safe-name rules', () => {
    assert.equal(projectFolderName('  song.master.wav  '), 'song.master');
    assert.equal(projectFolderName('song/<take>: "A"\u0000.wav'), 'song__take__ _A__');
    assert.equal(projectFolderName('   '), 'CloudDSP project');
    assert.equal(projectFolderName(undefined), 'CloudDSP project');
});

test('downloads prioritize the selected Blob without evaluating its source URL filename', () => {
    const file = new Blob(['audio'], { type: 'audio/wav' });
    Object.defineProperty(file, 'name', { value: 'selected.wav' });
    let sourceUrlReads = 0;
    const sourceUrl = { toString: () => { sourceUrlReads += 1; return 'https://storage.test/remote.wav'; } };
    const artifacts = getProjectDownloadArtifacts('selected', file, 'display.wav', sourceUrl, {}, {});
    assert.deepEqual(artifacts, [{ id: 'original', group: 'original', filename: 'selected.wav', file, archivePath: 'selected/selected.wav' }]);
    assert.equal(artifacts[0].file, file);
    assert.equal(sourceUrlReads, 0);
});

test('download selection preserves artifact IDs, paths, order, fallbacks, and URL identity', () => {
    const stems = Object.freeze({ vocals: 'https://storage.test/stems/vocals%20one.wav?token=one', drums: '', bass: '/legacy-bass-url' });
    const midi = Object.freeze({ drums: 'https://storage.test/midi/drums.mid?token=two', vocals: null, bass: '/legacy-midi-url' });
    const artifacts = getProjectDownloadArtifacts('project', null, 'display.wav', 'https://storage.test/source/original.aiff', stems, midi);
    assert.deepEqual(artifacts.map(({ id, archivePath }) => ({ id, archivePath })), [
        { id: 'original', archivePath: 'project/original.aiff' },
        { id: 'stem:vocals', archivePath: 'project/stems/vocals one.wav' },
        { id: 'stem:bass', archivePath: 'project/stems/bass.wav' },
        { id: 'midi:drums', archivePath: 'project/midi/drums.mid' },
        { id: 'midi:bass', archivePath: 'project/midi/bass.mid' },
    ]);
    assert.equal(artifacts[1].url, stems.vocals);
    assert.equal(artifacts[3].url, midi.drums);
    assert.deepEqual(getProjectDownloadArtifacts('project', null, '', null, null, null), []);
    assert.equal(getProjectDownloadArtifacts('project', new Blob(['audio']), '', null, {}, {})[0].filename, 'original-audio.wav');
});
