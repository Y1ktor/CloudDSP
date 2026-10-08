/** Export the editable score, preserving musical tick positions and all tracks. */
import * as ToneMidi from '@tonejs/midi';
const Midi = ToneMidi.Midi || ToneMidi.default?.Midi;

export function exportMidiForSheet(midi, bpm, meter, filename = 'score.mid') {
    if (!midi?.tracks?.some((track) => track.notes.length)) throw new Error('Add notes before queuing a sheet.');
    const tempo = Number(bpm);
    const [numerator, denominator] = String(meter).split('/').map(Number);
    if (!Number.isFinite(tempo) || tempo < 20 || tempo > 300
        || !Number.isInteger(numerator) || numerator < 1 || numerator > 32
        || ![1, 2, 4, 8, 16, 32].includes(denominator)) throw new Error('Choose a valid BPM and time signature.');
    // Clone before changing header metadata. Notes/CCs use ticks: changing BPM
    // changes playback speed without moving notes to different musical beats.
    // Export all notes, regardless of the viewport, cycle, mute, or solo state.
    const exported = new Midi(midi.toArray());
    exported.header.tempos = [{ ticks: 0, bpm: tempo }];
    exported.header.timeSignatures = [{ ticks: 0, timeSignature: [numerator, denominator] }];
    exported.header.update();
    return new File([exported.toArray()], `${filename.replace(/\.[^.]+$/, '')}.mid`, { type: 'audio/midi' });
}
