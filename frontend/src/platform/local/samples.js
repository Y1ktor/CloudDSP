/**
 * Provide piano, soundfont, and drum sampler options for the local profile,
 * pointing shared MIDI playback at its mirrored read-only MinIO sample bank.
 */
import { localPianoStorage, midiSampleUrl, soundfontSampleUrl } from '../../utils/midiSampleAssets.js';

// Bootstrap mirrors the same reviewed banks into a dedicated read-only MinIO
// bucket. Browser playback then works without reaching the original hosts.
// Only option construction changes; shared MIDI loading remains lazy/bounded.
export function soundfontOptions(instrument) {
    return { instrumentUrl: soundfontSampleUrl(instrument) };
}

export function pianoOptions(notes, origin) {
    const options = {
        baseUrl: midiSampleUrl('piano', origin),
        storage: localPianoStorage,
    };
    if (notes.length > 0) {
        options.notesToLoad = { notes, velocityRange: [0, 127] };
    }
    return options;
}

export function drumSampleBuffers(origin) {
    return {
        kick: midiSampleUrl('drums/kick.m4a', origin),
        snare: midiSampleUrl('drums/snare-1.m4a', origin),
        'mid-tom': midiSampleUrl('drums/tom-mid.m4a', origin),
        'hihat-close': midiSampleUrl('drums/hhclosed-1.m4a', origin),
        cymbal: midiSampleUrl('drums/crash.m4a', origin),
    };
}
