/**
 * ADTOF emits one General MIDI pitch for each of its five drum classes.
 *
 * Keep this mapping in one place so parsing, rendering, editing, and playback
 * all agree on what an ADTOF drum note means. The ADTOF-PyTorch post processor
 * defines the pitches in this order: [35, 38, 47, 42, 49].
 */
export const ADTOF_DRUM_VOICES = Object.freeze([
    { id: 'kick', label: 'Kick', midi: 35, sample: 'kick', color: '#e57373', velocityScale: 1.35 },
    { id: 'snare', label: 'Snare', midi: 38, sample: 'snare', color: '#ffb74d', velocityScale: 1.18 },
    { id: 'tom', label: 'Tom', midi: 47, sample: 'mid-tom', color: '#81c784', velocityScale: 0.7 },
    { id: 'hihat', label: 'Hi-hats', midi: 42, sample: 'hihat-close', color: '#64b5f6', velocityScale: 0.55 },
    { id: 'cymbal', label: 'Cymbal', midi: 49, sample: 'cymbal', color: '#ba68c8' },
]);

// smplr publishes this compact 808 kit with stable, flat sample paths. Loading
// only these five avoids downloading the full kit and its many alternate
// samples when ADTOF only emits five classes.
export const ADTOF_DRUM_SAMPLE_BUFFERS = Object.freeze({
    kick: 'https://smpldsnds.github.io/drum-machines/808-mini/kick.m4a',
    snare: 'https://smpldsnds.github.io/drum-machines/808-mini/snare-1.m4a',
    'mid-tom': 'https://smpldsnds.github.io/drum-machines/808-mini/tom-mid.m4a',
    'hihat-close': 'https://smpldsnds.github.io/drum-machines/808-mini/hhclosed-1.m4a',
    cymbal: 'https://smpldsnds.github.io/drum-machines/808-mini/crash.m4a',
});

// smplr's flat-buffer Sampler currently copies these optional values into its
// preset even when omitted, replacing its internal defaults with undefined.
// Keep every audio parameter finite to prevent a NaN detune from reaching the
// Web Audio AudioParam setter.
export const ADTOF_DRUM_SAMPLER_OPTIONS = Object.freeze({
    buffers: ADTOF_DRUM_SAMPLE_BUFFERS,
    detune: 0,
    decayTime: 0.08,
    lpfCutoffHz: 20_000,
});

const voiceByMidi = new Map(ADTOF_DRUM_VOICES.map((voice) => [voice.midi, voice]));

export function getMidiNotes(midiData) {
    return midiData?.tracks?.flatMap((track) => track.notes || []) || [];
}

export function getAdtofDrumVoice(midiNote) {
    return voiceByMidi.get(midiNote) || null;
}

export function getAdtofDrumVoiceIndex(midiNote) {
    return ADTOF_DRUM_VOICES.findIndex((voice) => voice.midi === midiNote);
}

/** Build the stable UI state key for one ADTOF drum voice. */
export function getDrumVoiceTrackId(trackName, voiceId) {
    return `${trackName}:${voiceId}`;
}

/**
 * Resolve one drum lane's MIDI audibility without changing the parent drum
 * audio stem. A solo takes precedence over individual mute states, matching
 * the normal track-console behavior.
 */
export function isDrumVoiceAudible(trackName, voice, mutedVoices = {}, soloedVoices = {}) {
    if (!voice) return false;

    const voiceTrackId = getDrumVoiceTrackId(trackName, voice.id);
    const hasSoloedVoices = Object.values(soloedVoices).some(Boolean);
    return hasSoloedVoices ? Boolean(soloedVoices[voiceTrackId]) : !mutedVoices[voiceTrackId];
}

/**
 * Validate an ADTOF percussion MIDI against its five fixed output pitches.
 * Empty MIDI is valid when an ADTOF drum stem contains no detected events;
 * the caller's extractor metadata determines whether to render its kit lanes.
 */
export function isAdtofDrumMidi(midiData) {
    const notes = getMidiNotes(midiData);
    return notes.every((note) => Boolean(getAdtofDrumVoice(note.midi)));
}

export function getAdtofDrumNotes(midiData, voiceId) {
    const voice = ADTOF_DRUM_VOICES.find((candidate) => candidate.id === voiceId);
    if (!voice) return [];
    return getMidiNotes(midiData).filter((note) => note.midi === voice.midi);
}

/** Convert a MIDI velocity (0-1) into smplr's 1-127 scale for a drum voice. */
export function getDrumPlaybackVelocity(note, voice) {
    const midiVelocity = (note.velocity !== undefined ? note.velocity : 0.8) * 127;
    const scaledVelocity = midiVelocity * (voice?.velocityScale ?? 1);
    return Math.round(Math.max(1, Math.min(127, scaledVelocity)));
}
