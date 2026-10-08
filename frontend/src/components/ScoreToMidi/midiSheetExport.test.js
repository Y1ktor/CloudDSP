import assert from 'node:assert/strict';
import test from 'node:test';
import * as ToneMidi from '@tonejs/midi';
const Midi = ToneMidi.Midi || ToneMidi.default?.Midi;
import { exportMidiForSheet } from './midiSheetExport.js';
import { uploadSheetMidi } from './sheetUpload.js';

test('queue export contains edited notes, tempo and meter, preserving ticks and source', async () => {
    const midi = new Midi();
    midi.header.setTempo(120);
    const track = midi.addTrack();
    track.instrument.number = 40;
    track.addNote({ midi: 60, ticks: 480, durationTicks: 240, velocity: 0.8 });
    track.addCC({ number: 64, ticks: 120, value: 1 });
    // Simulate a piano-roll pitch/duration edit on the active Tone instance.
    track.notes[0].midi = 66;
    track.notes[0].durationTicks = 960;
    const before = midi.toArray();
    const file = exportMidiForSheet(midi, 90, '6/8', 'edited.midi');
    const output = new Midi(await file.arrayBuffer());
    assert.equal(file.name, 'edited.mid');
    assert.ok(Math.abs(output.header.tempos[0].bpm - 90) < 0.001);
    assert.deepEqual(output.header.timeSignatures[0].timeSignature, [6, 8]);
    assert.equal(output.tracks[0].notes[0].midi, 66);
    assert.equal(output.tracks[0].notes[0].ticks, 480);
    assert.equal(output.tracks[0].notes[0].durationTicks, 960);
    assert.equal(output.tracks[0].instrument.number, 40);
    assert.equal(output.tracks[0].controlChanges[64][0].ticks, 120);
    assert.deepEqual(midi.toArray(), before);
    const calls = [];
    // Local export itself made no network calls. Only queue upload calls API
    // then MinIO, and the transferred bytes equal the exported edited score.
    await uploadSheetMidi({ file, authenticatedFetch: async (path, options) => {
        calls.push(path);
        assert.equal(JSON.parse(options.body).direction, 'midi_to_sheet');
        return { ok: true, status: 201, json: async () => ({ job_id: 'test', direction: 'midi_to_sheet',
            upload_url: 'https://storage.test', upload_fields: { key: 'source.mid' }, max_source_bytes: 10485760 }) };
    }, uploadFetch: async (_url, options) => {
        calls.push('storage');
        assert.deepEqual(await options.body.get('file').arrayBuffer(), await file.arrayBuffer());
        return { ok: true };
    } });
    assert.deepEqual(calls, ['/sheet-jobs', 'storage']);
});

test('empty score and unsupported tempo/meter cannot be exported', () => {
    assert.throws(() => exportMidiForSheet(new Midi(), 120, '4/4'), /Add notes/);
    const midi = new Midi(); midi.addTrack().addNote({ midi: 60, duration: 1 });
    assert.throws(() => exportMidiForSheet(midi, 0, '4/4'), /valid BPM/);
    assert.throws(() => exportMidiForSheet(midi, 120, '4/3'), /valid BPM/);
});
