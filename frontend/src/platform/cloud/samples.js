// Keep the reviewed compact cloud kit and FluidR3 banks. This module supplies
// options only: the MIDI manager still decides when to allocate each sampler.
export function soundfontOptions(instrument) {
    return { instrument, kit: 'FluidR3_GM' };
}

export function pianoOptions(notes) {
    return notes.length > 0 ? {
        notesToLoad: { notes, velocityRange: [0, 127] },
    } : undefined;
}

export function drumSampleBuffers() {
    const baseUrl = 'https://smpldsnds.github.io/drum-machines/808-mini';
    return {
        kick: `${baseUrl}/kick.m4a`,
        snare: `${baseUrl}/snare-1.m4a`,
        'mid-tom': `${baseUrl}/tom-mid.m4a`,
        'hihat-close': `${baseUrl}/hhclosed-1.m4a`,
        cymbal: `${baseUrl}/crash.m4a`,
    };
}
